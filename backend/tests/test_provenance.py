from datetime import datetime, timezone
from source_tracing.cached_trace import CachedTrace
from source_tracing.service import empty_result
from app import source_provider
from tests.conftest import REAL_FETCH_SOURCES


async def test_source_cache_metadata_preserves_original_time(monkeypatch):
    original = datetime(2026, 1, 1, tzinfo=timezone.utc)
    trace = CachedTrace(empty_result('trace'), True, original, 'neon_postgres', True)
    async def detailed(claim, seed_url=None): return trace
    monkeypatch.setattr(source_provider, 'trace_claim_detailed', detailed)
    bundle = await REAL_FETCH_SOURCES('claim')
    assert bundle.source_trace.retrieved_at == original.isoformat()
    assert bundle.source_trace.cache_hit and bundle.source_trace.storage_backend == 'neon_postgres'
    assert bundle.source_trace.serp_requests == bundle.source_trace.page_fetches == 0


def test_mock_and_failed_retrieval_do_not_invent_retrieval_time():
    for backend, error in [('mock', None), ('none', 'unavailable')]:
        trace = CachedTrace(empty_result('trace'), False, datetime.now(timezone.utc), backend, False, error=error)
        meta = source_provider.trace_metadata(trace)
        assert meta.retrieved_at is None and meta.saved is False


async def test_originating_url_reaches_source_provider(monkeypatch):
    from app import analysis
    from app.models import SourceBundle
    seen = []
    async def sources(claim, seed_url=None):
        seen.append((claim, seed_url)); return SourceBundle()
    async def factchecks(*args): return []
    monkeypatch.setattr(source_provider, 'fetch_sources', sources)
    monkeypatch.setattr(analysis, 'search_fact_checks', factchecks)
    await analysis._retrieve_evidence(None, None, 'unchanged claim', seed_url='https://example.org/article')
    assert seen == [('unchanged claim', 'https://example.org/article')]
