"""Live pipeline: SERP retrieval -> page fetch -> evidence graph -> TraceResult.

This runs behind `trace_claim` on a cache miss.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from .graph import build_graph
from .models import TraceResult
from .pages import FetchBudget, PageFetcher
from .retrieval import RetrievalResult, SerpSearcher, retrieve_sources
from .serp_client import SerpBudget
from .urls import is_social_url


@dataclass
class TraceRun:
    """TraceResult plus internal bookkeeping that is not part of Person 2's contract."""

    result: TraceResult
    retrieval: RetrievalResult
    serp_requests: int
    page_fetches: int
    pages_parsed: int
    warnings: list[str] = field(default_factory=list)


async def run_trace(claim: str, searcher: SerpSearcher, fetcher: PageFetcher) -> TraceRun:
    serp_budget, fetch_budget = SerpBudget(), FetchBudget()
    retrieval = await retrieve_sources(claim, searcher, budget=serp_budget)
    sources = [s.to_source() for s in retrieval.sources]
    # Social posts are login-walled: never fetched, known only from their search snippet.
    to_fetch = [(s.id, s.url) for s in sources if not is_social_url(s.url)]
    pages, fetch_warnings = await fetcher.fetch_pages(to_fetch, fetch_budget)
    snippet_times = {s.id: s.provenance.retrieved_at for s in retrieval.sources if is_social_url(s.url)}
    graph = build_graph(sources, pages, snippet_times=snippet_times)
    result = TraceResult(
        investigation_id=uuid.uuid4().hex,
        sources=graph.sources,
        earliest_observed_source=graph.earliest_observed_source,
        graph_nodes=graph.nodes,
        graph_edges=graph.edges,
    )
    return TraceRun(
        result=result, retrieval=retrieval, serp_requests=serp_budget.used,
        page_fetches=fetch_budget.used, pages_parsed=len(pages),
        warnings=[*retrieval.warnings, *fetch_warnings, *graph.warnings],
    )
