"""Shared test fixtures: isolated settings, fake HTTP transports, API client."""

import os

TEST_ENV = {
    "GEMINI_API_KEY": "test-gemini-key",
    "GEMINI_MODEL": "gemini-test",
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

from app import middleware  # noqa: E402
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


# Builds an AsyncClient whose requests are answered by a handler function.
def make_http_client(handler) -> httpx.AsyncClient:
    """Return an httpx.AsyncClient backed by httpx.MockTransport(handler)."""
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))
