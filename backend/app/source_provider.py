"""Integration point with source_tracing (Bright Data SERP, edge detection, Neon).

fetch_sources(claim) -> SourceBundle
    Calls source_tracing.trace_claim: cache lookup in Neon, else Bright Data SERP
    + page fetches + evidence graph, saved to tt_investigations/tt_sources/tt_edges.
save_investigation(result) -> None
    Records the final verdict in Neon's tt_analyses table (pooled DATABASE_URL).

Both are called by app.analysis, which contains any exception they raise.
"""

import asyncio
import logging
import sys
import uuid
from functools import lru_cache
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from source_tracing import trace_claim  # noqa: E402
from source_tracing.service import build_store  # noqa: E402

from app.models import (  # noqa: E402
    AnalyzeResponse,
    GraphEdge,
    GraphNode,
    Source,
    SourceBundle,
)

logger = logging.getLogger("truthtrace.sources")

UNSAVED_ID_PREFIXES = ("EMPTY-",)


# Converts one traced claim into the backend's SourceBundle.
async def fetch_sources(claim: str) -> SourceBundle:
    """Return sources, graph nodes, edges and earliest source for a claim."""
    payload = (await trace_claim(claim)).to_contract_dict()
    trace_id = payload.get("investigation_id") or ""
    earliest = payload.get("earliest_observed_source")
    return SourceBundle(
        sources=[Source(**source) for source in payload.get("sources", [])],
        graph_nodes=[GraphNode(**node) for node in payload.get("graph_nodes", [])],
        graph_edges=[GraphEdge(**edge) for edge in payload.get("graph_edges", [])],
        earliest_observed_source=Source(**earliest) if earliest else None,
        investigation_id=None if trace_id.startswith(UNSAVED_ID_PREFIXES) else trace_id,
    )


# Builds the store once per process; Neon when DATABASE_URL is set.
@lru_cache(maxsize=1)
def _analysis_store():
    """Return source_tracing's configured store (PostgresStore for Neon)."""
    return build_store()


# Records the final verdict in Neon so every analysis is queryable later.
async def save_investigation(result: AnalyzeResponse) -> None:
    """Insert the result into tt_analyses; does nothing without Neon configured."""
    store = _analysis_store()
    if not hasattr(store, "save_analysis"):
        logger.info("No Neon store configured (%s); analysis not saved.", store.backend)
        return
    await asyncio.to_thread(
        store.save_analysis,
        uuid.uuid4().hex,
        result.investigation_id,
        result.claim,
        result.status,
        result.explanation,
        result.model_dump(mode="json"),
    )
