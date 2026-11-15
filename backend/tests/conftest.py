"""Shared test fixtures: isolated settings, fake HTTP transports, API client."""

import os

TEST_ENV = {
    "GEMINI_API_KEY": "test-gemini-key",
    "GEMINI_MODEL": "gemini-test",
    "GEMINI_VIDEO_MODEL": "gemini-video-test",
    "GEMINI_FALLBACK_MODEL": "gemini-backup-test",
    "GEMINI_HEDGE_SECONDS": "0.2",
    "VIDEO_TIMEOUT_SECONDS": "5",
    "FACTCHECK_API_KEY": "test-factcheck-key",
    "TRUTHTRACE_API_KEY": "test-api-key",
    "USE_MOCK": "false",
    "ALLOWED_ORIGIN_REGEX": r"^chrome-extension://[a-p]{32}$",
    "REQUEST_TIMEOUT_SECONDS": "5",
    "ANALYZE_TIMEOUT_SECONDS": "10",
    "RATE_LIMIT_PER_MINUTE": "1000",
    "MAX_BODY_BYTES": "100000",
}
os.environ.update(TEST_ENV)

import json  # noqa: E402

import httpx  # noqa: E402
import pytest  # noqa: E402

from app import analysis, middleware, source_provider  # noqa: E402
from app.models import SourceBundle  # noqa: E402
from app.config import get_settings  # noqa: E402

AUTH_HEADERS = {"X-TruthTrace-Key": TEST_ENV["TRUTHTRACE_API_KEY"]}


# Wraps a JSON-serializable object as a Gemini generateContent response body.
def gemini_body(result_object: dict) -> dict:
    """Return a fake Gemini response whose text part is the JSON-encoded object."""
    return {
        "candidates": [
            {"content": {"parts": [{"text": json.dumps(result_object)}]}}
        ]
    }


# Resets cached settings and rate-limit state around every test.
@pytest.fixture(autouse=True)
def fresh_settings(monkeypatch):
    """Restore the test environment and clear caches before each test."""
    for name, value in TEST_ENV.items():
        monkeypatch.setenv(name, value)
    get_settings.cache_clear()
    middleware.request_times_by_client.clear()
    yield
    get_settings.cache_clear()
    middleware.request_times_by_client.clear()


REAL_FETCH_SOURCES = source_provider.fetch_sources
REAL_SAVE_INVESTIGATION = source_provider.save_investigation
REAL_FIND_VIDEO_CLAIM = source_provider.find_video_claim
REAL_SAVE_VIDEO_CLAIM = source_provider.save_video_claim


# Returns an empty bundle so offline tests never call Bright Data or Neon.
async def _no_sources(claim: str) -> SourceBundle:
    """Return an empty SourceBundle."""
    return SourceBundle()


# Stands in for the Neon write so offline tests never touch the database.
async def _no_save(result) -> None:
    """Do nothing."""
    return None


# Reports a Neon video-cache miss so offline tests never touch the database.
async def _no_video_claim(video_id, window):
    """Return None (cache miss)."""
    return None


# Stands in for the Neon video-cache write.
async def _no_save_video_claim(video_id, window, extraction) -> None:
    """Do nothing."""
    return None


# Replaces the live source_tracing calls for every test (tests may override).
@pytest.fixture(autouse=True)
def offline_source_provider(monkeypatch):
    """Stub Bright Data, Neon writes and the Neon video cache with no-ops."""
    monkeypatch.setattr(source_provider, "fetch_sources", _no_sources)
    monkeypatch.setattr(source_provider, "save_investigation", _no_save)
    monkeypatch.setattr(source_provider, "find_video_claim", _no_video_claim)
    monkeypatch.setattr(source_provider, "save_video_claim", _no_save_video_claim)
    analysis._video_claim_cache.clear()
    yield
    analysis._video_claim_cache.clear()


# Builds an AsyncClient whose requests are answered by a handler function.
def make_http_client(handler) -> httpx.AsyncClient:
    """Return an httpx.AsyncClient backed by httpx.MockTransport(handler)."""
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))
