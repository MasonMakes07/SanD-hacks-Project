"""Live pipeline: SERP retrieval -> page fetch -> evidence graph -> TraceResult.

This runs behind `trace_claim` on a cache miss.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from .graph import build_graph
from .models import TraceResult, Passage
from .query import keywords
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
    sources = attach_passages(claim, sources, pages)
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


def attach_passages(claim, sources, pages):
    """Keep two relevant literal windows per source; never synthesize quoted text."""
    terms = set(keywords(claim))
    by_id = {page.source_id: page for page in pages}
    enriched = []
    for source in sources:
        page = by_id.get(source.id)
        if page is None:
            enriched.append(source)
            continue
        windows = []
        for block in page.parsed.blocks:
            # Overlapping windows preserve exact wording across long paragraph boundaries.
            for offset in range(0, len(block), 750):
                text = block[offset:offset + 900]
                score = len(terms & set(keywords(text)))
                if score >= min(2, max(1, len(terms))):
                    windows.append((score, len(windows), text))
        selected = sorted(windows, key=lambda row: (-row[0], row[1]))[:2]
        passages = [Passage(id=f"{source.id}-p{i}", text=text,
                            retrieved_at=page.retrieved_at)
                    for i, (_, _, text) in enumerate(selected, 1)]
        enriched.append(source.model_copy(update={
            "passages": passages, "evidence_kind": "fetched_article",
            "retrieved_at": page.retrieved_at,
            "provenance": {**source.provenance, "body_status": "retrieved",
                           "final_url": page.final_url},
        }))
    return enriched
