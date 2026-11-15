"""Claim -> up to 10 normalized, deduplicated sources with provenance."""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from .config import MAX_SOURCES, TARGET_SOURCES
from .dates import parse_reliable_date
from .models import Source
from .query import build_queries, build_coverage_query, keywords
from .serp_client import BudgetExhaustedError, SerpBudget, SerpError, SerpResponseError
from .urls import UnsafeUrlError, dedup_key, is_social_url, normalize_serp_url

MAX_TITLE_CHARS = 300
MAX_EXCERPT_CHARS = 600
# Slots kept for social posts so a full page of news results cannot crowd them out.
MAX_SOCIAL_SOURCES = 3


class SerpSearcher(Protocol):
    async def search(self, query: str, budget: SerpBudget) -> dict: ...


class Provenance(BaseModel):
    """Internal: where a source came from. Not part of Person 2's contract."""

    model_config = ConfigDict(extra="forbid")
    provider: str = "brightdata_serp_google"
    query: str
    rank: int | None
    original_url: str
    retrieved_at: datetime
    date_field: str | None = None  # which SERP field the date came from, if any


class RetrievedSource(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    title: str
    url: str
    date: date | None
    excerpt: str
    provenance: Provenance

    def to_source(self) -> Source:
        return Source(id=self.id, title=self.title, url=self.url, date=self.date, excerpt=self.excerpt)


class RetrievalResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sources: list[RetrievedSource]
    queries: list[str]
    serp_requests: int = Field(ge=0)
    raw_results: int = 0
    dropped: dict[str, int] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)


def _clean(text: object, limit: int) -> str:
    if not isinstance(text, str):
        return ""
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def parse_organic(payload: dict, *, query: str, retrieved_at: datetime) -> tuple[list[dict], int]:
    """Candidate rows from one parsed Google SERP. Returns (rows, rows_seen).

    Uses `link` as the URL (never `display_link`). A date is kept only from an
    explicit `date` field that parses as an absolute date; snippets and
    relative text like "2 days ago" are never mined for dates.
    """
    organic = payload.get("organic", [])
    if organic is None:
        organic = []
    if not isinstance(organic, list):
        raise SerpResponseError("`organic` was not a list")
    rows = []
    for entry in organic:
        if not isinstance(entry, dict):
            continue
        rank = entry.get("rank") if isinstance(entry.get("rank"), int) else None
        parsed_date = parse_reliable_date(entry.get("date"))
        rows.append({
            "title": _clean(entry.get("title"), MAX_TITLE_CHARS),
            "link": entry.get("link") if isinstance(entry.get("link"), str) else "",
            "excerpt": _clean(entry.get("description"), MAX_EXCERPT_CHARS),
            "date": parsed_date,
            "date_field": "date" if parsed_date else None,
            "rank": rank,
            "query": query,
            "retrieved_at": retrieved_at,
        })
    return rows, len(organic)


def _relevant(row: dict, claim_terms: set[str]) -> bool:
    if not claim_terms:
        return True
    text_terms = set(keywords(f"{row['title']} {row['excerpt']}"))
    needed = min(2, len(claim_terms))
    return len(claim_terms & text_terms) >= needed


def select_sources(
    rows: list[dict], claim: str, *, limit: int = MAX_SOURCES, social_slots: int = MAX_SOCIAL_SOURCES,
) -> tuple[list[RetrievedSource], dict[str, int]]:
    """Up to `limit` relevant, unique sources in row order; up to `social_slots` may be social posts."""
    dropped = {"empty": 0, "unsafe_url": 0, "duplicate": 0, "irrelevant": 0, "over_limit": 0}
    claim_terms = set(keywords(claim))
    seen: set[str] = set()
    candidates: list[tuple[dict, str]] = []
    for row in rows:
        if not row["title"] or not row["link"]:
            dropped["empty"] += 1
            continue
        try:
            url = normalize_serp_url(row["link"])
        except UnsafeUrlError:
            dropped["unsafe_url"] += 1
            continue
        key = dedup_key(url)
        if key in seen:
            dropped["duplicate"] += 1
            continue
        if not _relevant(row, claim_terms):
            dropped["irrelevant"] += 1
            continue
        seen.add(key)
        candidates.append((row, url))
    social = [c for c in candidates if is_social_url(c[1])][: min(social_slots, limit)]
    other = [c for c in candidates if not is_social_url(c[1])][: limit - len(social)]
    kept = {id(c) for c in social + other}
    chosen = [c for c in candidates if id(c) in kept]  # original (rank) order
    dropped["over_limit"] += len(candidates) - len(chosen)
    out = [
        RetrievedSource(
            id=f"s{index}",
            title=row["title"],
            url=url,
            date=row["date"],
            excerpt=row["excerpt"],
            provenance=Provenance(
                query=row["query"], rank=row["rank"], original_url=row["link"],
                retrieved_at=row["retrieved_at"], date_field=row["date_field"],
            ),
        )
        for index, (row, url) in enumerate(chosen, start=1)
    ]
    return out, {k: v for k, v in dropped.items() if v}


async def retrieve_sources(
    claim: str,
    searcher: SerpSearcher,
    *,
    budget: SerpBudget | None = None,
    max_sources: int = MAX_SOURCES,
    now=lambda: datetime.now(timezone.utc),
) -> RetrievalResult:
    """Run up to two SERP queries and return deduplicated sources.

    The second query broadens article coverage when fewer than seven relevant
    sources are found; otherwise it searches social posts with reserved slots. A failure on the second query keeps the first query's
    results and records a warning; a failure on the first is raised.
    """
    budget = budget or SerpBudget()
    max_sources = min(max_sources, MAX_SOURCES)
    queries = build_queries(claim)
    rows: list[dict] = []
    raw = 0
    ran: list[str] = []
    warnings: list[str] = []
    for i, query in enumerate(queries):
        if i > 0:
            if budget.remaining <= 0:
                warnings.append("second query skipped: SERP budget exhausted")
                break
        if i > 0:
            current, _ = select_sources(rows, claim, limit=max_sources)
            if len(current) < min(TARGET_SOURCES, max_sources):
                query = build_coverage_query(claim)
        try:
            payload = await searcher.search(query, budget)
            new_rows, seen = parse_organic(payload, query=query, retrieved_at=now())
        except (SerpError, BudgetExhaustedError) as exc:
            if i == 0:
                raise
            warnings.append(f"second query failed: {exc}")
            break
        ran.append(query)
        raw += seen
        rows.extend(new_rows)
    sources, dropped = select_sources(rows, claim, limit=max_sources)
    if not sources:
        warnings.append("no relevant sources found")
    elif len(sources) < min(TARGET_SOURCES, max_sources):
        warnings.append(f"Limited coverage: found {len(sources)} relevant sources; target is {min(TARGET_SOURCES, max_sources)}. No sources were invented to fill the board.")
    return RetrievalResult(
        sources=sources, queries=ran, serp_requests=budget.used,
        raw_results=raw, dropped=dropped, warnings=warnings,
    )
