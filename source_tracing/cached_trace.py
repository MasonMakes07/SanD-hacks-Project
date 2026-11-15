"""Cache-first tracing: a repeated claim is answered from storage with zero
Bright Data requests and zero page fetches.

Failure policy (chosen so a demo keeps working, never to hide a problem):
  - lookup fails  -> warn, then trace live. This spends at most the normal
                     per-investigation cap (2 SERP requests, 10 page fetches).
  - save fails    -> return the fresh result, `saved=False`, with a warning.
  - the live trace itself fails -> the error propagates; nothing is saved.
Cached results keep their ORIGINAL retrieved_at.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable

from .models import TraceResult
from .pages import PageFetcher
from .pipeline import run_trace
from .retrieval import SerpSearcher
from .store import StoredInvestigation, Store, StoreError, cache_key


@dataclass
class CachedTrace:
    """Internal service model. Person 2 consumes `.result`; the rest is bookkeeping."""

    result: TraceResult
    cache_hit: bool
    retrieved_at: datetime
    storage_backend: str
    saved: bool
    serp_requests: int = 0
    page_fetches: int = 0
    warnings: list[str] = field(default_factory=list)
    # Set only when the trace failed and `result` is the safe empty result.
    error: str | None = None


async def trace_with_cache(
    claim: str,
    store: Store,
    searcher: SerpSearcher,
    fetcher: PageFetcher,
    *,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> CachedTrace:
    key = cache_key(claim)  # raises ValueError for an empty claim
    warnings: list[str] = []
    try:
        hit = await asyncio.wait_for(asyncio.to_thread(store.find_cached, key), 2.0)
    except (StoreError, TimeoutError) as exc:
        hit = None
        warnings.append(f"cache lookup failed ({exc}); tracing live")
    if hit is not None:
        return CachedTrace(
            result=hit.result, cache_hit=True, retrieved_at=hit.retrieved_at,
            storage_backend=hit.backend or store.backend, saved=True, warnings=warnings,
        )

    retrieved_at = now()
    run = await run_trace(claim, searcher, fetcher)
    meta = {
        "queries": run.retrieval.queries, "serp_requests": run.serp_requests,
        "page_fetches": run.page_fetches, "raw_results": run.retrieval.raw_results,
        "dropped": run.retrieval.dropped, "warnings": run.warnings,
    }
    saved = True
    try:
        await asyncio.wait_for(asyncio.to_thread(store.save, StoredInvestigation(
            result=run.result, cache_key=key, claim=claim, retrieved_at=retrieved_at, meta=meta,
        )), 2.0)
    except (StoreError, TimeoutError) as exc:
        saved = False
        warnings.append(f"result not saved ({exc}); it will be traced again next time")
    return CachedTrace(
        result=run.result, cache_hit=False, retrieved_at=retrieved_at, storage_backend=store.backend,
        saved=saved, serp_requests=run.serp_requests, page_fetches=run.page_fetches,
        warnings=[*warnings, *run.warnings],
    )
