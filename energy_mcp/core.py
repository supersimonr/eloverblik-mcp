"""Independent of MCP: usable from Streamlit, CLI and tests.

Inspired by Morten Helmstedt's eloverblik-utilities (MIT); see LICENSE.
All public date ranges are Copenhagen dates, start inclusive, end exclusive.
"""
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
import json
import math
import os
import re
import sqlite3
import time
import requests

TZ = ZoneInfo('Europe/Copenhagen')
ROOT = Path(__file__).resolve().parents[1]
BASE = 'https://api.eloverblik.dk/customerapi/api/'


class EnergyError(Exception):
    """Safe to present to a user: never contains credentials or raw HTTP bodies."""


def date_range(start, end):
    try:
        a, b = date.fromisoformat(start), date.fromisoformat(end)
    except (ValueError, TypeError):
        raise EnergyError('Dates must use YYYY-MM-DD.') from None
    if not a < b or (b-a).days > 366:
        raise EnergyError('Choose a non-empty range of at most 366 days; end is exclusive.')
    return a, b


def meter_id(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d{18}', value):
        raise EnergyError('Meter ID must be an 18-digit string.')
    return value


def stamp(value):
    dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if dt.tzinfo is None:
        raise EnergyError('API returned a timestamp without timezone.')
    return dt.astimezone(timezone.utc)


def iso(dt):
    return dt.astimezone(timezone.utc).isoformat()


def parse_readings(payload, meter):
    """Keep gaps explicit, reject bad units and unsupported resolutions.

    Quality A01/A03/A04 = adjusted/estimated/measured; A02/A05 missing/incomplete.
    Return one row per true interval, never assume Actual means hourly.
    """
    rows = {}
    results = payload.get('result')
    if not isinstance(results, list) or not results:
        raise EnergyError('API returned no time-series result.')
    for result in results:
        if result.get('success') is False:
            raise EnergyError('Eloverblik could not supply this meter/period.')
        if result.get('id') and str(result['id']) != meter:
            raise EnergyError('API returned another meter.')
        document = result.get('MyEnergyData_MarketDocument')
        if not isinstance(document, dict):
            raise EnergyError('Unexpected time-series response format.')
        for series in document.get('TimeSeries', []):
            if series.get('businessType') not in ('A04', 'A64'):
                raise EnergyError('Expected consumption series (A04/A64).')
            if str(series.get('measurement_Unit.name', '')).upper() != 'KWH':
                raise EnergyError('Expected kWh readings.')
            for period in series.get('Period', []):
                seconds = {'PT15M': 900, 'PT30M': 1800, 'PT60M': 3600, 'PT1H': 3600}.get(period['resolution'])
                if seconds is None:
                    raise EnergyError('Unsupported interval resolution.')
                begin, end = stamp(period['timeInterval']['start']), stamp(period['timeInterval']['end'])
                for p in period.get('Point', []):
                    position = int(p['position'])
                    t = begin + timedelta(seconds=(position-1)*seconds)
                    if position < 1 or t + timedelta(seconds=seconds) > end:
                        raise EnergyError('Invalid interval position.')
                    quality = p.get('out_Quantity.quality')
                    value = p.get('out_Quantity.quantity')
                    if quality in ('A02', 'A05') or value is None:
                        continue
                    if quality not in ('A01', 'A03', 'A04'):
                        raise EnergyError('Unknown reading quality; refusing to treat it as measured.')
                    kwh = float(value)
                    if not math.isfinite(kwh) or kwh < 0:
                        raise EnergyError('Invalid consumption value.')
                    key = iso(t)
                    row = (meter, key, seconds, kwh, quality)
                    if key in rows and rows[key] != row:
                        raise EnergyError('Conflicting duplicate interval.')
                    rows[key] = row
    ordered = sorted(rows.values(), key=lambda r: r[1])
    for prev, curr in zip(ordered, ordered[1:]):
        if stamp(prev[1]) + timedelta(seconds=prev[2]) > stamp(curr[1]):
            raise EnergyError('Overlapping intervals.')
    return ordered


class Client:
    def __init__(self, refresh_token=None, session=None, sleep=time.sleep):
        self.refresh = refresh_token or os.environ.get('ELOVERBLIK_REFRESH_TOKEN', '')
        if not self.refresh.strip():
            raise EnergyError('Set ELOVERBLIK_REFRESH_TOKEN locally in .env first.')
        self.session = session or requests.Session()
        self.sleep = sleep
        self.access = None
        self.expires = 0

    def _request(self, endpoint, token, body=None):
        for attempt in range(4):
            try:
                response = self.session.request(
                    'GET' if body is None else 'POST', BASE + endpoint,
                    headers={'Authorization': 'Bearer ' + token.strip(), 'Accept': 'application/json'},
                    json=body, timeout=(10, 30))
            except requests.RequestException:
                raise EnergyError('Eloverblik network error. Try again later.') from None
            if response.status_code in (429, 502, 503, 504):
                if attempt < 3:
                    self.sleep(3 * (attempt + 1))
                    continue
                raise EnergyError('Eloverblik is busy or unavailable; retry later.')
            if response.status_code in (401, 403):
                raise EnergyError('Eloverblik authorization failed. Check or renew the local token.')
            if response.status_code != 200:
                raise EnergyError(f'Eloverblik HTTP {response.status_code}; request stopped.')
            try:
                return response.json()
            except ValueError:
                raise EnergyError('Eloverblik returned invalid JSON.') from None

    def request(self, endpoint, body=None):
        if not self.access or time.monotonic() >= self.expires:
            token = self._request('token', self.refresh).get('result')
            if not isinstance(token, str) or not token:
                raise EnergyError('Eloverblik returned no access token.')
            self.access, self.expires = token, time.monotonic() + 11*3600
        # Space requests conservatively below published 25 calls/minute limit.
        self.sleep(2.5)
        return self._request(endpoint, self.access, body)

    def meters(self):
        result = self.request('meteringpoints/meteringpoints').get('result')
        if not isinstance(result, list):
            raise EnergyError('Unexpected meter-list response.')
        # Do not export resident names or addresses.
        return [{'meter_id': meter_id(str(x['meteringPointId'])),
                 'type': x.get('typeOfMP')}
                for x in result]

    def timeseries(self, meter, start, end):
        body = {'meteringPoints': {'meteringPoint': [meter_id(meter)]}}
        return self.request(f'meterdata/gettimeseries/{start}/{end}/Hour', body)

    def charges(self, meter):
        return self.request('meteringpoints/meteringpoint/getcharges',
                            {'meteringPoints': {'meteringPoint': [meter_id(meter)]}})


class Store:
    def __init__(self, path=None):
        self.path = Path(path or os.environ.get('ELOVERBLIK_DB') or ROOT / 'data/energy.sqlite3').expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript('''
              CREATE TABLE IF NOT EXISTS readings (
                meter TEXT, utc TEXT, seconds INTEGER, kwh REAL, quality TEXT,
                PRIMARY KEY(meter, utc));
              CREATE TABLE IF NOT EXISTS syncs (
                meter TEXT, start TEXT, end TEXT, fetched_at TEXT, rows INTEGER);
              CREATE TABLE IF NOT EXISTS charges (
                meter TEXT PRIMARY KEY, fetched_at TEXT, payload TEXT);
            ''')
        os.chmod(self.path, 0o600)

    def connect(self):
        return sqlite3.connect(self.path, timeout=30)

    def replace(self, meter, start, end, rows):
        # Atomic replacement removes obsolete/missing values when source revises data.
        lower = iso(datetime.combine(start, datetime.min.time(), TZ))
        upper = iso(datetime.combine(end, datetime.min.time(), TZ))
        rows = [r for r in rows if lower <= r[1] < upper]
        with self.connect() as db:
            db.execute('DELETE FROM readings WHERE meter=? AND utc>=? AND utc<?', (meter, lower, upper))
            db.executemany('INSERT INTO readings VALUES (?,?,?,?,?)', rows)
            db.execute('INSERT INTO syncs VALUES (?,?,?,?,?)',
                       (meter, str(start), str(end), iso(datetime.now(timezone.utc)), len(rows)))
        return len(rows)

    def query(self, meter, start, end, group='day'):
        meter_id(meter)
        a, b = date_range(start, end)
        if group not in ('hour', 'day', 'month'):
            raise EnergyError('group must be hour, day or month.')
        lower = iso(datetime.combine(a, datetime.min.time(), TZ))
        upper = iso(datetime.combine(b, datetime.min.time(), TZ))
        with self.connect() as db:
            rows = db.execute('SELECT utc,seconds,kwh,quality FROM readings WHERE meter=? AND utc>=? AND utc<? ORDER BY utc',
                              (meter, lower, upper)).fetchall()
        buckets = {}
        covered = 0
        for utc, seconds, kwh, quality in rows:
            t = stamp(utc).astimezone(TZ)
            key = t.replace(minute=0, second=0, microsecond=0).isoformat() if group == 'hour' else t.strftime('%Y-%m-%d' if group == 'day' else '%Y-%m')
            bucket = buckets.setdefault(key, {'period': key, 'kwh': 0., 'covered_hours': 0., 'estimated_kwh': 0.})
            bucket['kwh'] += kwh
            bucket['covered_hours'] += seconds/3600
            if quality == 'A03':
                bucket['estimated_kwh'] += kwh
            covered += seconds
        expected = (stamp(upper)-stamp(lower)).total_seconds()
        return {'start': start, 'end_exclusive': end, 'timezone': 'Europe/Copenhagen',
                'total_kwh': sum(r[2] for r in rows), 'expected_hours': expected/3600,
                'covered_hours': covered/3600, 'coverage_pct': 100*covered/expected,
                'warning': 'Partial or absent data: never interpret gaps as zero.' if covered < expected else None,
                'buckets': list(buckets.values())}

    def status(self):
        with self.connect() as db:
            rows = db.execute('SELECT meter,MIN(utc),MAX(utc),COUNT(*) FROM readings GROUP BY meter').fetchall()
            syncs = db.execute('SELECT meter,MAX(fetched_at) FROM syncs GROUP BY meter').fetchall()
        fetched = dict(syncs)
        return {'meters': [{'meter_id': m, 'first_interval_utc': a, 'last_interval_start_utc': b,
                            'intervals': n, 'last_sync_utc': fetched.get(m)} for m,a,b,n in rows],
                'source': 'Eloverblik history; not real-time. Coverage is reported per query.'}


def sync(client, store, meter, start, end):
    a, b = date_range(start, end)
    meter_id(meter)
    if b > datetime.now(TZ).date():
        raise EnergyError('Sync only completed local days; end cannot be after today.')
    count = 0
    while a < b:
        stop = min(a + timedelta(days=30), b)
        rows = parse_readings(client.timeseries(meter, a.isoformat(), stop.isoformat()), meter)
        count += store.replace(meter, a, stop, rows)
        a = stop
    return {'stored_intervals': count, 'end_exclusive': end,
            'note': 'Re-run recent days to pick up delayed or corrected readings.'}
