"""Investigation pipeline: extract claim -> retrieve evidence -> analyze -> validate."""

import asyncio
import logging
import re
import uuid
from collections import OrderedDict
from dataclasses import replace

import httpx

from app import source_provider
from app.budget import remaining_timeout
from app.confirmation import approved_context, issue_confirmation
from app.factcheck_client import reviews_to_sources, search_fact_checks
from app.gemini_client import VIDEO_PROMPT_VERSION, analyze_claim, extract_claim, extract_video_claim
from app.models import (
    AnalysisInput,
    AnalysisVerdict,
    AnalyzeResponse,
    ClaimSource,
    FactCheckReview,
    ExtractResponse,
    GraphNode,
    InputContext,
    Settings,
    Source,
    SourceBundle,
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
    clip_window,
    format_timestamp,
    timestamp_url,
    watch_url,
)

logger = logging.getLogger("truthtrace.analysis")

VIDEO_CACHE_SIZE = 256
NEON_LOOKUP_TIMEOUT_SECONDS = 2.0
VideoKey = tuple[str, int, int, int]
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
        return _video_claim_cache[key]
    video_id, start, end, _version = key
    try:
        cached = await asyncio.wait_for(
            source_provider.find_video_claim(video_id, (start, end)),
            remaining_timeout(NEON_LOOKUP_TIMEOUT_SECONDS),
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
    video_id, start, end, _version = key
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
    key = (video_id, *window, VIDEO_PROMPT_VERSION)
    cached = await _cached_video_claim(key)
    if cached is not None:
        seconds = cached.timestamp_seconds
        if seconds is not None and (not isinstance(seconds, int) or not window[0] <= seconds <= window[1]):
            return replace(cached, timestamp_seconds=None)
        return cached
    try:
        extraction = await extract_video_claim(
            http_client, settings, watch_url(video_id), window
        )
    except UpstreamError as error:
        logger.warning("Video claim extraction failed (%s).", error)
        return None
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
            timestamp_url(video_id, seconds) if has_time else None
        ),
        quote=extraction.quote,
        speaker=extraction.speaker,
    )


# Gets the claim from the video or Gemini text, with text heuristics as fallback.
async def _get_claim(
    http_client: httpx.AsyncClient, settings: Settings, analysis_input: AnalysisInput
) -> tuple[str, ClaimSource | None, InputContext]:
    """Return one proposal and explicit origin; clip failures never check a title."""
    kind = analysis_input.capture_kind or ("video" if analysis_input.video_id else "article")
    context = InputContext(capture_kind=kind, extraction_method="text_model")
    if analysis_input.video_id:
        window = clip_window(analysis_input.video_time_seconds, analysis_input.video_duration_seconds)
        context.video_url = watch_url(analysis_input.video_id)
        context.captured_time_seconds = analysis_input.video_time_seconds
        context.reviewed_start_seconds, context.reviewed_end_seconds = window
        video = await _get_video_claim(http_client, settings, analysis_input)
        if video is not None and video.checkable:
            context.extraction_method = "video_model"
            return video.claim, _claim_source(analysis_input.video_id, video), context
        context.extraction_method = "video_no_claim" if video is not None else "video_unavailable"
        context.fallback_reason = ("No checkable statement was detected in this clip." if video is not None
                                   else "Video extraction was unavailable.")
        return "", None, context
    try:
        extraction = await extract_claim(http_client, settings, analysis_input.text, analysis_input.platform)
    except UpstreamError as error:
        logger.warning("Claim extraction failed (%s); using text fallback.", error)
        context.extraction_method = "text_fallback"
        context.fallback_reason = "Text extraction was unavailable; this proposal uses the captured text."
        return _fallback_claim(analysis_input), None, context
    if not extraction.claim:
        context.extraction_method = "no_claim"
    return extraction.claim, None, context


async def prepare_claim(http_client: httpx.AsyncClient, settings: Settings,
                        analysis_input: AnalysisInput) -> ExtractResponse:
    """Extract only; no Bright Data, Fact Check, final analysis or persistence."""
    claim, source, context = await _get_claim(http_client, settings, analysis_input)
    return ExtractResponse(
        checkable=bool(claim), claim=claim, claim_source=source, input_context=context,
        confirmation_token=issue_confirmation(analysis_input, claim, source, context, settings.truthtrace_api_key) if claim else None,
    )


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
    http_client: httpx.AsyncClient, settings: Settings, claim: str
) -> tuple[list[FactCheckReview], SourceBundle]:
    """Return fact-check reviews and the provider bundle; failures become empty."""
    fact_check_result, bundle_result = await asyncio.gather(
        search_fact_checks(http_client, settings, claim),
        source_provider.fetch_sources(claim),
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
        await source_provider.save_investigation(result)
    except Exception as error:  # noqa: BLE001 - persistence must never break a reply
        logger.warning("Saving investigation failed: %r", error)


# Runs one full investigation from sanitized input to a validated response.
async def run_investigation(
    http_client: httpx.AsyncClient, settings: Settings, analysis_input: AnalysisInput
) -> AnalyzeResponse:
    """Return a contract-shaped result; never invents citations or verdicts."""
    investigation_id = uuid.uuid4().hex
    if analysis_input.approved_claim is not None:
        claim_source, input_context = approved_context(analysis_input, settings.truthtrace_api_key)
        claim = analysis_input.approved_claim
    else:
        claim, claim_source, input_context = await _get_claim(http_client, settings, analysis_input)
    if not claim:
        preview = analysis_input.text[:CLAIM_PREVIEW_CHARS].replace("\n", " ")
        no_claim_verdict = AnalysisVerdict(
            "insufficient_evidence", NO_CLAIM_EXPLANATION, []
        )
        result = to_analyze_response(
            investigation_id, preview, no_claim_verdict, [], None, [], []
        )
        result.input_context = input_context
        if input_context.capture_kind == "video":
            result.claim = "No video claim checked"
            result.explanation = f"{input_context.fallback_reason} No video claim was checked. Try another moment, or explicitly check selected text instead."
        return result

    reviews, bundle = await _retrieve_evidence(http_client, settings, claim)
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
    result.input_context = input_context
    # Saved after the reply is built so the database never adds response latency.
    _run_in_background(_persist_safely(result))
    return result
