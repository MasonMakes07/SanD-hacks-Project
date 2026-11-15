import copy
import pytest
from fastapi.testclient import TestClient
from app import main, reassessment, analysis, source_provider, evidence_registry, detective
from app.config import get_settings
from app.models import AnalyzeResponse, AnalysisVerdict, EvidenceQuote, Passage, Source, GraphNode, PinnedEvidenceSelection
from app.evidence_registry import issue_receipt, remember_analysis
from tests.conftest import AUTH_HEADERS


@pytest.fixture(autouse=True)
def clear_registry():
    evidence_registry._analyses.clear(); evidence_registry._receipts.clear()
    yield
    evidence_registry._analyses.clear(); evidence_registry._receipts.clear()


def source(sid, url=None, text='New measurements show a change.'):
    return Source(id=sid, title='Report', url=url or f'https://example.org/{sid}', evidence_kind='fetched_article',
        passages=[Passage(id=f'{sid}-p1', text=text)])


def original():
    s = source('s1')
    return AnalyzeResponse(analysis_id='original', investigation_id='same-trace', claim='DART changed the orbit.',
        status='insufficient_evidence', explanation='Unclear [s1].', cited_source_ids=['s1'],
        sources=[s], graph_nodes=[GraphNode(id=s.id, title=s.title, url=s.url)], graph_edges=[], earliest_observed_source=None,
        checked_at='2026-11-14T00:00:00Z', page_context={'url': 'https://example.org/article', 'text': 'Original capture'})


def receipt(claim=None, sources=None):
    rows = sources or [source('m1')]
    token = issue_receipt(claim or original().claim, rows,
                          [GraphNode(id=s.id, title=s.title, url=s.url) for s in rows], [])
    return PinnedEvidenceSelection(evidence_set_id=token, source_ids=[s.id for s in rows])


@pytest.mark.parametrize('status', ['supported', 'contradicted', 'misleading', 'insufficient_evidence'])
async def test_reassess_uses_exact_set_unchanged_claim_and_preserves_original(monkeypatch, status):
    before = original(); snapshot = before.model_dump()
    selection = receipt()
    calls = []
    async def analyze(http, settings, claim, sources):
        calls.append((claim, sources))
        return AnalysisVerdict(status, 'Measurements add context [m1].', ['m1'],
            [EvidenceQuote(source_id='m1', passage_id='m1-p1', quote='show a change.')])
    def forbidden(*a, **k): raise AssertionError('Reassessment cannot retrieve or extract')
    monkeypatch.setattr(reassessment, 'analyze_claim', analyze)
    for name in ['extract_claim', 'extract_video_claim', 'search_fact_checks', '_retrieve_evidence']:
        monkeypatch.setattr(analysis, name, forbidden)
    monkeypatch.setattr(source_provider, 'fetch_sources', forbidden)
    result = await reassessment.reassess_claim(None, get_settings(), before, [selection])
    revised = result['revised']
    assert len(calls) == 1 and calls[0][0] == before.claim
    assert [s.id for s in calls[0][1]] == ['s1', 'm1']
    assert calls[0][1][1].passages[0].text == 'New measurements show a change.'
    assert before.model_dump() == snapshot and revised.analysis_id != before.analysis_id
    assert revised.page_context == before.page_context
    assert revised.reassessment['original_result'] == before.model_dump(mode='json')
    assert revised.reassessment['added_source_ids'] == ['m1']
    assert revised.reassessment['source_retrieval_performed'] is False
    assert revised.evidence_quotes[0].wording_verified
    assert result['changed'] == (status != before.status)


def test_unknown_stale_wrong_claim_duplicate_url_and_collision():
    base = original()
    selected = receipt(sources=[source('s1', 'https://example.org/new'), source('m1', base.sources[0].url)])
    selected.source_ids.append('s1')
    sources, nodes, edges, added, mapping = reassessment.selected_evidence(base, [selected, selected])
    assert [s.id for s in sources] == ['s1', 'r1'] and added == ['r1']
    assert mapping[0]['analysis_source_id'] == 'r1' and mapping[1]['duplicate_url']
    for selection in [receipt(claim='Different claim'), PinnedEvidenceSelection(evidence_set_id='0'*32, source_ids=['m1'])]:
        with pytest.raises(reassessment.EvidenceSelectionError): reassessment.selected_evidence(base, [selection])
    selected.source_ids.append('unknown')
    with pytest.raises(reassessment.EvidenceSelectionError): reassessment.selected_evidence(base, [selected])


def test_unsafe_receipt_never_becomes_evidence():
    selected = receipt(sources=[source('m1', 'http://127.0.0.1/private')])
    with pytest.raises(reassessment.EvidenceSelectionError): reassessment.selected_evidence(original(), [selected])


def test_endpoint_auth_claim_guard_and_failure_preservation(monkeypatch):
    base = original(); remember_analysis(base)
    selection = receipt()
    payload = {'analysis_id': base.analysis_id, 'claim': base.claim, 'pinned_evidence': [selection.model_dump()]}
    async def fails(*a): raise reassessment.UpstreamError('gemini', 'unavailable')
    monkeypatch.setattr(reassessment, 'analyze_claim', fails)
    with TestClient(main.app) as client:
        assert client.post('/api/reassess', json=payload).status_code == 401
        assert client.post('/api/reassess', json={**payload, 'claim': 'Changed'}, headers=AUTH_HEADERS).status_code == 409
        assert client.post('/api/reassess', json={**payload, 'sources': []}, headers=AUTH_HEADERS).status_code == 422
        assert client.post('/api/reassess', json=payload, headers=AUTH_HEADERS).status_code == 503
    assert evidence_registry.get_analysis('original').model_dump() == base.model_dump()


async def test_invalid_new_citation_or_quote_cannot_be_verified(monkeypatch):
    async def bad(*a):
        return AnalysisVerdict('supported', 'Verified [m1].', ['m1'],
            [EvidenceQuote(source_id='m1', passage_id='m1-p1', quote='invented')])
    monkeypatch.setattr(reassessment, 'analyze_claim', bad)
    result = await reassessment.reassess_claim(None, get_settings(), original(), [receipt()])
    assert result['revised'].status == 'insufficient_evidence'
    assert result['revised'].evidence_quotes == []


def test_receipt_expiry_is_explicit(monkeypatch):
    monkeypatch.setattr(evidence_registry, 'monotonic', lambda: 1)
    selected = receipt()
    monkeypatch.setattr(evidence_registry, 'monotonic', lambda: 3602)
    with pytest.raises(reassessment.EvidenceSelectionError, match='stale'):
        reassessment.selected_evidence(original(), [selected])
