"""All TruthTrace backend classes: settings, API schemas, internal data, errors."""

from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Status = Literal["supported", "contradicted", "misleading", "insufficient_evidence"]
RelationshipType = Literal["explicit_citation", "similar_wording", "same_fact_check"]
Mode = Literal["manual", "automatic"]
NodeKind = Literal["article", "fact_check", "document", "social_post"]


# ==============================================================================
# Settings
# ==============================================================================
@dataclass(frozen=True)
class Settings:
    """Runtime configuration read from environment variables."""

    gemini_api_key: str
    gemini_model: str
    gemini_video_model: str
    gemini_fallback_model: str
    gemini_hedge_seconds: float
    video_timeout_seconds: float
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
    page_title: str | None = Field(default=None, max_length=300)
    # YouTube only: where the viewer is in the video, and its length.
    video_time_seconds: float | None = Field(default=None, ge=0, le=86400)
    video_duration_seconds: float | None = Field(default=None, gt=0, le=86400)


# ==============================================================================
# API response (backend -> frontend)
# ==============================================================================
class Passage(BaseModel):
    id: str
    text: str = Field(min_length=1, max_length=900)
    kind: Literal["fetched_article"] = "fetched_article"
    retrieved_at: str | None = None


class EvidenceQuote(BaseModel):
    source_id: str
    passage_id: str | None = None
    quote: str = Field(min_length=1, max_length=900)
    explanation: str = Field(default="", max_length=500)
    wording_verified: bool = False
    material_kind: str = "unknown"


class Source(BaseModel):
    """One retrieved source; `id` is what explanation markers like [S1] refer to."""

    id: str
    title: str
    url: str
    date: str | None = None
    excerpt: str = ""
    evidence_kind: Literal["unknown", "search_snippet", "social_snippet", "fetched_article", "fact_check_summary"] = "unknown"
    retrieved_at: str | None = None
    passages: list[Passage] = Field(default_factory=list, max_length=2)
    provenance: dict[str, Any] = Field(default_factory=dict)


class GraphNode(BaseModel):
    """One article or document in the source network."""

    id: str
    title: str
    url: str
    date: str | None = None
    kind: NodeKind = "article"


class GraphEdge(BaseModel):
    """One evidence-backed connection between two graph nodes.

    `evidence` is a short string, or Person 3's evidence object (url, excerpt,
    hyperlink, anchor_text, target_url, target_excerpt, similarity, method, note).
    """

    source_id: str
    target_id: str
    relationship_type: RelationshipType
    evidence: str | dict[str, Any]


class ClaimSource(BaseModel):
    """Where in a video the checked claim was made (YouTube checks only)."""

    type: Literal["video"] = "video"
    video_url: str
    timestamp: str | None
    timestamp_seconds: int | None
    timestamp_url: str
    quote: str
    speaker: str
    provenance: dict[str, Any] | None = None


class SourceTraceMetadata(BaseModel):
    cache_hit: bool | None = None
    retrieved_at: str | None = None
    storage_backend: str = "unknown"
    saved: bool | None = None
    serp_requests: int | None = Field(default=None, ge=0, le=2)
    page_fetches: int | None = Field(default=None, ge=0, le=5)
    error: str | None = None


class StorageMetadata(BaseModel):
    saved: bool | None = None
    storage_backend: str = "unknown"
    saved_at: str | None = None


class AnalysisProvenance(BaseModel):
    source_trace: SourceTraceMetadata | None = None
    video_extraction: dict[str, Any] | None = None
    saved_analysis: StorageMetadata = Field(default_factory=StorageMetadata)


class PageContext(BaseModel):
    url: str
    text: str = Field(default="", max_length=20000)
    title: str | None = None
    mode: Mode = "manual"
    platform: str | None = None
    video_time_seconds: float | None = None
    video_duration_seconds: float | None = None


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
    claim_source: ClaimSource | None = None
    analysis_id: str | None = None
    page_context: PageContext | None = None
    reopened: bool = False
    reassessment: dict[str, Any] | None = None
    checked_at: str | None = None
    provenance: AnalysisProvenance = Field(default_factory=AnalysisProvenance)
    evidence_quotes: list[EvidenceQuote] = Field(default_factory=list, max_length=8)
    cited_source_ids: list[str] = Field(default_factory=list)


# ==============================================================================
# Ask the Detective (chat + explicit evidence searches)
# ==============================================================================
SearchMode = Literal["more", "counter"]
Stance = Literal["supports", "contradicts", "unclear"]
# What an answer rests on: retrieved sources, the checked page, or model background.
AnswerBasis = Literal["evidence", "page", "background"]
SOURCE_ID_PATTERN = r"^[A-Za-z]{1,3}\d{1,4}$"


class ChatSource(BaseModel):
    """One source the extension already holds for this investigation."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    id: str = Field(pattern=SOURCE_ID_PATTERN)
    title: str = Field(max_length=400)
    url: str = Field(min_length=1, max_length=2048)
    date: str | None = Field(default=None, max_length=40)
    excerpt: str = Field(default="", max_length=1200)


class ChatEdge(BaseModel):
    """One relationship the backend previously established between two sources."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    source_id: str = Field(pattern=SOURCE_ID_PATTERN)
    target_id: str = Field(pattern=SOURCE_ID_PATTERN)
    relationship_type: RelationshipType


class ChatTurn(BaseModel):
    """One earlier message in this conversation."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    role: Literal["user", "detective"]
    text: str = Field(min_length=1, max_length=1700)


class ChatRequest(BaseModel):
    """Body of POST /api/chat: a question plus the investigation it is about."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    question: str = Field(min_length=1, max_length=500)
    claim: str = Field(min_length=1, max_length=600)
    status: Status
    explanation: str = Field(default="", max_length=2000)
    sources: list[ChatSource] = Field(default_factory=list, max_length=40)
    edges: list[ChatEdge] = Field(default_factory=list, max_length=80)
    history: list[ChatTurn] = Field(default_factory=list, max_length=8)
    page_text: str = Field(default="", max_length=8000)


class SourceStance(BaseModel):
    """How one cited source bears on the claim, according to the detective."""

    source_id: str
    stance: Stance


class ChatResponse(BaseModel):
    """Answer whose every [id] marker matches a source that was sent in."""

    answer: str
    cited_source_ids: list[str]
    stances: list[SourceStance]
    grounded: bool
    basis: AnswerBasis


class KnownSource(BaseModel):
    """A source already in the investigation, so a search can skip or link it."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    id: str = Field(pattern=SOURCE_ID_PATTERN)
    url: str = Field(min_length=1, max_length=2048)


class EvidenceSearchRequest(BaseModel):
    """Body of POST /api/evidence/search: one explicit, credit-spending search."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    claim: str = Field(min_length=1, max_length=600)
    mode: SearchMode
    known: list[KnownSource] = Field(default_factory=list, max_length=60)


class EvidenceSearchResponse(BaseModel):
    """Newly retrieved sources, with ids that cannot collide with existing ones."""

    mode: SearchMode
    retrieved_at: str
    from_cache: bool
    sources: list[Source]
    graph_nodes: list[GraphNode]
    graph_edges: list[GraphEdge]
    already_known: int
    evidence_set_id: str | None = None


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
    platform: str | None = None
    page_title: str | None = None
    video_id: str | None = None
    video_time_seconds: float | None = None
    video_duration_seconds: float | None = None


@dataclass
class SourceBundle:
    """What the source provider (Person 3) hands back for a claim."""

    sources: list[Source] = field(default_factory=list)
    graph_nodes: list[GraphNode] = field(default_factory=list)
    graph_edges: list[GraphEdge] = field(default_factory=list)
    earliest_observed_source: Source | None = None
    investigation_id: str | None = None
    source_trace: SourceTraceMetadata | None = None


@dataclass
class EvidenceSearch:
    """A SourceBundle from one explicit search, plus when and how it was retrieved."""

    bundle: SourceBundle
    retrieved_at: str
    from_cache: bool
    error: str | None = None


@dataclass(frozen=True)
class ChatAnswer:
    """Raw detective answer from Gemini; callers must validate its citations."""

    answer: str
    stances: list[tuple[str, str]]
    basis: str = "evidence"


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
class VideoClaimExtraction:
    """One claim Gemini found in a clip of a video, with where it was made."""

    checkable: bool
    claim: str
    timestamp_seconds: int | None
    quote: str
    speaker: str
    provenance: dict[str, Any] | None = None


@dataclass(frozen=True)
class AnalysisVerdict:
    """Status and explanation produced from retrieved sources only."""

    status: Status
    explanation: str
    cited_source_ids: list[str]
    evidence_quotes: list[EvidenceQuote] = field(default_factory=list)


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


class PinnedEvidenceSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    evidence_set_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    source_ids: list[str] = Field(min_length=1, max_length=8)


class ReassessRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    analysis_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    claim: str = Field(min_length=1, max_length=600)
    pinned_evidence: list[PinnedEvidenceSelection] = Field(min_length=1, max_length=4)
