import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock
from energy_mcp.core import Client, EnergyError, Store, TZ, iso, parse_readings, sync

METER = '123456789012345678'


def payload(start='2025-10-26T00:00:00Z', resolution='PT15M', count=4, quality='A04'):
    seconds = 900 if resolution == 'PT15M' else 3600
    end = (datetime.fromisoformat(start.replace('Z','+00:00'))+timedelta(seconds=seconds*count)).isoformat()
    return {'result': [{'id': METER, 'success': True, 'MyEnergyData_MarketDocument': {'TimeSeries': [{
        'businessType':'A04', 'measurement_Unit.name':'KWH', 'Period': [{
            'resolution':resolution, 'timeInterval':{'start':start,'end':end},
            'Point':[{'position':i+1,'out_Quantity.quantity':0.25,'out_Quantity.quality':quality} for i in range(count)]
        }]}]}}]}


class ParserTests(unittest.TestCase):
    def test_quarter_and_dst(self):
        rows = parse_readings(payload(count=8), METER)
        self.assertEqual(len(rows), 8)
        self.assertEqual(rows[0][2], 900)
        a = datetime.fromisoformat(rows[0][1]).astimezone(TZ).isoformat()
        b = datetime.fromisoformat(rows[4][1]).astimezone(TZ).isoformat()
        self.assertIn('02:00:00+02:00', a)
        self.assertIn('02:00:00+01:00', b)

    def test_missing_is_not_zero(self):
        self.assertEqual(parse_readings(payload(quality='A02'), METER), [])

    def test_estimated_flag_preserved(self):
        self.assertEqual(parse_readings(payload(quality='A03'), METER)[0][4], 'A03')

    def test_reject_units(self):
        p = payload()
        p['result'][0]['MyEnergyData_MarketDocument']['TimeSeries'][0]['measurement_Unit.name']='MWH'
        with self.assertRaises(EnergyError): parse_readings(p, METER)

    def test_reject_bad_resolution(self):
        with self.assertRaises(EnergyError): parse_readings(payload(resolution='P1D'), METER)

    def test_reject_failed_response(self):
        with self.assertRaises(EnergyError): parse_readings({'result':[{'success':False}]},METER)


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name)/'test.sqlite3')

    def tearDown(self): self.tmp.cleanup()

    def test_dst_full_days(self):
        for day, hours in [(date(2025,3,30),23), (date(2025,10,26),25)]:
            begin = datetime.combine(day,datetime.min.time(),TZ).astimezone(timezone.utc)
            rows = [(METER, iso(begin+timedelta(hours=i)),3600,1.,'A04') for i in range(hours)]
            end = day+timedelta(days=1)
            self.store.replace(METER,day,end,rows)
            result = self.store.query(METER,str(day),str(end),'hour')
            self.assertEqual(result['coverage_pct'],100)
            self.assertEqual(len(result['buckets']),hours)

    def test_idempotence_corrections_and_missing(self):
        a,b=date(2025,10,26),date(2025,10,27)
        rows=parse_readings(payload(),METER)
        self.store.replace(METER,a,b,rows)
        self.store.replace(METER,a,b,rows)
        result=self.store.query(METER,str(a),str(b))
        self.assertEqual(result['total_kwh'],1)
        self.assertEqual(result['covered_hours'],1)
        self.assertEqual(result['expected_hours'],25)
        self.assertIsNotNone(result['warning'])
        self.store.replace(METER,a,b,[])
        self.assertEqual(self.store.query(METER,str(a),str(b))['covered_hours'],0)

    def test_validation(self):
        for start,end in [('2025-01-01','2025-01-01'),('2025-02-30','2025-03-01'),('2024-01-01','2026-01-01')]:
            with self.assertRaises(EnergyError): self.store.query(METER,start,end)

    def test_sync_chunks_exclusive(self):
        client=Mock()
        client.timeseries.return_value=payload()
        sync(client,self.store,METER,'2025-09-01','2025-11-01')
        calls=[c.args[1:] for c in client.timeseries.call_args_list]
        self.assertEqual(calls,[('2025-09-01','2025-10-01'),('2025-10-01','2025-10-31'),('2025-10-31','2025-11-01')])


class HttpTests(unittest.TestCase):
    def test_403_stops_and_redacts(self):
        session=Mock()
        session.request.return_value.status_code=403
        client=Client('SECRET',session,lambda _:None)
        with self.assertRaises(EnergyError) as exc: client.meters()
        self.assertEqual(session.request.call_count,1)
        self.assertNotIn('SECRET',str(exc.exception))

    def test_retry_bounded(self):
        session=Mock()
        session.request.return_value.status_code=503
        with self.assertRaises(EnergyError): Client('SECRET',session,lambda _:None).meters()
        self.assertEqual(session.request.call_count,4)

    def test_access_token_memory_cache(self):
        token=Mock(status_code=200)
        token.json.return_value={'result':'SHORT_LIVED'}
        meters=Mock(status_code=200)
        meters.json.return_value={'result':[{'meteringPointId':METER}]}
        session=Mock()
        session.request.side_effect=[token,meters,meters]
        client=Client('SECRET',session,lambda _:None)
        self.assertEqual(client.meters()[0]['meter_id'],METER)
        client.meters()
        self.assertEqual(session.request.call_count,3)


if __name__=='__main__': unittest.main()
