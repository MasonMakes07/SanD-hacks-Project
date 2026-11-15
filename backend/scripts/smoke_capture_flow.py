"""Opt-in real API smoke. Spends bounded live credits, never prints credentials.

python -m scripts.smoke_capture_flow --env-root /path/to/configured/checkout
The backup files contain REAL API results, not fixtures. They are not silently
loaded by the extension. Separate article/video receipts record actual coverage.
"""
import argparse
import asyncio
import json
import logging
import os
from pathlib import Path
import time

from dotenv import load_dotenv


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--env-root', type=Path, required=True)
    parser.add_argument('--output', type=Path, default=Path('/tmp/truthtrace-capture-live'))
    args = parser.parse_args()
    load_dotenv(args.env_root / 'backend/.env', override=False)
    load_dotenv(args.env_root / 'source_tracing/.env', override=False)
    os.environ['USE_MOCK'] = 'false'
    os.environ['TRUTHTRACE_MOCK'] = '0'
    from fastapi.testclient import TestClient
    from app import main as api, analysis, source_provider
    from app.config import get_settings
    from source_tracing.store import cache_key
    settings = get_settings()
    if not all([settings.truthtrace_api_key, settings.gemini_api_key, settings.factcheck_api_key]):
        raise SystemExit('Live smoke requires configured API keys.')
    logging.getLogger('httpx').setLevel(logging.WARNING)
    args.output.mkdir(parents=True, exist_ok=True)
    cases = {
        'article': dict(text="NASA's DART impact changed Dimorphos's orbit around Didymos by 32 minutes.", url='https://www.nasa.gov/news-release/nasa-confirms-dart-mission-impact-changed-asteroids-motion-in-space/', mode='manual', capture_kind='selection'),
        'video': dict(text='Public YouTube video moment', url='https://www.youtube.com/watch?v=N-q2z4N_Rsk', mode='manual', capture_kind='video', video_time_seconds=141),
    }
    receipts = {}
    with TestClient(api.app) as client:
        for name, body in cases.items():
            calls = []
            async def record(response):
                calls.append({'host': response.request.url.host, 'status': response.status_code})
            client.app.state.http_client.event_hooks['response'] = [record]
            started = time.monotonic()
            response = client.post('/api/extract', json=body, headers={'X-TruthTrace-Key': settings.truthtrace_api_key})
            receipt = {'synthetic': False, 'extraction_http': response.status_code, 'extraction_seconds': round(time.monotonic()-started, 2)}
            proposal = response.json()
            receipt.update({key: proposal.get(key) for key in ['checkable', 'claim', 'input_context', 'claim_source']})
            print(name, 'extraction', json.dumps(receipt), flush=True)
            if response.status_code == 200 and proposal.get('checkable'):
                started = time.monotonic()
                response = client.post('/api/analyze', json={**body, 'approved_claim': proposal['claim'], 'confirmation_token': proposal['confirmation_token']}, headers={'X-TruthTrace-Key': settings.truthtrace_api_key})
                receipt.update(analysis_http=response.status_code, analysis_seconds=round(time.monotonic()-started, 2))
                result = response.json()
                if response.status_code == 200:
                    assert result['claim'] == proposal['claim'], 'Approved statement changed'
                    assert result['input_context']['claim_confirmed']
                    receipt.update(status=result['status'], sources=len(result['sources']), connections=len(result['graph_edges']), investigation_id=result['investigation_id'])
                    (args.output / f'real_{name}_result.json').write_text(json.dumps(result, indent=2))
                    async def drain():
                        await asyncio.wait_for(analysis.drain_background_tasks(), 15)
                    try:
                        client.portal.call(drain)
                        store = source_provider._analysis_store()
                        stored = store.find_cached(cache_key(result['claim']))
                        receipt['retrieval_cache_present'] = stored is not None
                        receipt['retrieval_cache_backend'] = stored.backend if stored else None
                        with store._connect() as conn:
                            count = conn.execute("SELECT count(*) FROM tt_analyses WHERE investigation_id = %s AND claim = %s", (result['investigation_id'], result['claim'])).fetchone()[0]
                        receipt['persisted_analysis_rows'] = count
                    except Exception as error:
                        receipt['persistence_verification_error'] = type(error).__name__
                else:
                    receipt['analysis_error'] = result.get('error')
            receipt['observed_upstream_responses'] = calls
            receipts[name] = receipt
            (args.output / 'receipt.json').write_text(json.dumps(receipts, indent=2))
            print(name, 'complete', json.dumps(receipt), flush=True)
    return 0 if all(r.get('analysis_http') == 200 and r.get('persisted_analysis_rows', 0) > 0 for r in receipts.values()) else 1


if __name__ == '__main__':
    raise SystemExit(main())
