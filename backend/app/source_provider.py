"""Integration point with source_tracing (Bright Data SERP, edge detection, Neon).

fetch_sources(claim, seed_url=None) -> SourceBundle
    Calls source_tracing.trace_claim_detailed: cache lookup in Neon, else Bright Data SERP
    + page fetches + evidence graph, saved to tt_investigations/tt_sources/tt_edges.
save_investigation(result) -> StorageMetadata
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
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from source_tracing import TraceResult, trace_claim_detailed  # noqa: E402
from source_tracing.service import build_store  # noqa: E402

from app.gemini_client import VIDEO_PROMPT_VERSION  # noqa: E402
from app.models import (  # noqa: E402
    AnalyzeResponse,
    EvidenceSearch,
    GraphEdge,
    GraphNode,
    Source,
    SourceBundle,
    SourceTraceMetadata,
    StorageMetadata,
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
async def fetch_sources(claim: str, seed_url: str | None = None) -> SourceBundle:
    """Return sources, graph nodes, edges and earliest source for a claim."""
    detailed = await trace_claim_detailed(claim, seed_url=seed_url)
    if detailed.error:
        logger.warning("Source trace failed: %s", detailed.error)
    payload = detailed.result.to_contract_dict()
    trace_id = payload.get("investigation_id") or ""
    earliest = payload.get("earliest_observed_source")
    return SourceBundle(
        source_trace=trace_metadata(detailed),
        sources=[Source(**source) for source in payload.get("sources", [])],
        graph_nodes=[GraphNode(**node) for node in payload.get("graph_nodes", [])],
        graph_edges=[GraphEdge(**edge) for edge in payload.get("graph_edges", [])],
        earliest_observed_source=Source(**earliest) if earliest else None,
        investigation_id=None if trace_id.startswith(UNSAVED_ID_PREFIXES) else trace_id,
    )


# Runs one explicit extra search through the same cached Bright Data pipeline.
async def search_evidence(query: str) -> EvidenceSearch:
    """Return what source_tracing finds for a query, with retrieval metadata.

    Uses trace_claim_detailed, so the query is cached and deduplicated exactly
    like a first investigation: repeating it costs no Bright Data requests.
    """
    trace = await trace_claim_detailed(query)
    payload = trace.result.to_contract_dict()
    return EvidenceSearch(
        bundle=SourceBundle(
            source_trace=trace_metadata(trace),
            sources=[Source(**source) for source in payload.get("sources", [])],
            graph_nodes=[GraphNode(**node) for node in payload.get("graph_nodes", [])],
            graph_edges=[GraphEdge(**edge) for edge in payload.get("graph_edges", [])],
        ),
        retrieved_at=trace.retrieved_at.isoformat(),
        from_cache=trace.cache_hit,
        error=trace.error,
    )


# Builds the store once per process; Neon when DATABASE_URL is set.
@lru_cache(maxsize=1)
def _analysis_store():
    """Return source_tracing's configured store (PostgresStore for Neon)."""
    return build_store()


# Records the final verdict in Neon so every analysis is queryable later.
async def save_investigation(result: AnalyzeResponse) -> StorageMetadata:
    """Persist a complete immutable response; report success only after the write."""
    store = _analysis_store()
    if not hasattr(store, "save_analysis"):
        return StorageMetadata(saved=False, storage_backend=store.backend)
    result.analysis_id = result.analysis_id or uuid.uuid4().hex
    saved = StorageMetadata(saved=True, storage_backend=store.backend,
                            saved_at=datetime.now(timezone.utc).isoformat())
    snapshot = result.model_copy(deep=True)
    snapshot.provenance.saved_analysis = saved
    await asyncio.to_thread(
        store.save_analysis, result.analysis_id, result.investigation_id,
        result.claim, result.status, result.explanation, snapshot.model_dump(mode="json"),
    )
    result.provenance.saved_analysis = saved
    return saved


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
        provenance={"cache_hit": True, "storage_backend": "neon_postgres",
                    "extracted_at": record["extracted_at"].isoformat() if record.get("extracted_at") else None,
                    "cached_at": record["created_at"].isoformat() if record.get("created_at") else None},
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
            "extracted_at": (extraction.provenance or {}).get("extracted_at"),
        },
    )


def trace_metadata(trace):
    return SourceTraceMetadata(
        cache_hit=trace.cache_hit,
        retrieved_at=(trace.retrieved_at.isoformat() if trace.retrieved_at and not trace.error
                      and trace.storage_backend != "mock" else None),
        storage_backend=trace.storage_backend,
        saved=trace.saved and trace.storage_backend not in {"none", "mock"},
        serp_requests=trace.serp_requests, page_fetches=trace.page_fetches,
        error="Source retrieval unavailable" if trace.error else None,
    )
