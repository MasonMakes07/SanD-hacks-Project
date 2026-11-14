"""Stage 5: the integration surface Person 2 calls.

Person 2's code does not exist in the repo yet, so the contract here is
Plan.md section 4 ("Shared API Contract"), written out by hand below and NOT
derived from our own models. `person2_style_response` plays the part of the
/api/analyze handler: it adds Person 2's fields and merges ours. It lives only
in this test; no route exists in this module.
"""

from __future__ import annotations

import asyncio
import json
import socket
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import pytest

import source_tracing
from sample_pages import PAGES, SOURCES
from source_tracing.pages import PageFetcher
from source_tracing.service import TraceService, set_service, trace_claim, trace_claim_detailed
from source_tracing.serp_client import SerpAuthError
from source_tracing.store import JsonFileStore, NullStore, StoreError
from source_tracing.urls import check_public_http_url

ROOT = Path(__file__).parent.parent
CLAIM = "The council approved a 12% increase in the water rate"

# --- Plan.md section 4, response table, transcribed ---
PLAN_RESPONSE_FIELDS = {"investigation_id", "claim", "status", "explanation", "sources",
                        "earliest_observed_source", "graph_nodes", "graph_edges"}
PERSON2_FIELDS = {"claim", "status", "explanation"}
PLAN_SOURCE_FIELDS = {"title", "url", "date", "excerpt"}          # "title, url, date (or null), excerpt"
PLAN_EDGE_FIELDS = {"source_id", "target_id", "relationship_type", "evidence"}
PLAN_RELATIONSHIPS = {"explicit_citation", "similar_wording", "same_fact_check"}
# Fields we add beyond Plan.md; reported to Person 2 in PERSON3_HANDOFF.md.
EXTRA_SOURCE_FIELDS = {"id"}


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def isolate(monkeypatch):
    for name in ("TRUTHTRACE_MOCK", "TRUTHTRACE_CACHE_DIR", "DATABASE_URL", "BRIGHTDATA_API_KEY", "BRIGHTDATA_SERP_ZONE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr("source_tracing.config.ENV_FILE", ROOT / "does-not-exist.env")

    def blocked(*a, **k):
        raise AssertionError("offline tests must not open sockets")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    set_service(None)
    yield
    set_service(None)


def person2_style_response(claim: str, trace) -> dict:
    """What an /api/analyze handler does with our result. Status/explanation are Person 2's."""
    return {"claim": claim, "status": "insufficient_evidence", "explanation": "(Person 2 writes this)",
            **trace.to_contract_dict()}


def assert_matches_plan_contract(resp: dict) -> None:
    assert set(resp) == PLAN_RESPONSE_FIELDS
    assert isinstance(resp["investigation_id"], str) and resp["investigation_id"]
    ids = set()
    for s in resp["sources"]:
        assert PLAN_SOURCE_FIELDS <= set(s) and set(s) - PLAN_SOURCE_FIELDS == EXTRA_SOURCE_FIELDS
        assert urlsplit(s["url"]).scheme in ("http", "https") and urlsplit(s["url"]).netloc  # clickable
        assert s["date"] is None or (isinstance(s["date"], str) and len(s["date"]) == 10)
        assert s["title"] and isinstance(s["excerpt"], str)
        ids.add(s["id"])
    assert len(ids) == len(resp["sources"])
    earliest = resp["earliest_observed_source"]
    assert earliest is None or (earliest["date"] is not None and earliest in resp["sources"])
    assert resp["graph_nodes"] == [{"id": s["id"], "title": s["title"], "url": s["url"]} for s in resp["sources"]]
    for e in resp["graph_edges"]:
        assert set(e) == PLAN_EDGE_FIELDS
        assert e["relationship_type"] in PLAN_RELATIONSHIPS
        assert e["source_id"] in ids and e["target_id"] in ids  # graph ids match source ids
        ev = e["evidence"]
        assert ev["url"] and (ev["excerpt"] or ev["hyperlink"]) and ev["retrieved_at"]  # "every edge needs evidence"
    json.dumps(resp)  # FastAPI must be able to serialize it
    # Person 3 never supplies a verdict.
    assert not {"status", "explanation", "claim"} & set(resp) - PERSON2_FIELDS


# ---------- fakes for the live path ----------

class FakeSearch:
    def __init__(self, urls=None, error=None):
        self.calls, self.error = 0, error
        self.urls = [s.url for s in SOURCES[:3]] if urls is None else urls

    async def search(self, query, budget):
        self.calls += 1
        budget.charge()
        if self.error:
            raise self.error
        return {"organic": [{"rank": i, "link": u, "title": "Council approves water rate increase vote",
                             "description": "council water rate increase"} for i, u in enumerate(self.urls, 1)]}


def make_fetcher(requests=None):
    by_url = {s.url: s.id for s in SOURCES}

    def handler(req):
        if requests is not None:
            requests.append(str(req.url))
        return httpx.Response(200, headers={"content-type": "text/html"}, content=PAGES[by_url[str(req.url)]].encode())

    return PageFetcher(transport=httpx.MockTransport(handler), url_check=check_public_http_url)


def service(search=None, store=None, requests=None, **kw):
    return TraceService(searcher=search or FakeSearch(), fetcher=make_fetcher(requests), store=store or NullStore(), **kw)


# ---------- importable + signature ----------

def test_public_import_and_signature():
    import inspect
    from source_tracing import trace_claim as imported
    assert imported is trace_claim and asyncio.iscoroutinefunction(imported)
    sig = inspect.signature(imported)
    assert list(sig.parameters) == ["claim", "seed_url"] and sig.parameters["seed_url"].default is None
    assert sig.return_annotation in ("TraceResult", source_tracing.TraceResult)


# ---------- contract match, mocked sources through the full chain ----------

def test_full_chain_output_matches_plan_contract():
    set_service(service())
    result = run(trace_claim(CLAIM, "https://news.example.org/article"))
    assert result.sources and result.graph_edges
    assert_matches_plan_contract(person2_style_response(CLAIM, result))


def test_earliest_observed_source_and_nulls_in_the_final_json():
    set_service(service())
    resp = person2_style_response(CLAIM, run(trace_claim(CLAIM)))
    assert resp["earliest_observed_source"]["id"] == "s1"
    assert [s["date"] for s in resp["sources"]] == ["2026-03-02", "2026-03-04", None]


def test_empty_search_gives_valid_empty_response():
    set_service(service(FakeSearch(urls=[])))
    resp = person2_style_response(CLAIM, run(trace_claim(CLAIM)))
    assert resp["sources"] == [] and resp["graph_edges"] == [] and resp["earliest_observed_source"] is None
    assert_matches_plan_contract(resp)


# ---------- cache through the public call ----------

def test_second_call_is_served_from_the_store_with_no_external_requests(tmp_path):
    search, requests = FakeSearch(), []
    set_service(service(search, JsonFileStore(tmp_path), requests))
    first = run(trace_claim_detailed(CLAIM))
    calls, fetched = search.calls, len(requests)
    second = run(trace_claim_detailed(CLAIM.upper()))
    assert not first.cache_hit and second.cache_hit
    assert search.calls == calls and len(requests) == fetched
    assert second.result.to_contract_dict() == first.result.to_contract_dict()
    assert second.retrieved_at == first.retrieved_at and second.storage_backend == "local_json"


def test_local_cache_hit_works_without_brightdata_credentials(tmp_path):
    from datetime import datetime, timezone
    from source_tracing.service import build_service
    from source_tracing.store import StoredInvestigation, cache_key
    from source_tracing.models import TraceResult

    claim = "A cached claim"
    result = TraceResult(investigation_id="cached-example", sources=[],
                         earliest_observed_source=None, graph_nodes=[], graph_edges=[])
    stored_at = datetime(2026, 11, 14, 20, 0, tzinfo=timezone.utc)
    JsonFileStore(tmp_path).save(StoredInvestigation(result=result, cache_key=cache_key(claim),
                                                     claim=claim, retrieved_at=stored_at))
    hit = run(build_service(env={"TRUTHTRACE_CACHE_DIR": str(tmp_path)}).trace(claim))
    assert hit.cache_hit and hit.result == result and hit.retrieved_at == stored_at


# ---------- failures become empty results, never invented ones ----------

@pytest.mark.parametrize("error", [SerpAuthError("rejected"), ])
def test_provider_failure_returns_safe_empty_result(error):
    set_service(service(FakeSearch(error=error)))
    detailed = run(trace_claim_detailed(CLAIM))
    assert detailed.error and "source search failed" in detailed.error
    resp = person2_style_response(CLAIM, detailed.result)
    assert resp["sources"] == [] and resp["graph_edges"] == [] and resp["earliest_observed_source"] is None
    assert_matches_plan_contract(resp)
    assert run(trace_claim(CLAIM)).sources == []


def test_storage_failure_on_save_still_returns_real_result():
    class Broken(NullStore):
        backend = "broken"

        def find_cached(self, key):
            raise StoreError("database unavailable")

        def save(self, stored):
            raise StoreError("database unavailable")

    set_service(service(store=Broken()))
    out = run(trace_claim_detailed(CLAIM))
    assert out.result.sources and not out.saved and out.error is None
    assert any("not saved" in w for w in out.warnings)


def test_missing_credentials_returns_empty_result_naming_variables_not_values():
    out = run(trace_claim_detailed(CLAIM))  # no service installed, env cleared by fixture
    assert out.error and "BRIGHTDATA_API_KEY" in out.error and "BRIGHTDATA_SERP_ZONE" in out.error
    assert out.result.sources == [] and out.result.investigation_id.startswith("EMPTY-")
    assert_matches_plan_contract(person2_style_response(CLAIM, out.result))


def test_timeout_returns_empty_result():
    class Slow:
        async def search(self, query, budget):
            await asyncio.sleep(5)

    set_service(service(Slow(), timeout_s=0.05))
    out = run(trace_claim_detailed(CLAIM))
    assert out.error and "timed out" in out.error and out.result.sources == []


@pytest.mark.parametrize("claim", ["", "   ", None])
def test_bad_claim_raises_value_error(claim):
    set_service(service())
    with pytest.raises(ValueError):
        run(trace_claim(claim))


@pytest.mark.parametrize("seed", ["file:///etc/passwd", "javascript:1", "no-scheme.example.org", ""])
def test_bad_seed_url_raises_value_error(seed):
    set_service(service())
    with pytest.raises(ValueError):
        run(trace_claim(CLAIM, seed))


def test_concurrent_calls_do_not_interfere():
    set_service(service())

    async def both():
        return await asyncio.gather(trace_claim(CLAIM), trace_claim("The council rejected the water rate increase"))

    a, b = run(both())
    assert a.sources and b.sources and a.investigation_id != b.investigation_id


# ---------- mock mode ----------

def test_mock_mode_is_labelled_and_makes_no_calls(monkeypatch):
    monkeypatch.setenv("TRUTHTRACE_MOCK", "1")
    out = run(trace_claim_detailed(CLAIM))
    assert out.storage_backend == "mock" and out.result.investigation_id.startswith("MOCK-")
    assert any("MOCK" in w for w in out.warnings)
    assert all(s.title.startswith("[MOCK]") for s in out.result.sources)


# ---------- the handoff document ----------

def test_handoff_example_in_doc_matches_the_fixture_exactly():
    import re
    doc = (ROOT / "PERSON3_HANDOFF.md").read_text(encoding="utf-8")
    blocks = re.findall(r"```json\n(.*?)\n```", doc, re.S)
    fixture = json.loads((ROOT / "fixtures" / "sample_graph_synthetic.json").read_text(encoding="utf-8"))
    assert any(json.loads(b) == fixture for b in blocks), "PERSON3_HANDOFF.md example is out of date"
    for name in ("BRIGHTDATA_API_KEY", "BRIGHTDATA_SERP_ZONE", "DATABASE_URL", "TRUTHTRACE_MOCK", "TRUTHTRACE_CACHE_DIR"):
        assert name in doc
