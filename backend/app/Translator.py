"""Translator: the only layer that converts between frontend JSON and backend data.

Inbound:  AnalyzeRequest (extension JSON)  -> AnalysisInput (sanitized)
Outbound: pipeline results                 -> AnalyzeResponse (API contract)
Errors:   error code + message             -> ErrorResponse JSON
"""

from app.models import (
    AnalysisInput,
    AnalysisVerdict,
    AnalyzeRequest,
    AnalyzeResponse,
    ClaimSource,
    ErrorResponse,
    GraphEdge,
    GraphNode,
    Source,
)
from app.platforms import detect_platform
from app.video import youtube_video_id
from app.sanitize import validate_claim_text, validate_page_url


# Converts the raw extension request into sanitized pipeline input.
def to_analysis_input(request_body: AnalyzeRequest) -> AnalysisInput:
    """Validate and clean an AnalyzeRequest, raising InputRejectedError if bad."""
    page_url = validate_page_url(request_body.url)
    platform = detect_platform(page_url)
    video_id = youtube_video_id(page_url) if platform == "YouTube" else None
    return AnalysisInput(
        text=validate_claim_text(request_body.text),
        url=page_url,
        mode=request_body.mode,
        platform=platform,
        video_id=video_id,
        video_time_seconds=request_body.video_time_seconds if video_id else None,
        video_duration_seconds=(
            request_body.video_duration_seconds if video_id else None
        ),
    )


# Assembles pipeline results into the response shape the extension expects.
def to_analyze_response(
    investigation_id: str,
    claim: str,
    verdict: AnalysisVerdict,
    sources: list[Source],
    earliest_observed_source: Source | None,
    graph_nodes: list[GraphNode],
    graph_edges: list[GraphEdge],
    claim_source: ClaimSource | None = None,
) -> AnalyzeResponse:
    """Return an AnalyzeResponse built from already-validated pipeline output."""
    return AnalyzeResponse(
        investigation_id=investigation_id,
        claim=claim,
        status=verdict.status,
        explanation=verdict.explanation,
        sources=sources,
        earliest_observed_source=earliest_observed_source,
        graph_nodes=graph_nodes,
        graph_edges=graph_edges,
        claim_source=claim_source,
        evidence_quotes=verdict.evidence_quotes,
        cited_source_ids=verdict.cited_source_ids,
    )


# Builds the JSON body used for every error response.
def to_error_body(error_code: str, message: str) -> dict:
    """Return a serializable ErrorResponse dict for the given code and message."""
    return ErrorResponse(error=error_code, message=message).model_dump()
