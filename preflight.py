import argparse
import json
from pathlib import Path

from llm import ChatError, ask
from mongo_store import _connect, mongo_settings
from rag import Retriever, load_passages


ROOT = Path(__file__).parent


def run_checks(production=False, test_model=False):
    checks = []
    def record(name, passed, detail):
        checks.append({'check': name, 'status': 'PASS' if passed else 'FAIL', 'detail': detail})
    try:
        passages = load_passages()
        record('Knowledge files', bool(passages), f'{len(passages)} readable passages.')
    except (OSError, ValueError):
        passages = []
        record('Knowledge files', False, 'Knowledge files are missing or unreadable.')
    try:
        uri, database, auto_seed = mongo_settings(production=production)
        record('Database configuration', True, 'Hosted connection configured.' if production else 'Local configuration accepted.')
        record('Request-time imports', not (production and auto_seed),
               'Disable MONGODB_AUTO_SEED for production; import data before deployment.' if production and auto_seed else 'Production requests will not import data.' if not auto_seed else 'Seeding enabled for development.')
        collection = _connect(uri, database, False)
        collection.database.client.admin.command('ping')
        total = collection.count_documents({})
        report = ROOT / 'data' / 'wasalt-clean' / 'report.json'
        expected = json.loads(report.read_text(encoding='utf-8'))['unique_records'] if report.exists() else None
        record('MongoDB records', total > 0 and (expected is None or total == expected),
               f'{total:,} records; expected {expected:,}.' if expected is not None else f'{total:,} records.')
        indexes = list(collection.list_indexes())
        record('MongoDB indexes', len(indexes) >= 4, f'{len(indexes)} indexes found.')
        try:
            client = collection.database.client
            names = [name for name in client.list_database_names() if name not in {'admin', 'config', 'local'}]
            stats = [client[name].command('dbStats') for name in names]
            size = sum(item['dataSize'] + item['indexSize'] for item in stats)
            record('Free database storage', size < 450_000_000,
                   f'{size / 1_000_000:.1f} MB across accessible databases; 450 MB operational ceiling leaves free-tier headroom.')
        except Exception:
            checks.append({'check': 'Free database storage', 'status': 'MANUAL', 'detail': 'Check logical storage usage in the database dashboard.'})
    except Exception:
        record('MongoDB readiness', False,
               'Check the hosted URI, database credentials, network access and imported data. Connection details are not printed.')
    if test_model:
        import os
        from dotenv import dotenv_values
        values = dotenv_values(ROOT / '.env')
        names = ['OPENROUTER_API_KEY'] + [f'OPENROUTER_API_KEY_{number}' for number in range(1, 5)]
        keys = [os.getenv(name) or values.get(name) or '' for name in names]
        try:
            hits = Retriever(passages).search('What is DarGlobal?', source='DarGlobal')
            answer, model = ask('What is DarGlobal?', hits, keys)
            record('Free model response', bool(answer), f'Response received from {model}.')
        except (ChatError, ValueError):
            record('Free model response', False, 'Check configured keys and free-model availability or quotas.')
    return checks


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Check deployment readiness without importing data or deploying.')
    parser.add_argument('--production', action='store_true')
    parser.add_argument('--model', action='store_true', help='Make one free-model request with configured key failover')
    args = parser.parse_args()
    checks = run_checks(args.production, args.model)
    for check in checks:
        print(f"{check['status']}: {check['check']} - {check['detail']}")
    raise SystemExit(any(check['status'] == 'FAIL' for check in checks))
