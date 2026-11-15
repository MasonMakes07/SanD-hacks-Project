"""Integration point with source_tracing (Bright Data SERP, edge detection, Neon).

fetch_sources(claim) -> SourceBundle
    Calls source_tracing.trace_claim: cache lookup in Neon, else Bright Data SERP
    + page fetches + evidence graph, saved to tt_investigations/tt_sources/tt_edges.
save_investigation(result) -> None
    Records the final verdict in Neon's tt_analyses table (pooled DATABASE_URL).
find_video_claim(video_id, window) / save_video_claim(video_id, window, claim)
    Neon cache of claims extracted from YouTube clips (tt_video_claims).

All are called by app.analysis, which contains any exception they raise.
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

from source_tracing import TraceResult, trace_claim_detailed  # noqa: E402
from source_tracing.service import build_store  # noqa: E402

from app.gemini_client import VIDEO_PROMPT_VERSION  # noqa: E402
from app.models import (  # noqa: E402
    AnalyzeResponse,
    GraphEdge,
    GraphNode,
    Source,
    SourceBundle,
    VideoClaimExtraction,
)

logger = logging.getLogger("truthtrace.sources")

UNSAVED_ID_PREFIXES = ("EMPTY-",)


# Traces a claim and logs why a result is empty, so source failures are visible.
async def trace_claim(claim: str) -> TraceResult:
    """Return source_tracing's result; log its error (e.g. Bright Data down)."""
    detailed = await trace_claim_detailed(claim)
    if detailed.error:
        logger.warning("Source trace failed: %s", detailed.error)
    return detailed.result


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


# Looks up a previously extracted claim for exactly this clip of a video.
async def find_video_claim(
    video_id: str, window: tuple[int, int]
) -> VideoClaimExtraction | None:
    """Return the Neon-cached claim for the clip, or None (also without Neon)."""
    store = _analysis_store()
    if not hasattr(store, "find_video_claim"):
        return None
    record = await asyncio.to_thread(
        store.find_video_claim, video_id, window[0], window[1], VIDEO_PROMPT_VERSION
    )
    if record is None:
        return None
    return VideoClaimExtraction(
        checkable=record["checkable"],
        claim=record["claim"],
        timestamp_seconds=record["timestamp_seconds"],
        quote=record["quote"],
        speaker=record["speaker"],
    )


# Caches a freshly extracted clip claim in Neon for later checks of the same clip.
async def save_video_claim(
    video_id: str, window: tuple[int, int], extraction: VideoClaimExtraction
) -> None:
    """Insert the clip claim into tt_video_claims; does nothing without Neon."""
    store = _analysis_store()
    if not hasattr(store, "save_video_claim"):
        return
    await asyncio.to_thread(
        store.save_video_claim,
        {
            "video_id": video_id,
            "window_start": window[0],
            "window_end": window[1],
            "prompt_version": VIDEO_PROMPT_VERSION,
            "checkable": extraction.checkable,
            "claim": extraction.claim,
            "timestamp_seconds": extraction.timestamp_seconds,
            "quote": extraction.quote,
            "speaker": extraction.speaker,
        },
    )
