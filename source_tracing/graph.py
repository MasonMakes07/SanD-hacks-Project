"""Evidence-backed source graph. Deterministic; no model, no network.

Edges are drawn only from what was observed in fetched pages:

explicit_citation  A -> B when the body of page A contains a hyperlink whose
                   normalized URL is page B (its retrieved, final or canonical
                   URL). A SERP snippet or a matching headline is never enough.

similar_wording    A -- B when at least one sentence of A and one of B (each
                   >= MIN_SENTENCE_TOKENS words, boilerplate excluded) share
                   >= SIMILARITY_THRESHOLD of their word 3-grams (Jaccard).
                   Undirected: stored with the lower source index first. It
                   says the wording overlaps, nothing about who wrote it first.
                   Social posts are never fetched (login walls); for them the
                   search-result snippet is the text compared, and the edge's
                   method and note say so.

same_fact_check    Not produced here. It needs fact-check identity from Person 2.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime

from .models import EdgeEvidence, GraphEdge, GraphNode, Source, node_for
from .pages import FetchedPage
from .urls import dedup_key

MIN_SENTENCE_TOKENS = 8
SHINGLE_SIZE = 3
SIMILARITY_THRESHOLD = 0.5
MAX_EXCERPT_CHARS = 400

CITATION_METHOD = "hyperlink_in_page_body"
CITATION_NOTE = "This page links to the other source. A link shows a reference, not agreement."
SIMILARITY_METHOD = (
    f"sentence_word_{SHINGLE_SIZE}gram_jaccard>={SIMILARITY_THRESHOLD}"
    f"_min_{MIN_SENTENCE_TOKENS}_words"
)
SIMILARITY_NOTE = (
    "Wording overlap only. It does not show that either source copied the other, "
    "or which was written first."
)
SNIPPET_NOTE = (
    "Wording overlap with a social post's search-result snippet; the post itself was not "
    "opened. It does not show copying, or which was written first."
)

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])[\"'”’)]*\s+(?=[\"'“‘(]*[A-Z0-9])")
_TOKEN = re.compile(r"[a-z0-9]+(?:['’][a-z]+)?")
_BOILERPLATE = re.compile(
    r"cookie|subscribe|newsletter|all rights reserved|privacy policy|terms of (use|service)"
    r"|sign up|sign in|log in|javascript|advertisement|copyright|click here|share this"
    r"|follow us|read more|related articles?",
    re.I,
)


@dataclass
class GraphResult:
    sources: list[Source]
    nodes: list[GraphNode]
    edges: list[GraphEdge]
    earliest_observed_source: Source | None
    warnings: list[str] = field(default_factory=list)


def _clip(text: str) -> str:
    text = " ".join(text.split())
    return text if len(text) <= MAX_EXCERPT_CHARS else text[: MAX_EXCERPT_CHARS - 1].rstrip() + "…"


def sentences(blocks: list[str]) -> list[str]:
    out: list[str] = []
    for block in blocks:
        out.extend(s.strip() for s in _SENTENCE_SPLIT.split(block) if s.strip())
    return out


def _shingles(sentence: str) -> frozenset[tuple[str, ...]] | None:
    tokens = _TOKEN.findall(sentence.lower())
    if len(tokens) < MIN_SENTENCE_TOKENS or _BOILERPLATE.search(sentence):
        return None
    return frozenset(tuple(tokens[i : i + SHINGLE_SIZE]) for i in range(len(tokens) - SHINGLE_SIZE + 1))


def sentence_similarity(a: str, b: str) -> float:
    """Jaccard overlap of word 3-grams; 0.0 if either sentence is too short or boilerplate."""
    sa, sb = _shingles(a), _shingles(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def best_sentence_match(blocks_a: list[str], blocks_b: list[str]) -> tuple[float, str, str, int] | None:
    """(score, sentence_a, sentence_b, matching_pair_count) for pairs at or above the threshold."""
    sents_b = [(s, sh) for s in sentences(blocks_b) if (sh := _shingles(s))]
    index: dict[tuple[str, ...], set[int]] = {}
    for j, (_, sh) in enumerate(sents_b):
        for gram in sh:
            index.setdefault(gram, set()).add(j)
    best: tuple[float, str, str] | None = None
    count = 0
    for sa in sentences(blocks_a):
        sha = _shingles(sa)
        if not sha:
            continue
        candidates = sorted({j for gram in sha for j in index.get(gram, ())})
        for j in candidates:
            sb, shb = sents_b[j]
            score = len(sha & shb) / len(sha | shb)
            if score >= SIMILARITY_THRESHOLD:
                count += 1
                if best is None or score > best[0]:
                    best = (score, sa, sb)
    return None if best is None else (*best, count)


def _page_keys(source: Source, page: FetchedPage | None) -> set[str]:
    keys = {dedup_key(source.url)}
    if page:
        keys.add(dedup_key(page.final_url))
        if page.parsed.canonical_url:
            keys.add(dedup_key(page.parsed.canonical_url))
    return keys


def _apply_page_dates(sources: list[Source], pages: dict[str, FetchedPage], warnings: list[str]) -> list[Source]:
    """Publisher-declared page metadata sets the date. Retrieval time never does."""
    out = []
    for s in sources:
        page = pages.get(s.id)
        new_date: date | None = s.date
        if page:
            if page.parsed.date_conflict:
                warnings.append(f"{s.id}: page declares conflicting publish dates; date cleared")
                new_date = None
            elif page.parsed.published:
                if s.date and s.date != page.parsed.published:
                    warnings.append(
                        f"{s.id}: search-result date {s.date} differs from page metadata "
                        f"{page.parsed.published}; using page metadata"
                    )
                new_date = page.parsed.published
        out.append(s if new_date == s.date else s.model_copy(update={"date": new_date}))
    return out


def earliest_observed(sources: list[Source]) -> Source | None:
    """Earliest dated source among those retrieved (ties: first listed). Not 'the origin'."""
    dated = [s for s in sources if s.date is not None]
    return min(dated, key=lambda s: s.date) if dated else None


def build_graph(
    sources: list[Source], fetched: list[FetchedPage], *, snippet_times: dict[str, datetime] | None = None,
) -> GraphResult:
    """`snippet_times` maps unfetched social source ids to when their search snippet was retrieved."""
    warnings: list[str] = []
    snippet_times = snippet_times or {}
    ids = [s.id for s in sources]
    if len(set(ids)) != len(ids):
        raise ValueError("source ids must be unique")
    pages = {p.source_id: p for p in fetched if p.source_id in set(ids)}
    sources = _apply_page_dates(sources, pages, warnings)
    by_id = {s.id: s for s in sources}
    order = {sid: i for i, sid in enumerate(ids)}
    keys = {s.id: _page_keys(s, pages.get(s.id)) for s in sources}

    # Sources that are the same document (shared URL / canonical) get no edges between them.
    same_doc: set[frozenset[str]] = set()
    for i, a in enumerate(ids):
        for b in ids[i + 1 :]:
            if keys[a] & keys[b]:
                same_doc.add(frozenset((a, b)))
                warnings.append(f"{a} and {b} are the same document (shared canonical URL); no edge drawn")

    edges: list[GraphEdge] = []

    # explicit_citation
    for a in ids:
        page = pages.get(a)
        if not page:
            continue
        cited: set[str] = set()
        for link in page.parsed.links:
            link_key = dedup_key(link.url)
            if link_key in keys[a]:
                continue  # self-link
            for b in ids:
                if b == a or b in cited or frozenset((a, b)) in same_doc or link_key not in keys[b]:
                    continue
                cited.add(b)
                edges.append(GraphEdge(
                    source_id=a, target_id=b, relationship_type="explicit_citation",
                    evidence=EdgeEvidence(
                        url=page.final_url, hyperlink=link.url,
                        anchor_text=_clip(link.anchor_text) or None,
                        excerpt=_clip(link.context) or None,
                        target_url=by_id[b].url, retrieved_at=page.retrieved_at,
                        method=CITATION_METHOD, note=CITATION_NOTE,
                    ),
                ))

    # similar_wording (each unordered pair once)
    fetched_ids = [sid for sid in ids if sid in pages]
    for i, a in enumerate(fetched_ids):
        for b in fetched_ids[i + 1 :]:
            if frozenset((a, b)) in same_doc:
                continue
            match = best_sentence_match(pages[a].parsed.blocks, pages[b].parsed.blocks)
            if not match:
                continue
            score, sent_a, sent_b, count = match
            edges.append(GraphEdge(
                source_id=a, target_id=b, relationship_type="similar_wording",
                evidence=EdgeEvidence(
                    url=pages[a].final_url, excerpt=_clip(sent_a),
                    target_url=pages[b].final_url, target_excerpt=_clip(sent_b),
                    similarity=round(score, 3),
                    retrieved_at=max(pages[a].retrieved_at, pages[b].retrieved_at),
                    method=f"{SIMILARITY_METHOD};matching_sentence_pairs={count}",
                    note=SIMILARITY_NOTE,
                ),
            ))

    # similar_wording involving a social post's snippet (pairs with at least one snippet side)
    snippet_ids = {sid for sid in ids if sid in snippet_times and sid not in pages and by_id[sid].excerpt}
    texts = {sid: pages[sid].parsed.blocks for sid in fetched_ids}
    texts.update({sid: [by_id[sid].excerpt] for sid in snippet_ids})
    seen_at = {sid: pages[sid].retrieved_at for sid in fetched_ids} | {sid: snippet_times[sid] for sid in snippet_ids}
    for i, a in enumerate(ids):
        for b in ids[i + 1 :]:
            if a not in texts or b not in texts or not ({a, b} & snippet_ids) or frozenset((a, b)) in same_doc:
                continue
            match = best_sentence_match(texts[a], texts[b])
            if not match:
                continue
            score, sent_a, sent_b, count = match
            edges.append(GraphEdge(
                source_id=a, target_id=b, relationship_type="similar_wording",
                evidence=EdgeEvidence(
                    url=pages[a].final_url if a in pages else by_id[a].url, excerpt=_clip(sent_a),
                    target_url=pages[b].final_url if b in pages else by_id[b].url, target_excerpt=_clip(sent_b),
                    similarity=round(score, 3), retrieved_at=max(seen_at[a], seen_at[b]),
                    method=f"{SIMILARITY_METHOD};serp_snippet;matching_sentence_pairs={count}",
                    note=SNIPPET_NOTE,
                ),
            ))

    edges.sort(key=lambda e: (order[e.source_id], order[e.target_id], e.relationship_type))
    nodes = [node_for(s) for s in sources]
    return GraphResult(
        sources=sources, nodes=nodes, edges=edges,
        earliest_observed_source=earliest_observed(sources), warnings=warnings,
    )
