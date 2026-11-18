"""Run a small, free-only check against the configured OpenRouter account."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import dotenv_values
from llm import ChatError, ask
from rag import Retriever, load_passages


def main():
    key = os.getenv('OPENROUTER_API_KEY') or dotenv_values(ROOT / '.env').get('OPENROUTER_API_KEY', '')
    if not key:
        print('Set OPENROUTER_API_KEY in .env before running the live check.')
        return 1
    retriever = Retriever(load_passages())
    checks = []
    for question in ['What amenities does AIDA offer?', 'How do I list a property on Wasalt?']:
        passages = retriever.search(question)
        started = time.monotonic()
        try:
            answer, model = ask(question, passages, key)
            check = {'question': question, 'status': 'passed', 'model': model, 'answer': answer,
                     'source_urls': sorted({item.url for item in passages}),
                     'seconds': round(time.monotonic() - started, 2)}
            print(f'PASS: {question} ({model})', flush=True)
        except ChatError as error:
            check = {'question': question, 'status': 'failed', 'error': str(error)}
            print(f'FAIL: {question}: {error}', flush=True)
        checks.append(check)
    result = {'checked_at': datetime.now(timezone.utc).isoformat(), 'checks': checks}
    (ROOT / 'tests' / 'live_results.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    return 0 if all(check['status'] == 'passed' for check in checks) else 1


if __name__ == '__main__':
    raise SystemExit(main())
