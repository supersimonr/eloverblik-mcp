"""Local setup/sync CLI. Never pass secrets as command arguments."""
import argparse
from datetime import datetime, timezone
import json
from dotenv import load_dotenv
from .core import ROOT, Client, Store, EnergyError, sync, meter_id, iso


def main():
    load_dotenv(ROOT / '.env', override=False)
    parser = argparse.ArgumentParser(description='Eloverblik local history; all end dates are EXCLUSIVE.')
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('meters')
    commands.add_parser('status')
    p = commands.add_parser('sync')
    p.add_argument('--meter', required=True)
    p.add_argument('--start', required=True)
    p.add_argument('--end', required=True)
    p = commands.add_parser('charges')
    p.add_argument('--meter', required=True)
    args = parser.parse_args()
    try:
        if args.command == 'meters':
            result = Client().meters()
        elif args.command == 'status':
            result = Store().status()
        elif args.command == 'sync':
            result = sync(Client(), Store(), args.meter, args.start, args.end)
        else:
            meter_id(args.meter)
            payload = Client().charges(args.meter)
            results = payload.get('result')
            if not isinstance(results, list) or not results or any(r.get('success') is False for r in results):
                raise EnergyError('Eloverblik could not supply charges.')
            with Store().connect() as db:
                db.execute('INSERT OR REPLACE INTO charges VALUES (?,?,?)',
                           (args.meter, iso(datetime.now(timezone.utc)), json.dumps(payload)))
            result = {'saved': True, 'note': 'Raw charge snapshot only; NOT a historical bill calculation. VAT and effective dates require validation.'}
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except EnergyError as exc:
        parser.exit(1, f'{exc}\n')


if __name__ == '__main__':
    main()
