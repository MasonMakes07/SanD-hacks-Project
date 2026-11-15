"""Offline tests for Stage 3: page parsing, safe fetching and the evidence graph."""

from __future__ import annotations

import asyncio
import json
import socket
from datetime import date, datetime, timezone
from pathlib import Path

import httpx
import pytest

from sample_pages import PAGES, RETRIEVED_AT, SOURCES, fetched_pages, sample_trace_result
from source_tracing.graph import (
    SIMILARITY_NOTE, SIMILARITY_THRESHOLD, build_graph, earliest_observed, sentence_similarity,
)
from source_tracing.models import Source, TraceResult
from source_tracing.pages import FetchBudget, FetchedPage, PageFetcher, parse_page
from source_tracing.pipeline import run_trace
from source_tracing.urls import UnsafeUrlError, check_public_http_url

FIXTURE = Path(__file__).parent.parent / "fixtures" / "sample_graph_synthetic.json"
FORBIDDEN_WORDS = ("copied", "plagiar", "original author", "first ever", "originated", "stole")


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def blocked(*a, **k):
        raise AssertionError("offline tests must not open sockets")

    monkeypatch.setattr(socket.socket, "connect", blocked)


def edge_set(graph):
    return {(e.source_id, e.target_id, e.relationship_type) for e in graph.edges}


def page(sid, url, html):
    return FetchedPage(source_id=sid, requested_url=url, final_url=url,
                       retrieved_at=RETRIEVED_AT, parsed=parse_page(html, url))


def src(sid, url, d=None):
    return Source(id=sid, title=f"title {sid}", url=url, date=d, excerpt="")


# ---------- the sample graph: exactly these edges, each for a stated reason ----------

def test_sample_graph_has_exactly_the_supported_edges():
    g = build_graph(SOURCES, fetched_pages())
    assert edge_set(g) == {
        ("s2", "s1", "explicit_citation"),  # s2's body links to s1
        ("s1", "s2", "similar_wording"),    # shared sentence, s2 adds a clause
        ("s1", "s3", "similar_wording"),    # identical sentence
        ("s2", "s3", "similar_wording"),    # same shared sentence
    }
    assert [n.id for n in g.nodes] == ["s1", "s2", "s3", "s4", "s5"]


def test_explicit_citation_evidence_is_the_observed_hyperlink():
    g = build_graph(SOURCES, fetched_pages())
    (e,) = [e for e in g.edges if e.relationship_type == "explicit_citation"]
    ev = e.evidence
    assert (e.source_id, e.target_id) == ("s2", "s1")
    assert ev.url == "https://example.com/analysis"           # where the link was seen
    assert ev.hyperlink == "https://example.org/council-vote"  # tracking params and #fragment removed
    assert ev.anchor_text == "first reported by Example Org"
    assert ev.anchor_text in ev.excerpt
    # The excerpt is the page's own paragraph with the <a> tag stripped.
    assert ev.excerpt == "The vote was first reported by Example Org earlier this week."
    assert "The vote was <a href" in PAGES["s2"] and "</a> earlier this week." in PAGES["s2"]
    assert ev.method == "hyperlink_in_page_body" and ev.retrieved_at == RETRIEVED_AT
    assert ev.similarity is None


def test_no_citation_from_serp_only():
    # s5 has the same headline as s1 and a snippet matching s1's text, but was never fetched.
    g = build_graph(SOURCES, fetched_pages())
    assert not [e for e in g.edges if "s5" in (e.source_id, e.target_id)]
    # With no pages at all, snippets and headlines alone produce an empty graph.
    g = build_graph(SOURCES, [])
    assert g.edges == [] and len(g.nodes) == 5


def test_similar_wording_is_measured_quoted_and_never_called_copying():
    g = build_graph(SOURCES, fetched_pages())
    similar = [e for e in g.edges if e.relationship_type == "similar_wording"]
    assert len(similar) == 3
    for e in similar:
        ev = e.evidence
        assert ev.similarity >= SIMILARITY_THRESHOLD
        assert ev.similarity == pytest.approx(sentence_similarity(ev.excerpt, ev.target_excerpt), abs=1e-3)
        assert ev.excerpt in " ".join(PAGES[e.source_id].split())
        assert ev.target_excerpt in " ".join(PAGES[e.target_id].split())
        assert ev.hyperlink is None
        assert ev.note == SIMILARITY_NOTE
        # Undirected: stored once, lower source first, regardless of dates.
        assert e.source_id < e.target_id
    identical = next(e for e in similar if (e.source_id, e.target_id) == ("s1", "s3"))
    assert identical.evidence.similarity == 1.0
    text = json.dumps([e.model_dump(mode="json") for e in g.edges]).lower()
    affirmative = text.replace(SIMILARITY_NOTE.lower(), "")
    assert not any(w in affirmative for w in FORBIDDEN_WORDS)


def test_similar_wording_is_symmetric_in_input_order():
    a = build_graph(SOURCES, fetched_pages(("s1", "s3")))
    b = build_graph(SOURCES, fetched_pages(("s3", "s1")))
    assert edge_set(a) == edge_set(b) == {("s1", "s3", "similar_wording")}


def test_unrelated_articles_get_no_edges():
    g = build_graph(SOURCES, fetched_pages(("s3", "s4")))
    assert g.edges == []


def test_boilerplate_and_short_sentences_do_not_count_as_similarity():
    # s1 and s2 share an identical cookie footer; with the real shared sentence removed, no edge.
    html_a = "<p>We use cookies to improve your experience on this website and for analytics.</p><p>Short one here.</p>"
    html_b = "<p>We use cookies to improve your experience on this website and for analytics.</p><p>Short one here.</p>"
    g = build_graph([src("a", "https://example.org/a"), src("b", "https://example.com/b")],
                    [page("a", "https://example.org/a", html_a), page("b", "https://example.com/b", html_b)])
    assert g.edges == []


def test_below_threshold_overlap_is_not_an_edge():
    a = "The council voted on Tuesday to approve a 12 percent increase in the water utility rate."
    b = "On Tuesday the council held a long meeting about parks, libraries and the summer festival budget."
    assert sentence_similarity(a, b) < SIMILARITY_THRESHOLD
    g = build_graph([src("a", "https://example.org/a"), src("b", "https://example.com/b")],
                    [page("a", "https://example.org/a", f"<p>{a}</p>"), page("b", "https://example.com/b", f"<p>{b}</p>")])
    assert g.edges == []


# ---------- links that must not become citations ----------

def test_nav_hidden_sponsored_and_unsafe_links_are_not_citations():
    parsed = parse_page(PAGES["s4"], "https://example.edu/garden")
    assert parsed.links == []  # nav, display:none, rel=sponsored, javascript:, file:, private IPs
    assert parse_page(PAGES["s1"], "https://example.org/council-vote").links == []  # nav link only
    g = build_graph(SOURCES, fetched_pages(("s1", "s4")))
    assert g.edges == []


def test_malicious_page_instructions_change_nothing():
    g = build_graph(SOURCES, fetched_pages())
    s4 = next(s for s in g.sources if s.id == "s4")
    assert s4.date is None  # neither "yesterday" nor the injected 2020-01-01
    assert not [e for e in g.edges if "s4" in (e.source_id, e.target_id)]
    assert g.earliest_observed_source.id == "s1"


def test_self_links_and_links_to_unretrieved_pages_are_ignored():
    html = ('<p>See <a href="/a#section">our earlier section</a> and '
            '<a href="https://elsewhere.example.net/x">an outside page</a> for more detail here.</p>')
    g = build_graph([src("a", "https://example.org/a"), src("b", "https://example.com/b")],
                    [page("a", "https://example.org/a", html)])
    assert g.edges == []


def test_relative_link_and_redirected_target_resolve_to_a_citation():
    # b was requested at /old and redirected to /new; a links to /new.
    a = page("a", "https://example.org/a", '<p>Details are in <a href="https://example.com/new">this report</a> from the agency.</p>')
    b = FetchedPage(source_id="b", requested_url="https://example.com/old", final_url="https://example.com/new",
                    retrieved_at=RETRIEVED_AT, parsed=parse_page("<p>x</p>", "https://example.com/new"))
    g = build_graph([src("a", "https://example.org/a"), src("b", "https://example.com/old")], [a, b])
    assert edge_set(g) == {("a", "b", "explicit_citation")}
    rel = parse_page('<p><a href="../other/page?utm_source=z">x</a> text</p>', "https://example.org/dir/a")
    assert rel.links[0].url == "https://example.org/other/page"


def test_one_edge_per_pair_even_with_repeated_links():
    html = ('<p>First <a href="https://example.com/b">link</a> in text.</p>'
            '<p>Second <a href="https://www.example.com/b/">link</a> in text.</p>')
    g = build_graph([src("a", "https://example.org/a"), src("b", "https://example.com/b")],
                    [page("a", "https://example.org/a", html)])
    assert len(g.edges) == 1 and g.edges[0].evidence.excerpt == "First link in text."


# ---------- duplicate pages ----------

def test_duplicate_pages_sharing_a_canonical_get_no_edge():
    body = "<p>The council voted on Tuesday to approve a 12 percent increase in the water utility rate starting in July.</p>"
    amp = '<link rel="canonical" href="https://example.org/story">' + body + '<p>Full version <a href="https://example.org/story">here</a> for readers.</p>'
    g = build_graph([src("a", "https://example.org/story"), src("b", "https://example.org/amp/story")],
                    [page("a", "https://example.org/story", body), page("b", "https://example.org/amp/story", amp)])
    assert g.edges == []
    assert any("same document" in w for w in g.warnings)


def test_duplicate_source_ids_are_rejected():
    with pytest.raises(ValueError):
        build_graph([src("a", "https://example.org/a"), src("a", "https://example.org/b")], [])


# ---------- dates ----------

def test_dates_come_only_from_agreeing_page_metadata():
    g = build_graph(SOURCES, fetched_pages())
    dates = {s.id: s.date for s in g.sources}
    assert dates == {"s1": date(2026, 3, 2), "s2": date(2026, 3, 4), "s3": None, "s4": None, "s5": None}
    # s3 says "Posted 3 days ago" and was retrieved today: neither becomes a date.
    assert g.earliest_observed_source.id == "s1"
    assert g.earliest_observed_source in g.sources


def test_conflicting_page_dates_yield_null():
    html = ('<meta property="article:published_time" content="2026-03-02T00:00:00Z">'
            '<script type="application/ld+json">{"datePublished":"2025-01-01"}</script><p>x</p>')
    parsed = parse_page(html, "https://example.org/a")
    assert parsed.published is None and parsed.date_conflict
    g = build_graph([src("a", "https://example.org/a")], [page("a", "https://example.org/a", html)])
    assert g.sources[0].date is None and g.earliest_observed_source is None


def test_conflicting_page_dates_invalidate_serp_date():
    html = ('<meta property="article:published_time" content="2026-03-02">'
            '<script type="application/ld+json">{"datePublished":"2025-01-01"}</script><p>x</p>')
    g = build_graph([src("a", "https://example.org/a", date(2026, 3, 2))],
                    [page("a", "https://example.org/a", html)])
    assert g.sources[0].date is None
    assert g.earliest_observed_source is None


@pytest.mark.parametrize("html", [
    '<meta property="article:modified_time" content="2026-03-02">',
    '<meta property="article:published_time" content="2099-01-01">',
    '<time datetime="2026-03-02">March 2</time>',  # not marked as publish date
    '<script type="application/ld+json">{not json</script>',
    '<p>Published March 2, 2026 by staff</p>',     # body text is never mined for dates
])
def test_unverified_date_hints_yield_null(html):
    assert parse_page(html, "https://example.org/a").published is None


def test_missing_dates_everywhere_gives_null_earliest():
    g = build_graph(SOURCES, fetched_pages(("s3", "s4")))
    assert g.earliest_observed_source is None
    assert earliest_observed([]) is None


def test_earliest_is_minimum_date_and_ties_keep_listing_order():
    ss = [src("a", "https://example.org/a", date(2026, 5, 1)), src("b", "https://example.org/b", date(2026, 2, 1)),
          src("c", "https://example.org/c", date(2026, 2, 1)), src("d", "https://example.org/d")]
    assert earliest_observed(ss).id == "b"


# ---------- empty graph ----------

def test_empty_inputs_give_a_valid_empty_result():
    g = build_graph([], [])
    result = TraceResult(investigation_id="x", sources=g.sources, earliest_observed_source=None,
                         graph_nodes=g.nodes, graph_edges=g.edges)
    assert result.to_contract_dict() == {
        "investigation_id": "x", "sources": [], "earliest_observed_source": None,
        "graph_nodes": [], "graph_edges": [],
    }


def test_garbage_html_does_not_crash():
    for html in ["", "<<<>>>", "<a href=", "<nav><p>unclosed", "\x00\x01" * 50, "<p>" * 5000]:
        parse_page(html, "https://example.org/a")
    g = build_graph([src("a", "https://example.org/a")], [page("a", "https://example.org/a", "<nav><p>unclosed")])
    assert g.edges == []


# ---------- fixture ----------

def test_sample_fixture_matches_algorithm_output_and_contract():
    stored = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert stored == sample_trace_result().to_contract_dict()
    assert stored["investigation_id"].startswith("SYNTHETIC-")
    TraceResult.model_validate(stored)


# ---------- fetching ----------

def fetcher(handler, **kw):
    kw.setdefault("url_check", check_public_http_url)  # literal checks only; no DNS offline
    return PageFetcher(transport=httpx.MockTransport(handler), now=lambda: RETRIEVED_AT, **kw)


def html_response(body="<p>ok</p>", **kw):
    return httpx.Response(200, headers={"content-type": "text/html; charset=utf-8"}, content=body.encode(), **kw)


def test_fetch_follows_safe_redirect_and_records_final_url():
    def handler(req):
        if req.url.path == "/old":
            return httpx.Response(301, headers={"location": "/new"})
        return html_response("<p>hello there</p>")

    pages, warnings = run(fetcher(handler).fetch_pages([("s1", "https://example.org/old")]))
    assert warnings == [] and pages[0].final_url == "https://example.org/new"
    assert pages[0].retrieved_at == RETRIEVED_AT and pages[0].parsed.blocks == ["hello there"]


@pytest.mark.parametrize("location", [
    "http://127.0.0.1/admin", "http://169.254.169.254/latest/meta-data/", "http://localhost/",
    "file:///etc/passwd", "http://10.0.0.1:8080/", "http://[::1]/",
])
def test_redirect_to_private_address_is_blocked_before_any_request(location):
    seen = []

    def handler(req):
        seen.append(str(req.url))
        return httpx.Response(302, headers={"location": location})

    pages, warnings = run(fetcher(handler).fetch_pages([("s1", "https://example.org/a")]))
    assert pages == [] and "blocked unsafe URL" in warnings[0]
    assert seen == ["https://example.org/a"]  # the private target was never requested


@pytest.mark.parametrize("url", ["http://192.168.0.1/", "http://localhost/x", "ftp://example.org/a", "https://u:p@example.org/"])
def test_invalid_urls_are_never_requested(url):
    def handler(req):
        raise AssertionError("must not be requested")

    pages, warnings = run(fetcher(handler).fetch_pages([("s1", url)]))
    assert pages == [] and "blocked unsafe URL" in warnings[0]


def test_dns_resolving_to_private_address_is_blocked(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("10.0.0.7", 443))])

    def handler(req):
        raise AssertionError("must not be requested")

    f = PageFetcher(transport=httpx.MockTransport(handler))  # default check resolves DNS
    pages, warnings = run(f.fetch_pages([("s1", "https://internal.example.org/")]))
    assert pages == [] and "non-public" in warnings[0]


def test_oversized_non_html_error_and_timeout_pages_are_skipped_with_warnings():
    def handler(req):
        path = req.url.path
        if path == "/big":
            return html_response("x" * 5000)
        if path == "/lying":
            return httpx.Response(200, headers={"content-type": "text/html", "content-length": "999999999"}, content=b"x")
        if path == "/pdf":
            return httpx.Response(200, headers={"content-type": "application/pdf"}, content=b"%PDF")
        if path == "/gone":
            return httpx.Response(404, headers={"content-type": "text/html"})
        raise httpx.ReadTimeout("slow")

    targets = [(f"s{i}", f"https://example.org/{p}") for i, p in enumerate(["big", "lying", "pdf", "gone", "slow"], 1)]
    pages, warnings = run(fetcher(handler, max_bytes=1000).fetch_pages(targets))
    assert pages == []
    assert [w.split("(")[1].rstrip(")") for w in warnings] == [
        "page exceeds size limit", "page exceeds size limit", "not HTML (application/pdf", "HTTP 404", "timed out after 10s",
    ] or len(warnings) == 5
    assert "size limit" in warnings[0] and "not HTML" in warnings[2] and "404" in warnings[3] and "timed out" in warnings[4]


def test_redirect_loop_stops():
    calls = []

    def handler(req):
        calls.append(1)
        return httpx.Response(302, headers={"location": "/loop"})

    pages, warnings = run(fetcher(handler).fetch_pages([("s1", "https://example.org/loop")]))
    assert pages == [] and "too many redirects" in warnings[0] and len(calls) == 4


def test_never_more_than_five_page_fetches():
    requested = []

    def handler(req):
        requested.append(str(req.url))
        return html_response()

    targets = [(f"s{i}", f"https://site{i}.example.org/a") for i in range(1, 16)]
    budget = FetchBudget(99)
    pages, warnings = run(fetcher(handler).fetch_pages(targets, budget))
    assert budget.max_fetches == 5 and budget.used == 5 and len(requested) == 5 and len(pages) == 5
    assert [p.source_id for p in pages] == [f"s{i}" for i in range(1, 6)]
    assert "10 source page(s) not fetched" in warnings[0]
    # A spent budget makes no further requests.
    pages, _ = run(fetcher(handler).fetch_pages(targets, budget))
    assert pages == [] and len(requested) == 5


# ---------- end to end, offline ----------

def test_pipeline_end_to_end_with_mocked_search_and_pages():
    class FakeSearch:
        async def search(self, query, budget):
            budget.charge()
            return {"organic": [
                {"rank": i, "link": s.url, "title": "Council approves water rate increase vote",
                 "description": "council water rate increase"} for i, s in enumerate(SOURCES[:3], 1)
            ]}

    def handler(req):
        sid = {s.url: s.id for s in SOURCES}[str(req.url)]
        return html_response(PAGES[sid])

    trace = run(run_trace("The council approved a water rate increase", FakeSearch(), fetcher(handler)))
    r = trace.result
    assert trace.serp_requests <= 2 and trace.page_fetches == 3 and trace.pages_parsed == 3
    assert {(e.source_id, e.target_id, e.relationship_type) for e in r.graph_edges} == {
        ("s2", "s1", "explicit_citation"), ("s1", "s2", "similar_wording"),
        ("s1", "s3", "similar_wording"), ("s2", "s3", "similar_wording"),
    }
    assert r.earliest_observed_source.id == "s1" and r.earliest_observed_source.date == date(2026, 3, 2)
    assert len(r.investigation_id) == 32
    assert set(r.to_contract_dict()) == {"investigation_id", "sources", "earliest_observed_source", "graph_nodes", "graph_edges"}


def test_pipeline_with_no_search_results_returns_empty_graph():
    class Empty:
        async def search(self, query, budget):
            budget.charge()
            return {"organic": []}

    trace = run(run_trace("A claim nobody wrote about", Empty(), fetcher(lambda r: html_response())))
    assert trace.result.sources == [] and trace.result.graph_edges == [] and trace.page_fetches == 0
    assert "no relevant sources found" in trace.warnings


def test_sparse_search_expands_to_seven_sources_with_five_page_budget():
    queries = []

    class CoverageSearch:
        async def search(self, query, budget):
            budget.charge()
            queries.append(query)
            indices = range(1, 3) if len(queries) == 1 else range(1, 8)
            return {"organic": [
                {"link": f"https://news{i}.example.org/water", "title": f"Council water rate report {i}",
                 "description": "Council approved the utility water rate increase"}
                for i in indices
            ]}

    def handler(req):
        return html_response('<article><p>The city council approved the water utility rate increase after a public hearing.</p>'
                             '<p>Read the <a href="https://news1.example.org/water">council water report</a> for details.</p></article>')

    trace = run(run_trace("The city council approved a 12% increase in the water utility rate", CoverageSearch(), fetcher(handler)))
    assert len(trace.result.sources) == len(trace.result.graph_nodes) == 7
    assert trace.page_fetches == trace.pages_parsed == 5
    assert trace.serp_requests == 2
    assert queries[0] != queries[1] and 'site:' not in queries[1]
    assert ('s7', 's1', 'explicit_citation') not in {(e.source_id, e.target_id, e.relationship_type) for e in trace.result.graph_edges}
    assert ('s5', 's1', 'explicit_citation') in {(e.source_id, e.target_id, e.relationship_type) for e in trace.result.graph_edges}
    assert all(edge.evidence for edge in trace.result.graph_edges)
