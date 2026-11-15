"""Google Fact Check Tools API client (claims:search)."""

import httpx

from app.models import FactCheckReview, Settings, Source, UpstreamError

FACTCHECK_ENDPOINT = "https://factchecktools.googleapis.com/v1alpha1/claims:search"
MAX_QUERY_CHARS = 300
MAX_REVIEWS = 8


# Returns True only for absolute http(s) URLs.
def _is_web_url(url: object) -> bool:
    """Return True when the value is a string starting with http:// or https://."""
    return isinstance(url, str) and url.startswith(("http://", "https://"))


# Searches Google Fact Check Tools for published reviews of a claim.
async def search_fact_checks(
    http_client: httpx.AsyncClient, settings: Settings, claim: str
) -> list[FactCheckReview]:
    """Return up to MAX_REVIEWS fact-check reviews matching the claim text."""
    if not settings.factcheck_api_key:
        raise UpstreamError("factcheck", "FACTCHECK_API_KEY is not set")
    try:
        response = await http_client.get(
            FACTCHECK_ENDPOINT,
            params={
                "query": claim[:MAX_QUERY_CHARS],
                "languageCode": "en",
                "pageSize": MAX_REVIEWS,
            },
            headers={"x-goog-api-key": settings.factcheck_api_key},
            timeout=settings.request_timeout_seconds,
        )
    except httpx.TimeoutException as error:
        raise UpstreamError("factcheck", "request timed out") from error
    except httpx.HTTPError as error:
        raise UpstreamError(
            "factcheck", f"network error: {type(error).__name__}"
        ) from error
    if response.status_code != 200:
        raise UpstreamError("factcheck", f"HTTP {response.status_code}")
    try:
        claims = response.json().get("claims", [])
    except (ValueError, AttributeError) as error:
        raise UpstreamError("factcheck", "malformed response") from error

    reviews: list[FactCheckReview] = []
    seen_urls: set[str] = set()
    for claim_entry in claims if isinstance(claims, list) else []:
        if not isinstance(claim_entry, dict):
            continue
        for review in claim_entry.get("claimReview", []) or []:
            review_url = review.get("url") if isinstance(review, dict) else None
            if not _is_web_url(review_url) or review_url in seen_urls:
                continue
            seen_urls.add(review_url)
            publisher = review.get("publisher") or {}
            reviews.append(
                FactCheckReview(
                    claim_text=str(claim_entry.get("text", "")).strip(),
                    claimant=str(claim_entry.get("claimant", "")).strip(),
                    publisher=str(
                        publisher.get("name") or publisher.get("site") or "Unknown"
                    ).strip(),
                    title=str(review.get("title") or "Fact-check").strip(),
                    url=review_url,
                    rating=str(review.get("textualRating", "")).strip(),
                    review_date=(review.get("reviewDate") or None),
                )
            )
            if len(reviews) >= MAX_REVIEWS:
                return reviews
    return reviews


# Converts fact-check reviews into contract sources with ids F1, F2, ...
def reviews_to_sources(reviews: list[FactCheckReview]) -> list[Source]:
    """Return one Source per review, with an excerpt naming publisher and rating."""
    sources = []
    for index, review in enumerate(reviews, start=1):
        rating_text = review.rating or "no rating given"
        claimant_text = f" (claimed by {review.claimant})" if review.claimant else ""
        sources.append(
            Source(
            evidence_kind="fact_check_summary",
                id=f"F{index}",
                title=review.title,
                url=review.url,
                date=review.review_date[:10] if review.review_date else None,
                excerpt=(
                    f'{review.publisher} fact-checked "{review.claim_text}"'
                    f"{claimant_text} and rated it: {rating_text}."
                ),
            )
        )
    return sources
