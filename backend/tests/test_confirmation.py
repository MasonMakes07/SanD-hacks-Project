from dataclasses import replace

import httpx
import pytest
from fastapi.testclient import TestClient

from app import analysis, main, source_provider
from app.config import get_settings
from app.confirmation import approved_context
from app.models import AnalyzeRequest, InputRejectedError
from app.Translator import to_analysis_input
from tests.conftest import AUTH_HEADERS, gemini_body, make_http_client

BODY = dict(text='Original article says the orbit changed by 32 minutes.', url='https://example.org/article', mode='manual', capture_kind='selection')


async def test_extract_has_no_retrieval_and_approved_edit_is_used_verbatim(monkeypatch):
    calls = []
    async def forbidden(*args):
        raise AssertionError('Extraction must not retrieve evidence')
    monkeypatch.setattr(analysis, 'search_fact_checks', forbidden)
    monkeypatch.setattr(source_provider, 'fetch_sources', forbidden)
    original = to_analysis_input(AnalyzeRequest(**BODY))
    async with make_http_client(lambda request: httpx.Response(200, json=gemini_body({'checkable': True, 'claim': 'Proposed claim.'}))) as client:
        proposal = await analysis.prepare_claim(client, get_settings(), original)
    approved = replace(original, approved_claim='The approved wording stays exactly as written.', confirmation_token=proposal.confirmation_token)
    async def lookup(client, settings, claim):
        calls.append(claim)
        return []
    async def sources(claim):
        calls.append(claim)
        from app.models import SourceBundle
        return SourceBundle()
    monkeypatch.setattr(analysis, 'search_fact_checks', lookup)
    monkeypatch.setattr(source_provider, 'fetch_sources', sources)
    monkeypatch.setattr(analysis, '_get_claim', forbidden)
    result = await analysis.run_investigation(None, get_settings(), approved)
    assert calls == [approved.approved_claim, approved.approved_claim]
    assert result.claim == approved.approved_claim
    assert result.input_context.claim_confirmed and result.input_context.claim_edited
    await analysis.drain_background_tasks()


async def test_confirmation_is_bound_to_original_capture_and_signed(monkeypatch):
    original = to_analysis_input(AnalyzeRequest(**BODY))
    async with make_http_client(lambda request: httpx.Response(200, json=gemini_body({'checkable': True, 'claim': 'Claim.'}))) as client:
        proposal = await analysis.prepare_claim(client, get_settings(), original)
    approved = replace(original, approved_claim='Claim.', confirmation_token=proposal.confirmation_token)
    assert not approved_context(approved, get_settings().truthtrace_api_key)[1].claim_edited
    for change in [dict(text='Changed selection'), dict(url='https://other.example/'), dict(video_time_seconds=21), dict(capture_kind='article'), dict(confirmation_token=proposal.confirmation_token + 'x')]:
        with pytest.raises(InputRejectedError):
            approved_context(replace(approved, **change), get_settings().truthtrace_api_key)
    monkeypatch.setattr('app.confirmation.time.time', lambda: 10**12)
    with pytest.raises(InputRejectedError, match='expired'):
        approved_context(approved, get_settings().truthtrace_api_key)


def test_api_extract_approve_validation_and_no_silent_reextraction(monkeypatch):
    monkeypatch.setenv('USE_MOCK', 'true'); get_settings.cache_clear()
    with TestClient(main.app) as client:
        assert client.post('/api/extract', json=BODY).status_code == 401
        proposal = client.post('/api/extract', json=BODY, headers=AUTH_HEADERS).json()
        approved = {**BODY, 'approved_claim': ' My exact edited statement. ', 'confirmation_token': proposal['confirmation_token']}
        result = client.post('/api/analyze', json=approved, headers=AUTH_HEADERS)
        assert result.status_code == 200
        assert result.json()['claim'] == 'My exact edited statement.'
        assert result.json()['input_context']['claim_edited']
        for bad in ['', ' ', 'x' * 401]:
            assert client.post('/api/analyze', json={**approved, 'approved_claim': bad}, headers=AUTH_HEADERS).status_code in (400, 422)
        assert client.post('/api/analyze', json={**BODY, 'approved_claim': 'A claim'}, headers=AUTH_HEADERS).status_code == 400
        assert client.post('/api/analyze', json={**approved, 'text': 'Another capture'}, headers=AUTH_HEADERS).status_code == 400
