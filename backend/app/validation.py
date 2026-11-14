"""Output validation: no invented citations, no unsupported verdicts, no bad edges."""

import re

from app.models import AnalysisVerdict, GraphEdge, GraphNode, Source

VALID_STATUSES = frozenset(
    {"supported", "contradicted", "misleading", "insufficient_evidence"}
)
CITATION_GROUP = re.compile(r"\[([^\[\]]{1,60})\]")
CITATION_TOKEN = re.compile(r"^[A-Za-z]{1,3}\d{1,4}$")
URL_PATTERN = re.compile(r"(https?://|www\.)\S+", re.IGNORECASE)
SPACE_BEFORE_PUNCTUATION = re.compile(r"\s+([.,;:!?])")
MULTIPLE_SPACES = re.compile(r"\s{2,}")
MAX_EXPLANATION_CHARS = 1200
EVIDENCE_TEXT_FIELDS = ("excerpt", "hyperlink", "target_excerpt")

NO_VERDICT_EXPLANATION = (
    "The retrieved sources did not clearly support or contradict this claim, "
    "so no verdict is given. A lack of supporting sources does not mean the "
    "claim is false."
)


# Returns True only for absolute http(s) URLs.
def _is_web_url(url: str) -> bool:
    """Return True when the URL starts with http:// or https://."""
    return url.startswith(("http://", "https://"))


# Removes citation markers that do not match a retrieved source id.
def _strip_invalid_citations(
    explanation: str, allowed_ids: set[str]
) -> tuple[str, list[str]]:
    """Return the cleaned explanation and the valid ids it cites, in order."""
    cited_ids: list[str] = []

    # Rewrites one [..] group, keeping only tokens that are retrieved source ids.
    def replace_group(match: re.Match) -> str:
        """Return the bracket group with invalid citation tokens removed."""
        tokens = [token.strip() for token in match.group(1).split(",")]
        if not all(CITATION_TOKEN.match(token) for token in tokens):
            return match.group(0)
        kept_tokens = [token for token in tokens if token in allowed_ids]
        for token in kept_tokens:
            if token not in cited_ids:
                cited_ids.append(token)
        return f"[{', '.join(kept_tokens)}]" if kept_tokens else ""

    cleaned_text = CITATION_GROUP.sub(replace_group, explanation)
    return cleaned_text, cited_ids


# Enforces the status rules and citation rules on a model verdict.
def validate_verdict(
    verdict: AnalysisVerdict, sources: list[Source]
) -> AnalysisVerdict:
    """Return a verdict whose every citation matches a retrieved source."""
    allowed_ids = {source.id for source in sources}
    explanation = URL_PATTERN.sub("", verdict.explanation or "")
    explanation, cited_ids = _strip_invalid_citations(explanation, allowed_ids)
    explanation = SPACE_BEFORE_PUNCTUATION.sub(r"\1", explanation)
    explanation = MULTIPLE_SPACES.sub(" ", explanation).strip()
    explanation = explanation[:MAX_EXPLANATION_CHARS]

    status = verdict.status if verdict.status in VALID_STATUSES else None
    if status is None or (status != "insufficient_evidence" and not cited_ids):
        return AnalysisVerdict(
            status="insufficient_evidence",
            explanation=NO_VERDICT_EXPLANATION,
            cited_source_ids=[],
        )
    if not explanation:
        explanation = NO_VERDICT_EXPLANATION
    return AnalysisVerdict(
        status=status, explanation=explanation, cited_source_ids=cited_ids
    )


# Drops sources with duplicate ids/URLs or non-web URLs.
def clean_sources(sources: list[Source]) -> list[Source]:
    """Return sources with unique ids and URLs that the extension can open."""
    seen_ids: set[str] = set()
    seen_urls: set[str] = set()
    kept_sources = []
    for source in sources:
        if (
            not source.id
            or source.id in seen_ids
            or source.url in seen_urls
            or not _is_web_url(source.url)
        ):
            continue
        seen_ids.add(source.id)
        seen_urls.add(source.url)
        kept_sources.append(source)
    return kept_sources


# Drops graph nodes with duplicate ids or non-web URLs.
def clean_nodes(nodes: list[GraphNode]) -> list[GraphNode]:
    """Return nodes with unique ids and openable URLs."""
    seen_ids: set[str] = set()
    kept_nodes = []
    for node in nodes:
        if not node.id or node.id in seen_ids or not _is_web_url(node.url):
            continue
        seen_ids.add(node.id)
        kept_nodes.append(node)
    return kept_nodes


# Reports whether edge evidence (text or evidence object) has real content.
def _has_evidence(evidence: str | dict) -> bool:
    """Return True for non-blank text, or an object with a non-blank text field."""
    if isinstance(evidence, str):
        return bool(evidence.strip())
    return any(
        str(evidence.get(field_name) or "").strip()
        for field_name in EVIDENCE_TEXT_FIELDS
    )


# Drops edges that lack evidence, loop to themselves or point at missing nodes.
def clean_edges(edges: list[GraphEdge], nodes: list[GraphNode]) -> list[GraphEdge]:
    """Return only evidence-backed edges whose endpoints both exist."""
    node_ids = {node.id for node in nodes}
    seen_edges: set[tuple[str, str, str]] = set()
    kept_edges = []
    for edge in edges:
        edge_key = (edge.source_id, edge.target_id, edge.relationship_type)
        if (
            not _has_evidence(edge.evidence)
            or edge.source_id == edge.target_id
            or edge.source_id not in node_ids
            or edge.target_id not in node_ids
            or edge_key in seen_edges
        ):
            continue
        seen_edges.add(edge_key)
        if isinstance(edge.evidence, str):
            edge = edge.model_copy(update={"evidence": edge.evidence.strip()})
        kept_edges.append(edge)
    return kept_edges
