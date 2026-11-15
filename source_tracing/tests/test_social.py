"""Offline tests for social posts: search query, reserved slots, no fetching, snippet edges."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import httpx
import pytest

from sample_pages import RETRIEVED_AT
from source_tracing.graph import SNIPPET_NOTE, build_graph
from source_tracing.models import Source, node_for
from source_tracing.pages import FetchedPage, PageFetcher, parse_page
from source_tracing.pipeline import run_trace
from source_tracing.query import SOCIAL_FILTER, build_queries
from source_tracing.retrieval import MAX_SOCIAL_SOURCES, select_sources
from source_tracing.urls import check_public_http_url, is_social_url

CLAIM = "A poll worker found a foreign wifi signal inside a vote tabulator in Michigan"
SHARED = "A poll worker found a foreign wifi signal inside a vote tabulator in Michigan on Tuesday."
SNIPPET_AT = datetime(2026, 11, 14, 21, 0, tzinfo=timezone.utc)


def row(link, rank, title="Poll worker wifi signal vote tabulator Michigan", excerpt=SHARED):
    return {"title": title, "link": link, "excerpt": excerpt, "date": None, "date_field": None,
            "rank": rank, "query": "q", "retrieved_at": RETRIEVED_AT}


@pytest.mark.parametrize("url,expected", [
    ("https://x.com/user/status/1", True),
    ("https://mobile.twitter.com/user/status/1", True),
    ("https://old.reddit.com/r/news/comments/a/", True),
    ("https://www.facebook.com/post/1", True),
    ("https://bsky.app/profile/a/post/b", True),
    ("https://www.reuters.com/world/", False),
    ("https://notx.com/status/1", False),
    ("https://x.com.evil.example/status/1", False),
    ("not a url [", False),
])
def test_is_social_url(url, expected):
    assert is_social_url(url) is expected


def test_second_query_targets_social_sites_within_word_limit():
    queries = build_queries(CLAIM)
    assert queries[0] == CLAIM
    assert queries[1].endswith(SOCIAL_FILTER) and "site:x.com" in queries[1]
    assert len(queries[1].split()) <= 32  # Google ignores words past 32


def test_social_posts_keep_reserved_slots_when_news_fills_the_page():
    news = [row(f"https://news{i}.example.org/a", i) for i in range(12)]
    social = [row(f"https://x.com/u{i}/status/{i}", 20 + i) for i in range(5)]
    sources, dropped = select_sources(news + social, CLAIM)
    urls = [s.url for s in sources]
    assert len(sources) == 10
    assert sum(is_social_url(u) for u in urls) == MAX_SOCIAL_SOURCES
    assert urls[:7] == [f"https://news{i}.example.org/a" for i in range(7)]  # rank order kept
    assert [s.id for s in sources] == [f"s{i}" for i in range(1, 11)]
    assert dropped["over_limit"] == 7


def test_news_fills_unused_social_slots():
    news = [row(f"https://news{i}.example.org/a", i) for i in range(12)]
    sources, _ = select_sources(news + [row("https://x.com/u/status/1", 30)], CLAIM)
    assert len(sources) == 10 and sum(is_social_url(s.url) for s in sources) == 1


def social_source(sid, url, excerpt=SHARED):
    return Source(id=sid, title=f"post {sid}", url=url, date=None, excerpt=excerpt)


def article_page(sid, url, sentence):
    return FetchedPage(source_id=sid, requested_url=url, final_url=url, retrieved_at=RETRIEVED_AT,
                       parsed=parse_page(f"<article><p>{sentence}</p></article>", url))


def test_snippet_edge_links_a_post_to_an_article_with_honest_evidence():
    article = Source(id="s1", title="Fact check", url="https://news.example.org/a", date=None, excerpt="")
    post = social_source("s2", "https://x.com/u/status/9")
    unrelated = social_source("s3", "https://reddit.com/r/x/comments/1/", "Cats are wonderful pets that sleep a lot.")
    graph = build_graph([article, post, unrelated],
                        [article_page("s1", article.url, f"Officials said claims that {SHARED[2:]}")],
                        snippet_times={"s2": SNIPPET_AT, "s3": SNIPPET_AT})
    assert [(e.source_id, e.target_id, e.relationship_type) for e in graph.edges] == [("s1", "s2", "similar_wording")]
    evidence = graph.edges[0].evidence
    assert evidence.target_url == post.url and evidence.target_excerpt == SHARED
    assert "serp_snippet" in evidence.method and evidence.note == SNIPPET_NOTE
    assert evidence.retrieved_at == max(RETRIEVED_AT, SNIPPET_AT)
    assert [n.kind for n in graph.nodes] == ["article", "social_post", "social_post"]


def test_snippets_are_only_compared_when_snippet_times_are_given():
    article = Source(id="s1", title="a", url="https://news.example.org/a", date=None, excerpt="")
    post = social_source("s2", "https://x.com/u/status/9")
    graph = build_graph([article, post], [article_page("s1", article.url, SHARED)])
    assert graph.edges == []  # no snippet_times: ordinary page-only behaviour


def test_two_posts_with_the_same_wording_are_linked():
    graph = build_graph([social_source("s1", "https://x.com/a/status/1"), social_source("s2", "https://x.com/b/status/2")],
                        [], snippet_times={"s1": SNIPPET_AT, "s2": SNIPPET_AT})
    assert [(e.source_id, e.target_id) for e in graph.edges] == [("s1", "s2")]


def test_node_kind_is_derived_from_the_url():
    assert node_for(social_source("s1", "https://www.reddit.com/r/a/")).kind == "social_post"
    assert node_for(social_source("s1", "https://www.nasa.gov/a")).kind == "article"


def test_pipeline_never_fetches_social_pages():
    class FakeSearch:
        async def search(self, query, budget):
            budget.charge()
            return {"organic": [
                {"rank": 1, "link": "https://news.example.org/a", "title": "Poll worker wifi tabulator", "description": SHARED},
                {"rank": 2, "link": "https://x.com/u/status/9", "title": "Poll worker wifi tabulator", "description": SHARED},
            ]}

    fetched_urls = []

    def handler(request):
        fetched_urls.append(str(request.url))
        return httpx.Response(200, headers={"content-type": "text/html"}, content=f"<article><p>{SHARED}</p></article>".encode())

    fetcher = PageFetcher(transport=httpx.MockTransport(handler), now=lambda: RETRIEVED_AT, url_check=check_public_http_url)
    trace = asyncio.run(run_trace(CLAIM, FakeSearch(), fetcher))
    assert fetched_urls == ["https://news.example.org/a"]
    assert trace.serp_requests == 2  # news query + social query
    kinds = {n.url: n.kind for n in trace.result.graph_nodes}
    assert kinds == {"https://news.example.org/a": "article", "https://x.com/u/status/9": "social_post"}
    assert [(e.source_id, e.target_id, e.relationship_type) for e in trace.result.graph_edges] == [("s1", "s2", "similar_wording")]
