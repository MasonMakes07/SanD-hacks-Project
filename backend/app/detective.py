"""Ask the Detective: grounded follow-up chat and explicit extra evidence searches.

Chat is stateless. The extension sends the page text and the investigation it
already holds, and may ask open questions about either; this
module treats all of it as untrusted data, asks Gemini, then keeps only
citations that match a source that was sent in. It never changes a verdict.

Evidence searches reuse source_tracing (Bright Data SERP, normalization, Neon
cache, deduplication) through app.source_provider. Nothing here talks to Bright
Data directly.
"""

import logging
import re

import httpx

from app import source_provider
from app.gemini_client import answer_question
from app.models import (
    ChatRequest,
    ChatResponse,
    EvidenceSearchRequest,
    EvidenceSearchResponse,
    GraphEdge,
    GraphNode,
    InputRejectedError,
    Settings,
    Source,
    SourceStance,
    UpstreamError,
)
from app.sanitize import clean_text, looks_like_code, validate_claim_text
from app.validation import (
    CITATION_GROUP,
    MULTIPLE_SPACES,
    SPACE_BEFORE_PUNCTUATION,
    URL_PATTERN,
    _strip_invalid_citations,
    clean_edges,
    clean_nodes,
    clean_sources,
)

logger = logging.getLogger("truthtrace.detective")

MAX_ANSWER_CHARS = 1600
MAX_EXCERPT_CHARS_FOR_CHAT = 600
VALID_STANCES = frozenset({"supports", "contradicts", "unclear"})
VALID_BASES = frozenset({"evidence", "page", "background"})
# New ids never collide with the first investigation's S/F ids.
ID_PREFIX_BY_MODE = {"more": "m", "counter": "c"}
MAX_NEW_SOURCES = 8
# Counter-evidence terms steer the same search pipeline toward rebuttals.
QUERY_SUFFIX_BY_MODE = {
    "more": "evidence report",
    "counter": "false OR debunked OR misleading fact check",
}
TRAILING_PUNCTUATION = re.compile(r"[\s.!?]+$")
# Bracketed labels the model sometimes writes for things that are not sources.
PSEUDO_CITATION = re.compile(
    r"\s*\[(?:the\s+)?(?:page(?:[ _]text)?|background|article|text|source|sources)\]",
    re.IGNORECASE,
)

NO_ANSWER = (
    "I could not form an answer from the evidence on this board. Try a more "
    "specific question, or search for more evidence."
)
MOCK_ANSWER = (
    "This backend is in mock mode, so there is no real evidence to question. "
    "Turn off USE_MOCK to ask about a live investigation."
)


class EvidenceSearchError(RuntimeError):
    """Raised when the extra search could not run (credentials, Bright Data, storage)."""


# Cleans one piece of user-supplied chat text and rejects code-like input.
def _clean_user_text(raw_text: str, field_name: str) -> str:
    """Return cleaned text, raising InputRejectedError if it is unusable."""
    text = clean_text(raw_text)
    if not text:
        raise InputRejectedError(f"{field_name} is empty after cleaning.")
    if looks_like_code(text):
        raise InputRejectedError(f"{field_name} looks like code and was rejected.")
    return text


# Turns a bare mention of a known source id ("s1 and s2") into a citation marker.
def _bracket_bare_ids(answer: str, ids_by_lowercase: dict[str, str]) -> str:
    """Return the answer with known ids outside [..] groups wrapped in brackets."""
    if not ids_by_lowercase:
        return answer
    bare_id = re.compile(
        r"(?<![\w\[])("
        + "|".join(re.escape(source_id) for source_id in ids_by_lowercase)
        + r")(?![\w\]])",
        re.IGNORECASE,
    )
    pieces: list[str] = []
    position = 0
    for group in CITATION_GROUP.finditer(answer):
        pieces.append(bare_id.sub(r"[\1]", answer[position : group.start()]))
        pieces.append(group.group(0))
        position = group.end()
    pieces.append(bare_id.sub(r"[\1]", answer[position:]))
    return "".join(pieces)


# Answers one question about an investigation, citing only sources sent in.
async def answer_chat(
    http_client: httpx.AsyncClient, settings: Settings, request_body: ChatRequest
) -> ChatResponse:
    """Return a validated answer; raises UpstreamError if Gemini is unavailable."""
    question = _clean_user_text(request_body.question, "Question")
    claim = validate_claim_text(request_body.claim)
    sources = clean_sources(
        [Source(**source.model_dump()) for source in request_body.sources]
    )
    source_ids = {source.id for source in sources}
    investigation = {
        "claim": claim,
        "existing_verdict": request_body.status,
        "existing_explanation": clean_text(request_body.explanation),
        "sources": [
            {
                "id": source.id,
                "title": clean_text(source.title),
                "publisher": httpx.URL(source.url).host,
                "date": source.date,
                "excerpt": clean_text(source.excerpt)[:MAX_EXCERPT_CHARS_FOR_CHAT],
            }
            for source in sources
        ],
        "relationships": [
            edge.model_dump()
            for edge in request_body.edges
            if edge.source_id in source_ids and edge.target_id in source_ids
        ],
    }
    conversation = [
        {"role": turn.role, "text": clean_text(turn.text)}
        for turn in request_body.history
    ]
    raw_answer = await answer_question(
        http_client,
        settings,
        investigation,
        conversation,
        question,
        clean_text(request_body.page_text),
    )

    ids_by_lowercase: dict[str, str] = {}
    for source in sources:
        ids_by_lowercase.setdefault(source.id.lower(), source.id)
    answer = URL_PATTERN.sub("", raw_answer.answer)
    answer = PSEUDO_CITATION.sub("", answer)
    answer = _bracket_bare_ids(answer, ids_by_lowercase)
    answer, cited_ids = _strip_invalid_citations(answer, ids_by_lowercase)
    answer = SPACE_BEFORE_PUNCTUATION.sub(r"\1", answer)
    answer = MULTIPLE_SPACES.sub(" ", answer).strip()[:MAX_ANSWER_CHARS]
    stances: list[SourceStance] = []
    for source_id, stance in raw_answer.stances:
        canonical_id = ids_by_lowercase.get(source_id.lower())
        if (
            canonical_id in cited_ids
            and stance in VALID_STANCES
            and all(item.source_id != canonical_id for item in stances)
        ):
            stances.append(SourceStance(source_id=canonical_id, stance=stance))
    return ChatResponse(
        answer=answer or NO_ANSWER,
        cited_source_ids=cited_ids if answer else [],
        stances=stances if answer else [],
        grounded=bool(answer and cited_ids),
        basis=raw_answer.basis
        if answer and raw_answer.basis in VALID_BASES
        else "evidence",
    )


# Returns the fixed reply used when USE_MOCK is on.
def build_mock_chat_response() -> ChatResponse:
    """Return an uncited, clearly labelled mock answer."""
    return ChatResponse(
        answer=MOCK_ANSWER,
        cited_source_ids=[],
        stances=[],
        grounded=False,
        basis="evidence",
    )


# Builds the search text for a mode from the claim.
def build_search_query(claim: str, mode: str) -> str:
    """Return the claim plus the mode's steering terms."""
    return f"{TRAILING_PUNCTUATION.sub('', claim)} {QUERY_SUFFIX_BY_MODE[mode]}"


# Runs one explicit search and returns only sources the investigation lacks.
async def search_more_evidence(
    request_body: EvidenceSearchRequest,
) -> EvidenceSearchResponse:
    """Return new sources with fresh ids, plus edges the tracer established."""
    claim = validate_claim_text(request_body.claim)
    search = await source_provider.search_evidence(
        build_search_query(claim, request_body.mode)
    )
    if search.error:
        logger.warning("Evidence search failed: %s", search.error)
        raise EvidenceSearchError(search.error)

    known_id_by_url = {known.url: known.id for known in request_body.known}
    prefix = ID_PREFIX_BY_MODE[request_body.mode]
    taken_ids = {known.id.lower() for known in request_body.known}
    new_id_by_old: dict[str, str] = {}
    known_id_by_old: dict[str, str] = {}
    new_sources: list[Source] = []
    next_number = 1
    for source in clean_sources(search.bundle.sources):
        if source.url in known_id_by_url:
            known_id_by_old[source.id] = known_id_by_url[source.url]
            continue
        if len(new_sources) >= MAX_NEW_SOURCES:
            continue
        while f"{prefix}{next_number}" in taken_ids:
            next_number += 1
        new_id = f"{prefix}{next_number}"
        next_number += 1
        new_id_by_old[source.id] = new_id
        new_sources.append(source.model_copy(update={"id": new_id}))

    new_nodes = clean_nodes(
        [
            node.model_copy(update={"id": new_id_by_old[node.id]})
            for node in search.bundle.graph_nodes
            if node.id in new_id_by_old
        ]
    )
    # Keep an edge only if it touches a new source and both ends are on the board
    # (new, or already known). Edges between two known sources are not re-sent.
    final_id_by_old = {**known_id_by_old, **new_id_by_old}
    remapped_edges = [
        edge.model_copy(
            update={
                "source_id": final_id_by_old[edge.source_id],
                "target_id": final_id_by_old[edge.target_id],
            }
        )
        for edge in search.bundle.graph_edges
        if edge.source_id in final_id_by_old
        and edge.target_id in final_id_by_old
        and (edge.source_id in new_id_by_old or edge.target_id in new_id_by_old)
    ]
    endpoint_nodes = new_nodes + [
        GraphNode(id=known.id, title="", url=known.url) for known in request_body.known
    ]
    return EvidenceSearchResponse(
        mode=request_body.mode,
        retrieved_at=search.retrieved_at,
        from_cache=search.from_cache,
        sources=new_sources,
        graph_nodes=new_nodes,
        graph_edges=clean_edges(remapped_edges, endpoint_nodes),
        already_known=len(known_id_by_old),
    )
