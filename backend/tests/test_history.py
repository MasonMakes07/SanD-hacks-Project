import copy
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app import main, source_provider
from app.models import AnalyzeResponse, AnalysisInput, AnalysisVerdict, SourceBundle
from app import analysis
from tests.conftest import AUTH_HEADERS, REAL_SAVE_INVESTIGATION


class MemoryAnalyses:
    backend = 'neon_postgres'
    def __init__(self): self.rows = {}
    def save_analysis(self, aid, iid, claim, status, explanation, response):
        self.rows.setdefault(aid, {'analysis_id': aid, 'response': copy.deepcopy(response),
            'saved_at': '2026-11-14T21:00:00+00:00', 'storage_backend': self.backend})
    def load_analysis(self, aid): return copy.deepcopy(self.rows.get(aid))
    def list_analyses(self, limit):
        return [{'analysis_id': aid, 'claim': row['response']['claim'], 'status': row['response']['status'],
                 'checked_at': row['response'].get('checked_at'), 'storage_backend': self.backend,
                 'saved_at': row['saved_at']} for aid, row in list(self.rows.items())[-limit:]]


def legacy():
    return dict(investigation_id='same-trace', claim='A claim.', status='insufficient_evidence',
        explanation='Inconclusive.', sources=[], earliest_observed_source=None, graph_nodes=[], graph_edges=[])


@pytest.fixture
def storage(monkeypatch):
    store = MemoryAnalyses()
    monkeypatch.setattr(source_provider, '_analysis_store', lambda: store)
    return store


def test_history_is_authenticated_and_bounded(storage):
    with TestClient(main.app) as client:
        for path in ['/api/analyses', '/api/analyses/a']:
            assert client.get(path).status_code == 401
        assert client.get('/api/analyses?limit=51', headers=AUTH_HEADERS).status_code == 422
        assert client.get('/api/analyses?limit=0', headers=AUTH_HEADERS).status_code == 422
        assert client.get('/api/analyses', headers=AUTH_HEADERS).json()['items'] == []


async def test_reopen_restores_complete_video_result_without_any_upstream(storage, monkeypatch):
    def forbidden(*args, **kwargs): raise AssertionError('History must never invoke upstream work')
    for name in ['run_investigation', 'extract_claim', 'extract_video_claim', 'search_fact_checks', 'analyze_claim']:
        monkeypatch.setattr(analysis, name, forbidden)
    monkeypatch.setattr(main, 'run_investigation', forbidden)
    monkeypatch.setattr(source_provider, 'fetch_sources', forbidden)
    result = AnalyzeResponse(**legacy(), page_context={'url': 'https://youtube.com/watch?v=abcdefghijk',
        'text': 'Original title', 'mode': 'manual', 'video_time_seconds': 180, 'video_duration_seconds': 400},
        claim_source={'video_url': 'https://youtube.com/watch?v=abcdefghijk', 'timestamp': '2:58',
                      'timestamp_seconds': 178, 'timestamp_url': 'https://youtube.com/watch?v=abcdefghijk&t=178',
                      'quote': 'Detected words', 'speaker': ''}, checked_at='2026-11-14T20:00:00Z')
    await REAL_SAVE_INVESTIGATION(result)
    first_id = result.analysis_id
    second = result.model_copy(deep=True, update={'analysis_id': None})
    await REAL_SAVE_INVESTIGATION(second)
    assert second.analysis_id != first_id
    with TestClient(main.app) as client:
        items = client.get('/api/analyses', headers=AUTH_HEADERS).json()['items']
        assert len(items) == 2
        body = client.get(f'/api/analyses/{first_id}', headers=AUTH_HEADERS).json()
    assert body['analysis_id'] == first_id and body['reopened'] is True
    assert body['checked_at'] == result.checked_at
    assert body['page_context']['video_time_seconds'] == 180
    assert body['claim_source']['timestamp_seconds'] == 178
    assert body['claim_source']['quote'] == 'Detected words'


def test_legacy_missing_malformed_outage(storage):
    storage.save_analysis('legacy', 'same-trace', 'A claim.', 'insufficient_evidence', '', legacy())
    storage.rows['broken'] = {'analysis_id': 'broken', 'response': {'bad': True}}
    with TestClient(main.app) as client:
        body = client.get('/api/analyses/legacy', headers=AUTH_HEADERS).json()
        assert body['checked_at'] is None and body['page_context'] is None
        assert body['provenance']['source_trace'] is None
        assert body['provenance']['saved_analysis']['saved'] is True
        assert client.get('/api/analyses/absent', headers=AUTH_HEADERS).status_code == 404
        assert client.get('/api/analyses/broken', headers=AUTH_HEADERS).status_code == 422
        storage.load_analysis = lambda aid: (_ for _ in ()).throw(RuntimeError('outage'))
        storage.list_analyses = storage.load_analysis
        assert client.get('/api/analyses/legacy', headers=AUTH_HEADERS).status_code == 503
        assert client.get('/api/analyses', headers=AUTH_HEADERS).status_code == 503


async def test_storage_outage_does_not_destroy_current_finding(monkeypatch):
    async def fails(result): raise RuntimeError('outage')
    monkeypatch.setattr(source_provider, 'save_investigation', fails)
    result = AnalyzeResponse(**legacy())
    await analysis.complete_result(result, AnalysisInput('Original text', 'https://example.org/article', 'manual'))
    assert result.claim == 'A claim.' and result.page_context.text == 'Original text'
    assert result.analysis_id and result.provenance.saved_analysis.saved is None
