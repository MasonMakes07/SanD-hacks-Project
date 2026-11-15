"""Investigation pipeline: extract claim -> retrieve evidence -> analyze -> validate."""

import asyncio
import logging
import re
import uuid
from collections import OrderedDict
from dataclasses import replace
from datetime import datetime, timezone

import httpx

from app import source_provider
from app.factcheck_client import reviews_to_sources, search_fact_checks
from app.gemini_client import analyze_claim, extract_claim, extract_video_claim
from app.models import (
    AnalysisInput,
    AnalysisVerdict,
    AnalyzeResponse,
    ClaimSource,
    FactCheckReview,
    GraphNode,
    Settings,
    Source,
    SourceBundle,
    PageContext,
    StorageMetadata,
    UpstreamError,
    VideoClaimExtraction,
)
from app.Translator import to_analyze_response
from app.validation import (
    clean_edges,
    clean_nodes,
    clean_sources,
    validate_verdict,
)
from app.video import (
    DEFAULT_CLIP_SECONDS,
    clip_window,
    format_timestamp,
    timestamp_url,
    watch_url,
)

logger = logging.getLogger("truthtrace.analysis")

VIDEO_CACHE_SIZE = 256
NEON_LOOKUP_TIMEOUT_SECONDS = 2.0
VideoKey = tuple[str, int, int]
_video_claim_cache: OrderedDict[VideoKey, VideoClaimExtraction] = OrderedDict()
_background_tasks: set[asyncio.Task] = set()

MAX_FALLBACK_CLAIM_CHARS = 400
MIN_FALLBACK_CLAIM_CHARS = 20
CLAIM_PREVIEW_CHARS = 200
MIN_CLAIM_WORD_OVERLAP = 0.5
SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")
WORD_PATTERN = re.compile(r"[a-z0-9]+")
SOCIAL_NOISE = re.compile(
    r"https?://\S+|^\s*RT\s+@\w+:?|@\w+|#\w+|\b\d{1,2}/\d{1,2}\s*$"
)
WHITESPACE_RUNS = re.compile(r"\s+")

NO_CLAIM_EXPLANATION = (
    "No specific, checkable factual claim was found in this text, so it was "
    "not checked. Try selecting a single sentence that states a fact."
)
NO_SOURCES_EXPLANATION = (
    "No existing fact-checks or relevant sources were found for this claim. "
    "That is not evidence that the claim is false; it could not be verified."
)
ANALYSIS_UNAVAILABLE_NOTE = (
    "Automated analysis was unavailable, so this result relies only on "
    "published fact-checks."
)

RATING_PHRASES: list[tuple[str, str]] = [
    ("mostly false", "misleading"),
    ("partly false", "misleading"),
    ("half true", "misleading"),
    ("misleading", "misleading"),
    ("missing context", "misleading"),
    ("lacks context", "misleading"),
    ("out of context", "misleading"),
    ("exaggerated", "misleading"),
    ("false", "contradicted"),
    ("pants on fire", "contradicted"),
    ("fake", "contradicted"),
    ("incorrect", "contradicted"),
    ("not true", "contradicted"),
    ("wrong", "contradicted"),
    ("fabricated", "contradicted"),
    ("four pinocchios", "contradicted"),
    ("true", "supported"),
    ("correct", "supported"),
    ("accurate", "supported"),
]
STATUS_LABELS = {
    "supported": "true",
    "contradicted": "false",
    "misleading": "misleading or missing context",
}


# Removes links, @mentions, hashtags, "RT" and thread counters from a post.
def _strip_social_noise(post_text: str) -> str:
    """Return the post text with social-media decoration removed."""
    return WHITESPACE_RUNS.sub(" ", SOCIAL_NOISE.sub(" ", post_text)).strip()


# Picks a usable claim from the raw text when Gemini extraction is unavailable.
def _fallback_claim(analysis_input: AnalysisInput) -> str:
    """Return the selection itself if short, else its first full sentence."""
    text = analysis_input.text.replace("\n", " ")
    if analysis_input.platform:
        text = _strip_social_noise(text)
    if analysis_input.mode == "manual" and len(text) <= MAX_FALLBACK_CLAIM_CHARS:
        return text
    for sentence in SENTENCE_SPLIT.split(text):
        if MIN_FALLBACK_CLAIM_CHARS <= len(sentence) <= MAX_FALLBACK_CLAIM_CHARS:
            return sentence
    return ""


# Runs a coroutine after the response is sent, keeping a reference until it ends.
def _run_in_background(coroutine) -> None:
    """Schedule the coroutine; its task is tracked so it is not garbage collected."""
    task = asyncio.create_task(coroutine)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


# Waits for every background write (used by tests and on shutdown).
async def drain_background_tasks() -> None:
    """Return once all scheduled background tasks have finished."""
    while _background_tasks:
        await asyncio.gather(*list(_background_tasks), return_exceptions=True)


# Stores a clip claim in the in-process LRU cache.
def _remember_video_claim(key: VideoKey, extraction: VideoClaimExtraction) -> None:
    """Insert or refresh the entry, evicting the least recently used one."""
    _video_claim_cache[key] = extraction
    _video_claim_cache.move_to_end(key)
    while len(_video_claim_cache) > VIDEO_CACHE_SIZE:
        _video_claim_cache.popitem(last=False)


# Looks for a clip claim in memory first, then in Neon (bounded wait).
async def _cached_video_claim(key: VideoKey) -> VideoClaimExtraction | None:
    """Return a cached clip claim, or None; Neon problems count as a miss."""
    if key in _video_claim_cache:
        _video_claim_cache.move_to_end(key)
        return replace(_video_claim_cache[key], provenance={
            **(_video_claim_cache[key].provenance or {}), "cache_hit": True,
            "storage_backend": "memory"})
    video_id, start, end = key
    try:
        cached = await asyncio.wait_for(
            source_provider.find_video_claim(video_id, (start, end)),
            NEON_LOOKUP_TIMEOUT_SECONDS,
        )
    except Exception as error:  # noqa: BLE001 - a cache must never break a check
        logger.warning("Video claim cache lookup failed: %r", error)
        return None
    if cached is not None:
        _remember_video_claim(key, cached)
    return cached


# Caches a clip claim in Neon without letting a database failure surface.
async def _save_video_claim_safely(
    key: VideoKey, extraction: VideoClaimExtraction
) -> None:
    """Call source_provider.save_video_claim and log, never raise, on failure."""
    video_id, start, end = key
    try:
        await source_provider.save_video_claim(video_id, (start, end), extraction)
    except Exception as error:  # noqa: BLE001 - caching must never break a reply
        logger.warning("Saving video claim failed: %r", error)


# Gets the claim from the clip the viewer is watching: cache, then Gemini video.
async def _get_video_claim(
    http_client: httpx.AsyncClient, settings: Settings, analysis_input: AnalysisInput
) -> VideoClaimExtraction | None:
    """Return the clip's claim, or None if the video model could not be used."""
    video_id = analysis_input.video_id
    window = clip_window(
        analysis_input.video_time_seconds, analysis_input.video_duration_seconds
    )
    key = (video_id, *window)
    cached = await _cached_video_claim(key)
    if cached is not None:
        return cached
    try:
        extraction = await extract_video_claim(
            http_client, settings, watch_url(video_id), window
        )
    except UpstreamError as error:
        # HTTP 400 means the window lies outside a shorter video: use its opening.
        if error.detail != "HTTP 400" or window == (0, DEFAULT_CLIP_SECONDS):
            logger.warning("Video claim extraction failed (%s).", error)
            return None
        try:
            extraction = await extract_video_claim(
                http_client, settings, watch_url(video_id), (0, DEFAULT_CLIP_SECONDS)
            )
        except UpstreamError as retry_error:
            logger.warning("Video claim extraction failed (%s).", retry_error)
            return None
    extraction = replace(extraction, provenance={
        "cache_hit": False, "storage_backend": "none",
        "extracted_at": datetime.now(timezone.utc).isoformat(),
    })
    _remember_video_claim(key, extraction)
    _run_in_background(_save_video_claim_safely(key, extraction))
    return extraction


# Builds the "claim at 2:21" details shown for a video check.
def _claim_source(video_id: str, extraction: VideoClaimExtraction) -> ClaimSource:
    """Return the ClaimSource for a clip claim, linking to its timestamp."""
    seconds = extraction.timestamp_seconds
    has_time = seconds is not None
    return ClaimSource(
        video_url=watch_url(video_id),
        timestamp=format_timestamp(seconds) if has_time else None,
        timestamp_seconds=seconds,
        timestamp_url=(
            timestamp_url(video_id, seconds) if has_time else watch_url(video_id)
        ),
        quote=extraction.quote,
        speaker=extraction.speaker,
        provenance=extraction.provenance,
    )


# Gets the claim from the video or Gemini text, with text heuristics as fallback.
async def _get_claim(
    http_client: httpx.AsyncClient, settings: Settings, analysis_input: AnalysisInput
) -> tuple[str, ClaimSource | None]:
    """Return the claim to check ("" if none) and, for videos, where it was made."""
    if analysis_input.video_id:
        video = await _get_video_claim(http_client, settings, analysis_input)
        if video is not None and video.checkable:
            return video.claim, _claim_source(analysis_input.video_id, video)
        # No claim in the clip, or the video model failed: use the page text (title).
    try:
        extraction = await extract_claim(
            http_client, settings, analysis_input.text, analysis_input.platform
        )
    except UpstreamError as error:
        logger.warning("Claim extraction failed (%s); using fallback.", error)
        return _fallback_claim(analysis_input), None
    return extraction.claim, None


# Maps a fact-check's textual rating onto one of our statuses, if it is clear.
def _rating_to_status(rating: str) -> str | None:
    """Return a status for a clear rating like 'False', else None."""
    normalized_rating = " ".join(WORD_PATTERN.findall(rating.lower().replace("-", " ")))
    for phrase, status in RATING_PHRASES:
        if normalized_rating == phrase or normalized_rating.startswith(phrase + " "):
            return status
    return None


# Measures how many words two claims share (Jaccard similarity).
def _word_overlap(first_text: str, second_text: str) -> float:
    """Return the Jaccard similarity of the two texts' words longer than 2 chars."""
    first_words = {w for w in WORD_PATTERN.findall(first_text.lower()) if len(w) > 2}
    second_words = {w for w in WORD_PATTERN.findall(second_text.lower()) if len(w) > 2}
    if not first_words or not second_words:
        return 0.0
    return len(first_words & second_words) / len(first_words | second_words)


# Builds a conservative verdict from fact-check ratings when Gemini is down.
def _verdict_from_fact_checks(
    claim: str, reviews: list[FactCheckReview], fact_sources: list[Source]
) -> AnalysisVerdict:
    """Return a verdict only if all closely matching fact-checks agree."""
    matched: list[tuple[str, str]] = []
    for review, source in zip(reviews, fact_sources):
        status = _rating_to_status(review.rating)
        if status and _word_overlap(claim, review.claim_text) >= MIN_CLAIM_WORD_OVERLAP:
            matched.append((status, source.id))
    matched_statuses = {status for status, _ in matched}
    if len(matched_statuses) != 1:
        return AnalysisVerdict(
            status="insufficient_evidence",
            explanation=(
                f"{ANALYSIS_UNAVAILABLE_NOTE} No closely matching fact-check gave "
                "a clear, consistent rating."
            ),
            cited_source_ids=[],
        )
    status = matched_statuses.pop()
    cited_ids = [source_id for _, source_id in matched]
    return AnalysisVerdict(
        status=status,
        explanation=(
            "Published fact-checks rated a closely matching claim as "
            f"{STATUS_LABELS[status]} [{', '.join(cited_ids)}]. "
            f"{ANALYSIS_UNAVAILABLE_NOTE}"
        ),
        cited_source_ids=cited_ids,
    )


# Runs the fact-check lookup and the source provider at the same time.
async def _retrieve_evidence(
    http_client: httpx.AsyncClient, settings: Settings, claim: str, seed_url: str | None = None
) -> tuple[list[FactCheckReview], SourceBundle]:
    """Return fact-check reviews and the provider bundle; failures become empty."""
    fact_check_result, bundle_result = await asyncio.gather(
        search_fact_checks(http_client, settings, claim),
        source_provider.fetch_sources(claim, seed_url=seed_url),
        return_exceptions=True,
    )
    if isinstance(fact_check_result, BaseException):
        logger.warning("Fact-check lookup failed: %s", fact_check_result)
        fact_check_result = []
    if not isinstance(bundle_result, SourceBundle):
        if isinstance(bundle_result, BaseException):
            logger.warning("Source provider failed: %r", bundle_result)
        bundle_result = SourceBundle()
    return fact_check_result, bundle_result


# Saves the result via the provider without letting a DB failure break the reply.
async def _persist_safely(result: AnalyzeResponse) -> None:
    """Call source_provider.save_investigation and log, never raise, on failure."""
    try:
        saved = await asyncio.wait_for(source_provider.save_investigation(result), 2.0)
        if isinstance(saved, StorageMetadata):
            result.provenance.saved_analysis = saved
    except Exception as error:  # noqa: BLE001 - persistence must never break a reply
        logger.warning("Saving investigation failed: %r", error)


# Runs one full investigation from sanitized input to a validated response.
async def run_investigation(
    http_client: httpx.AsyncClient, settings: Settings, analysis_input: AnalysisInput
) -> AnalyzeResponse:
    """Return a contract-shaped result; never invents citations or verdicts."""
    investigation_id = uuid.uuid4().hex
    claim, claim_source = await _get_claim(http_client, settings, analysis_input)
    if not claim:
        preview = analysis_input.text[:CLAIM_PREVIEW_CHARS].replace("\n", " ")
        no_claim_verdict = AnalysisVerdict(
            "insufficient_evidence", NO_CLAIM_EXPLANATION, []
        )
        result = to_analyze_response(
            investigation_id, preview, no_claim_verdict, [], None, [], [])
        return await complete_result(result, analysis_input)

    reviews, bundle = await _retrieve_evidence(
        http_client, settings, claim, seed_url=analysis_input.url if not analysis_input.platform else None)
    investigation_id = bundle.investigation_id or investigation_id
    fact_sources = reviews_to_sources(reviews)
    sources = clean_sources(fact_sources + bundle.sources)
    fact_nodes = [
        GraphNode(
            id=source.id,
            title=source.title,
            url=source.url,
            date=source.date,
            kind="fact_check",
        )
        for source in fact_sources
    ]
    graph_nodes = clean_nodes(fact_nodes + bundle.graph_nodes)
    graph_edges = clean_edges(bundle.graph_edges, graph_nodes)
    source_urls = {source.url for source in sources}
    earliest_source = bundle.earliest_observed_source
    if earliest_source is not None and earliest_source.url not in source_urls:
        earliest_source = None

    if not sources:
        verdict = AnalysisVerdict("insufficient_evidence", NO_SOURCES_EXPLANATION, [])
    else:
        try:
            raw_verdict = await analyze_claim(http_client, settings, claim, sources)
        except UpstreamError as error:
            logger.warning("Final analysis failed (%s); using fact-checks.", error)
            raw_verdict = _verdict_from_fact_checks(claim, reviews, fact_sources)
        verdict = validate_verdict(raw_verdict, sources)

    result = to_analyze_response(
        investigation_id,
        claim,
        verdict,
        sources,
        earliest_source,
        graph_nodes,
        graph_edges,
        claim_source,
    )
    result.checked_at = datetime.now(timezone.utc).isoformat()
    result.provenance.source_trace = bundle.source_trace
    result.provenance.video_extraction = claim_source.provenance if claim_source else None
    return await complete_result(result, analysis_input)


async def complete_result(result, analysis_input):
    result.analysis_id = uuid.uuid4().hex
    result.checked_at = result.checked_at or datetime.now(timezone.utc).isoformat()
    result.page_context = PageContext(
        url=analysis_input.url, text=analysis_input.text, title=analysis_input.page_title,
        mode=analysis_input.mode, platform=analysis_input.platform,
        video_time_seconds=analysis_input.video_time_seconds,
        video_duration_seconds=analysis_input.video_duration_seconds,
    )
    await _persist_safely(result)
    from app.evidence_registry import remember_analysis
    remember_analysis(result)
    return result
