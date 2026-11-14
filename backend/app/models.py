"""All TruthTrace backend classes: settings, API schemas, internal data, errors."""

from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Status = Literal["supported", "contradicted", "misleading", "insufficient_evidence"]
RelationshipType = Literal["explicit_citation", "similar_wording", "same_fact_check"]
Mode = Literal["manual", "automatic"]
NodeKind = Literal["article", "fact_check", "document"]


# ==============================================================================
# Settings
# ==============================================================================
@dataclass(frozen=True)
class Settings:
    """Runtime configuration read from environment variables."""

    gemini_api_key: str
    gemini_model: str
    factcheck_api_key: str
    truthtrace_api_key: str
    use_mock: bool
    allowed_origin_regex: str
    request_timeout_seconds: float
    analyze_timeout_seconds: float
    rate_limit_per_minute: int
    max_body_bytes: int


# ==============================================================================
# API request (frontend -> backend)
# ==============================================================================
class AnalyzeRequest(BaseModel):
    """Raw body of POST /api/analyze as sent by the extension."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    text: str = Field(min_length=1, max_length=20000)
    url: str = Field(min_length=1, max_length=2048)
    mode: Mode


# ==============================================================================
# API response (backend -> frontend)
# ==============================================================================
class Source(BaseModel):
    """One retrieved source; `id` is what explanation markers like [S1] refer to."""

    id: str
    title: str
    url: str
    date: str | None = None
    excerpt: str = ""


class GraphNode(BaseModel):
    """One article or document in the source network."""

    id: str
    title: str
    url: str
    date: str | None = None
    kind: NodeKind = "article"


class GraphEdge(BaseModel):
    """One evidence-backed connection between two graph nodes."""

    source_id: str
    target_id: str
    relationship_type: RelationshipType
    evidence: str


class AnalyzeResponse(BaseModel):
    """Full investigation result returned by POST /api/analyze."""

    investigation_id: str
    claim: str
    status: Status
    explanation: str
    sources: list[Source]
    earliest_observed_source: Source | None
    graph_nodes: list[GraphNode]
    graph_edges: list[GraphEdge]


class ErrorResponse(BaseModel):
    """Error body returned for every non-2xx response."""

    error: str
    message: str


# ==============================================================================
# Internal data
# ==============================================================================
@dataclass(frozen=True)
class AnalysisInput:
    """Sanitized, validated request ready for the analysis pipeline."""

    text: str
    url: str
    mode: Mode


@dataclass
class SourceBundle:
    """What the source provider (Person 3) hands back for a claim."""

    sources: list[Source] = field(default_factory=list)
    graph_nodes: list[GraphNode] = field(default_factory=list)
    graph_edges: list[GraphEdge] = field(default_factory=list)
    earliest_observed_source: Source | None = None


@dataclass(frozen=True)
class FactCheckReview:
    """One published fact-check from the Google Fact Check Tools API."""

    claim_text: str
    claimant: str
    publisher: str
    title: str
    url: str
    rating: str
    review_date: str | None


@dataclass(frozen=True)
class ClaimExtraction:
    """Result of asking Gemini for one checkable factual claim."""

    checkable: bool
    claim: str


@dataclass(frozen=True)
class AnalysisVerdict:
    """Status and explanation produced from retrieved sources only."""

    status: Status
    explanation: str
    cited_source_ids: list[str]


# ==============================================================================
# Errors
# ==============================================================================
class InputRejectedError(ValueError):
    """Raised when user input fails sanitization (e.g. it looks like code)."""


class UpstreamError(RuntimeError):
    """Raised when an external API fails, times out, or returns bad data."""

    def __init__(self, service: str, detail: str) -> None:
        super().__init__(f"{service}: {detail}")
        self.service = service
        self.detail = detail
