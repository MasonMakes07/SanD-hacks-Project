"""Search queries from a claim. At most two, so at most two SERP calls.

1. the claim itself (news and reference pages)
2. its keywords restricted to social sites, to find posts repeating the claim
Retrieval substitutes a broader article query when primary coverage is sparse.
"""

from __future__ import annotations

import re

from .config import MAX_SERP_REQUESTS

MAX_QUERY_WORDS = 30  # Google ignores words past 32
MAX_KEYWORDS = 8
SOCIAL_SEARCH_SITES = ("x.com", "reddit.com", "threads.net", "bsky.app", "facebook.com")
SOCIAL_FILTER = "(" + " OR ".join(f"site:{site}" for site in SOCIAL_SEARCH_SITES) + ")"

STOPWORDS = frozenset(
    """a about above after again against all also am an and any are as at be because been
    before being below between both but by can could did do does doing down during each few
    for from further had has have having he her here hers him his how i if in into is it its
    itself just me more most my no nor not now of off on once only or other our ours out over
    own said same says she should so some such than that the their theirs them then there
    these they this those through to too under until up very was we were what when where which
    while who whom why will with would you your yours according reportedly""".split()
)
_WORD = re.compile(r"[A-Za-z0-9][A-Za-z0-9'%$.-]*")


def tokens(text: str) -> list[str]:
    return [w.strip(".'-").lower() for w in _WORD.findall(text or "")]


def keywords(text: str) -> list[str]:
    seen: list[str] = []
    for w in tokens(text):
        if len(w) >= 3 and w not in STOPWORDS and w not in seen:
            seen.append(w)
    return seen


def build_queries(claim: str) -> list[str]:
    """[full claim, social form]: the claim's keywords limited to SOCIAL_SEARCH_SITES."""
    cleaned = " ".join(claim.replace('"', " ").replace("“", " ").replace("”", " ").split())
    if not cleaned:
        raise ValueError("claim must contain words")
    primary = " ".join(cleaned.split()[:MAX_QUERY_WORDS])
    terms = " ".join(keywords(cleaned)[:MAX_KEYWORDS]) or " ".join(cleaned.split()[:MAX_KEYWORDS])
    return [primary, f"{terms} {SOCIAL_FILTER}"][:MAX_SERP_REQUESTS]


def build_coverage_query(claim: str) -> str:
    """Shorten a sparse full-sentence search to its key entities and topic."""
    terms = keywords(claim)[:6]
    return " ".join(terms) or build_queries(claim)[0]
