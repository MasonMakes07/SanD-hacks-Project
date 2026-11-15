import asyncio
from datetime import datetime, timezone

import httpx
import pytest

from source_tracing.cached_trace import trace_with_cache
from source_tracing.models import Source
from source_tracing.pages import FetchBudget, PageFetcher
from source_tracing.pipeline import run_trace
from source_tracing.seed import fetch_seed_context
from source_tracing.serp_client import SerpError
from source_tracing.store import JsonFileStore, NullStore, cache_key
from source_tracing.urls import check_public_http_url

CLAIM = 'DART changed the asteroid orbit'
SEED = 'https://example.org/article'
DOC = 'https://example.org/report'
TEXT = 'DART changed the asteroid orbit according to new measurements.'


class Search:
    def __init__(self, urls=(), fail=False): self.urls, self.fail, self.calls = urls, fail, 0
    async def search(self, query, budget):
        budget.charge(); self.calls += 1
        if self.fail: raise SerpError('unavailable')
        return {'organic': [{'title': TEXT, 'description': TEXT, 'link': url} for url in self.urls]}


def fetcher(pages, requests):
    def handler(request):
        url = str(request.url); requests.append(url)
        body = pages.get(url)
        if isinstance(body, httpx.Response): return body
        return httpx.Response(200 if body else 404, headers={'content-type': 'text/html'}, text=body or '')
    return PageFetcher(transport=httpx.MockTransport(handler), url_check=check_public_http_url)


def run(coro): return asyncio.run(coro)


def test_seed_observed_relevant_link_and_total_budgets():
    search_urls = [f'https://search.example.org/{i}' for i in range(8)]
    pages = {SEED: f'<nav><a href="https://ignored.example/nav">DART orbit</a></nav><article><p>{TEXT} <a href="{DOC}">DART report</a></p><p>{TEXT} <a href="https://ignored.example/second">DART study</a></p></article>', DOC: f'<p>{TEXT}</p><p><a href="https://ignored.example/recursive">DART report</a></p>', **{u: f'<p>{TEXT}</p>' for u in search_urls}}
    requests = []; search = Search(search_urls)
    result = run(run_trace(CLAIM, search, fetcher(pages, requests), seed_url=SEED))
    assert result.serp_requests == 2 and result.page_fetches == 5 and len(requests) == 5
    assert requests[:2] == [SEED, DOC]
    assert not any('ignored.example' in url for url in requests)
    sources = {s.url: s for s in result.result.sources}
    seed, doc = sources[SEED], sources[DOC]
    assert seed.provenance['role'] == 'originating_article'
    assert seed.passages and doc.passages
    edge = next(e for e in result.result.graph_edges if e.source_id == seed.id and e.target_id == doc.id and e.relationship_type == 'explicit_citation')
    assert edge.evidence.hyperlink == DOC and edge.evidence.method == 'hyperlink_in_page_body'
    assert 'not agreement' in edge.evidence.note


@pytest.mark.parametrize('link', [
    '<a href="http://127.0.0.1/private">DART report</a>',
    '<a href="javascript:alert(1)">DART report</a>',
    '<a href="https://ignored.example/unrelated">Weather forecast</a>',
    '<a rel="sponsored" href="https://ignored.example/ad">DART report</a>',
    '<a href="/missing">DART report</a>',
])
def test_unsafe_unrelated_sponsored_or_unavailable_link_has_no_invented_document(link):
    requests = []
    result = run(run_trace(CLAIM, Search(), fetcher({SEED: f'<p>{TEXT} {link}</p>'}, requests), seed_url=SEED))
    assert [s.url for s in result.result.sources] == [SEED]
    assert result.result.graph_edges == []
    assert len(requests) <= 2
    assert not any('127.0.0.1' in url or 'ignored.example' in url for url in requests)


def test_matching_search_urls_are_not_refetched_and_ids_stay_stable():
    requests = []
    pages = {SEED: f'<p>{TEXT} <a href="{DOC}?utm_source=test">DART report</a></p>', DOC: f'<p>{TEXT}</p>'}
    result = run(run_trace(CLAIM, Search([SEED, DOC]), fetcher(pages, requests), seed_url=SEED))
    assert requests == [SEED, DOC]
    assert [(s.id, s.url) for s in result.result.sources] == [('s1', SEED), ('s2', DOC)]


def test_redirect_aliases_deduplicate_without_self_edges():
    alias = 'https://example.org/alias'
    requests = []
    pages = {alias: httpx.Response(302, headers={'location': SEED}), SEED: f'<p>{TEXT}</p>'}
    result = run(run_trace(CLAIM, Search([SEED]), fetcher(pages, requests), seed_url=alias))
    assert len(result.result.sources) == 1
    assert result.result.sources[0].id == 's1'
    assert result.result.sources[0].provenance['role'] == 'originating_article'
    assert result.result.graph_edges == []
    assert requests == [alias, SEED]
    assert result.page_fetches == 1


def test_different_seeds_have_different_cache_and_hits_keep_timestamp(tmp_path):
    second_seed = 'https://example.org/other'
    second_doc = 'https://example.org/other-report'
    requests = []; search = Search()
    pages = {SEED: f'<p>{TEXT} <a href="{DOC}">DART report</a></p>', DOC: f'<p>{TEXT}</p>', second_seed: f'<p>{TEXT} <a href="{second_doc}">DART report</a></p>', second_doc: f'<p>{TEXT}</p>'}
    store = JsonFileStore(tmp_path); client = fetcher(pages, requests)
    original_time = datetime(2026, 1, 1, tzinfo=timezone.utc)
    first = run(trace_with_cache(CLAIM, store, search, client, seed_url=SEED, now=lambda: original_time))
    calls = search.calls; count = len(requests)
    hit = run(trace_with_cache(CLAIM, store, search, client, seed_url=SEED+'?utm_source=test'))
    assert hit.cache_hit and hit.retrieved_at == original_time
    assert search.calls == calls and len(requests) == count
    second = run(trace_with_cache(CLAIM, store, search, client, seed_url=second_seed))
    assert not second.cache_hit and second.result.investigation_id != first.result.investigation_id
    assert DOC not in {s.url for s in second.result.sources}
    assert cache_key(CLAIM) != cache_key(CLAIM, seed_url=SEED)


def test_seed_failure_keeps_search_and_search_failure_keeps_readable_seed():
    search_url = 'https://example.org/search'
    requests = []; client = fetcher({search_url: f'<p>{TEXT}</p>', SEED: f'<p>{TEXT}</p>'}, requests)
    result = run(run_trace(CLAIM, Search([search_url]), client, seed_url='http://127.0.0.1/private'))
    assert [s.url for s in result.result.sources] == [search_url]
    result = run(run_trace(CLAIM, Search(fail=True), client, seed_url=SEED))
    assert [s.url for s in result.result.sources] == [SEED]
    assert any('search unavailable' in warning for warning in result.warnings)


def test_exhausted_budget_and_navigation_roles_do_not_follow():
    requests = []; budget = FetchBudget(max_fetches=0)
    result = run(fetch_seed_context(CLAIM, [], fetcher({SEED: TEXT}, requests), budget, SEED))
    assert requests == [] and result[0] == []
    pages = {SEED: f'<div role="navigation"><p>{TEXT} <a href="{DOC}">DART report</a></p></div><div class="sponsored"><p>{TEXT} <a href="{DOC}">DART report</a></p></div><p>{TEXT}</p>'}
    result = run(run_trace(CLAIM, Search(), fetcher(pages, requests), seed_url=SEED))
    assert requests == [SEED] and not result.result.graph_edges
