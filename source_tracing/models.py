"""Typed models for Person 3's tracing result.

Field names follow Plan.md section 4 (the shared /api/analyze response) and
PERSON3_CONTRACT.md. Person 2 owns `claim`, `status` and `explanation`; none of
those appear here, and nothing in this module expresses a truth verdict.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .urls import is_social_url

RelationshipType = Literal["explicit_citation", "similar_wording", "same_fact_check"]


def _require_http_url(value: str) -> str:
    parts = urlsplit(value)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise ValueError("must be an absolute http(s) URL")
    return value


class _Strict(BaseModel):
    # Unknown fields are rejected so the JSON handed to Person 2 cannot drift
    # from the agreed contract without a test failing.
    model_config = ConfigDict(extra="forbid")


class Source(_Strict):
    id: str = Field(min_length=1)
    title: str
    url: str
    # Publication date only when the retrieved data states it reliably.
    # Unknown or ambiguous dates are None, never guessed.
    date: date | None
    excerpt: str

    _check_url = field_validator("url")(_require_http_url)


class GraphNode(_Strict):
    id: str = Field(min_length=1)
    title: str
    url: str
    # "social_post" for posts on SOCIAL_HOSTS (seen only as search snippets).
    kind: Literal["article", "social_post"] = "article"

    _check_url = field_validator("url")(_require_http_url)


def node_for(source: Source) -> GraphNode:
    """The graph node for a source; its kind is derived from the URL, so cached results get it too."""
    kind = "social_post" if is_social_url(source.url) else "article"
    return GraphNode(id=source.id, title=source.title, url=source.url, kind=kind)


class EdgeEvidence(_Strict):
    """What was actually observed that justifies drawing an edge.

    explicit_citation: `url` is the citing page, `hyperlink` the link target
    seen in its body, `anchor_text` the link text, `excerpt` the surrounding text.
    similar_wording: `url`/`excerpt` quote one page, `target_url`/`target_excerpt`
    the other, `similarity` is the overlap score. The pair is undirected.
    """

    # Page on which the evidence was observed.
    url: str
    # Quoted text observed on that page.
    excerpt: str | None = None
    # Observed outbound link, for explicit citations.
    hyperlink: str | None = None
    anchor_text: str | None = None
    # The other page and its quoted text, for similar_wording.
    target_url: str | None = None
    target_excerpt: str | None = None
    # When the page at `url` was retrieved. Not a publication time.
    retrieved_at: datetime
    # 0..1 overlap measure for similar_wording; None when not applicable.
    similarity: float | None = Field(default=None, ge=0.0, le=1.0)
    # How the evidence was established, e.g. "hyperlink_in_page_body".
    method: str = Field(min_length=1)
    # Plain-language limit on what the edge means, safe to show to users.
    note: str | None = None

    _check_url = field_validator("url")(_require_http_url)

    @field_validator("hyperlink", "target_url")
    @classmethod
    def _check_optional_url(cls, value: str | None) -> str | None:
        return None if value is None else _require_http_url(value)

    @field_validator("retrieved_at")
    @classmethod
    def _require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("retrieved_at must be timezone-aware")
        return value


class GraphEdge(_Strict):
    source_id: str = Field(min_length=1)
    target_id: str = Field(min_length=1)
    relationship_type: RelationshipType
    evidence: EdgeEvidence

    @model_validator(mode="after")
    def _evidence_supports_relationship(self) -> GraphEdge:
        if self.source_id == self.target_id:
            raise ValueError("an edge cannot connect a source to itself")
        ev = self.evidence
        has_excerpt = bool(ev.excerpt and ev.excerpt.strip())
        if self.relationship_type == "explicit_citation" and ev.hyperlink is None:
            raise ValueError("explicit_citation requires an observed hyperlink")
        if self.relationship_type == "similar_wording" and not (
            has_excerpt
            and ev.similarity is not None
            and ev.target_url is not None
            and ev.target_excerpt
            and ev.target_excerpt.strip()
        ):
            raise ValueError(
                "similar_wording requires both quoted excerpts, target_url and a similarity"
            )
        if not has_excerpt and ev.hyperlink is None:
            raise ValueError("an edge needs an excerpt or a hyperlink as evidence")
        return self


class TraceResult(_Strict):
    investigation_id: str = Field(min_length=1)
    sources: list[Source]
    # Earliest source with a reliable date. This is "earliest observed", not
    # the original publisher. None when no source has a reliable date.
    earliest_observed_source: Source | None
    graph_nodes: list[GraphNode]
    graph_edges: list[GraphEdge]

    @model_validator(mode="after")
    def _internally_consistent(self) -> TraceResult:
        source_ids = [s.id for s in self.sources]
        if len(set(source_ids)) != len(source_ids):
            raise ValueError("source ids must be unique")
        node_ids = {n.id for n in self.graph_nodes}
        if len(node_ids) != len(self.graph_nodes):
            raise ValueError("graph node ids must be unique")
        for edge in self.graph_edges:
            missing = {edge.source_id, edge.target_id} - node_ids
            if missing:
                raise ValueError(f"edge references unknown node ids: {sorted(missing)}")
        earliest = self.earliest_observed_source
        if earliest is not None:
            if earliest.date is None:
                raise ValueError("earliest_observed_source must have a date")
            if earliest.id not in source_ids:
                raise ValueError("earliest_observed_source must be one of sources")
        return self

    def to_contract_dict(self) -> dict:
        """JSON-ready dict using exactly the contract field names."""
        return self.model_dump(mode="json")
