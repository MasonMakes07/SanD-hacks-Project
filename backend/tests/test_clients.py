"""Tests for the Gemini and Fact Check API clients using fake transports."""

import json

import httpx
import pytest

from app.config import get_settings
from app.factcheck_client import reviews_to_sources, search_fact_checks
from app.gemini_client import analyze_claim, extract_claim
from app.models import Source, UpstreamError
from tests.conftest import gemini_body, make_http_client

FACTCHECK_BODY = {
    "claims": [
        {
            "text": "The city cut its library budget by 40%",
            "claimant": "Viral post",
            "claimReview": [
                {
                    "publisher": {"name": "Fact Desk", "site": "factdesk.example"},
                    "url": "https://factdesk.example/library",
                    "title": "No, the library budget was not cut 40%",
                    "reviewDate": "2026-10-02T00:00:00Z",
                    "textualRating": "False",
                },
                {
                    "publisher": {"site": "dup.example"},
                    "url": "https://factdesk.example/library",
                    "textualRating": "False",
                },
                {"url": "javascript:alert(1)", "textualRating": "True"},
            ],
        },
        "not a dict",
        {"text": "Other", "claimReview": None},
    ]
}


# Checks a well-formed Gemini extraction is parsed and the key sent as header.
async def test_extract_claim_success():
    """The claim is returned and the API key goes in a header, not the URL."""
    captured = {}

    # Records the outgoing request and returns a valid extraction.
    def handler(request: httpx.Request) -> httpx.Response:
        """Return a canned Gemini extraction response."""
        captured["request"] = request
        return httpx.Response(
            200, json=gemini_body({"checkable": True, "claim": "  X rose 5%.  "})
        )

    async with make_http_client(handler) as client:
        result = await extract_claim(client, get_settings(), "Some text")
    assert result.checkable and result.claim == "X rose 5%."
    sent_request = captured["request"]
    assert sent_request.headers["x-goog-api-key"] == "test-gemini-key"
    assert "test-gemini-key" not in str(sent_request.url)
    assert "gemini-test" in str(sent_request.url)
    sent_body = json.loads(sent_request.content)
    assert sent_body["generationConfig"]["responseMimeType"] == "application/json"
    assert "<page_text>" in sent_body["contents"][0]["parts"][0]["text"]


# Checks JSON split across several parts is joined and thought parts are skipped.
async def test_extract_claim_joins_split_parts():
    """Multi-part responses are concatenated, ignoring thought parts."""
    split_body = {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {"text": "thinking...", "thought": True},
                        {"text": '{"checkable": true, '},
                        {"inlineData": {"mimeType": "image/png"}},
                        {"text": '"claim": "Y fell 3%."}'},
                    ]
                }
            }
        ]
    }
    async with make_http_client(
        lambda request: httpx.Response(200, json=split_body)
    ) as client:
        result = await extract_claim(client, get_settings(), "Some text")
    assert result.checkable and result.claim == "Y fell 3%."


# Checks checkable=True with an empty claim is treated as not checkable.
async def test_extract_claim_empty_claim_is_not_checkable():
    """An empty claim string is never reported as checkable."""

    # Returns an inconsistent extraction.
    def handler(request):
        """Return checkable=True but an empty claim."""
        return httpx.Response(200, json=gemini_body({"checkable": True, "claim": ""}))

    async with make_http_client(handler) as client:
        result = await extract_claim(client, get_settings(), "Opinion text")
    assert not result.checkable and result.claim == ""


# Checks every Gemini failure mode surfaces as UpstreamError.
@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(500, json={"error": "boom"}),
        httpx.Response(429, json={"error": "quota"}),
        httpx.Response(200, json={"promptFeedback": {"blockReason": "SAFETY"}}),
        httpx.Response(200, json={"candidates": []}),
        httpx.Response(200, text="not json"),
        httpx.Response(200, json=gemini_body(["a", "list"])),
        httpx.Response(200, json={"candidates": [{"content": {"parts": []}}]}),
        httpx.Response(200, json={"candidates": [{"finishReason": "SAFETY"}]}),
        httpx.Response(
            200,
            json={
                "candidates": [
                    {"content": {"parts": [{"text": "{}", "thought": True}]}}
                ]
            },
        ),
        httpx.Response(
            200, json={"candidates": [{"content": {"parts": [{"text": "{bad"}]}}]}
        ),
    ],
)
async def test_gemini_failures_raise_upstream_error(response):
    """HTTP errors, blocks and malformed bodies raise UpstreamError."""
    async with make_http_client(lambda request: response) as client:
        with pytest.raises(UpstreamError):
            await extract_claim(client, get_settings(), "text")


# Checks timeouts and network errors raise UpstreamError.
@pytest.mark.parametrize(
    "exception", [httpx.ReadTimeout("slow"), httpx.ConnectError("down")]
)
async def test_gemini_network_errors_raise(exception):
    """Transport-level failures raise UpstreamError."""

    # Simulates a transport failure.
    def handler(request):
        """Raise the parametrized httpx exception."""
        raise exception

    async with make_http_client(handler) as client:
        with pytest.raises(UpstreamError):
            await extract_claim(client, get_settings(), "text")


# Checks a missing key fails fast without any network call.
async def test_gemini_missing_key_raises(monkeypatch):
    """No GEMINI_API_KEY means UpstreamError before any request."""
    monkeypatch.setenv("GEMINI_API_KEY", "")
    get_settings.cache_clear()

    # Fails the test if any request is attempted.
    def handler(request):
        """Fail if called."""
        raise AssertionError("no request expected")

    async with make_http_client(handler) as client:
        with pytest.raises(UpstreamError):
            await extract_claim(client, get_settings(), "text")


# Checks analyze_claim parses status, explanation and ids.
async def test_analyze_claim_parses_verdict():
    """The raw verdict fields are passed through for later validation."""
    captured = {}

    # Returns a canned analysis.
    def handler(request):
        """Record the prompt and return a verdict."""
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json=gemini_body(
                {
                    "status": "contradicted",
                    "explanation": "Fell 4% [S1].",
                    "cited_source_ids": ["S1"],
                }
            ),
        )

    sources = [Source(id="S1", title="t", url="https://a.example/", excerpt="4%")]
    async with make_http_client(handler) as client:
        verdict = await analyze_claim(client, get_settings(), "claim", sources)
    assert verdict.status == "contradicted"
    assert verdict.cited_source_ids == ["S1"]
    prompt_text = captured["body"]["contents"][0]["parts"][0]["text"]
    assert '"id": "S1"' in prompt_text and "https://a.example/" not in prompt_text


# Checks fact-check parsing dedupes, skips bad entries and maps to sources.
async def test_search_fact_checks_parses_and_dedupes():
    """Duplicate URLs, bad URLs and malformed entries are skipped."""
    captured = {}

    # Returns the canned fact-check body.
    def handler(request):
        """Record the request and return FACTCHECK_BODY."""
        captured["request"] = request
        return httpx.Response(200, json=FACTCHECK_BODY)

    async with make_http_client(handler) as client:
        reviews = await search_fact_checks(client, get_settings(), "library budget")
    assert len(reviews) == 1
    assert reviews[0].publisher == "Fact Desk" and reviews[0].rating == "False"
    assert captured["request"].headers["x-goog-api-key"] == "test-factcheck-key"
    assert "test-factcheck-key" not in str(captured["request"].url)

    sources = reviews_to_sources(reviews)
    assert sources[0].id == "F1"
    assert sources[0].date == "2026-10-02"
    assert "rated it: False" in sources[0].excerpt
    assert "Viral post" in sources[0].excerpt


# Checks an empty fact-check response yields no reviews.
async def test_search_fact_checks_empty():
    """`{}` (the API's no-results body) yields an empty list."""
    async with make_http_client(lambda request: httpx.Response(200, json={})) as client:
        assert await search_fact_checks(client, get_settings(), "x") == []


# Checks fact-check HTTP and network failures raise UpstreamError.
@pytest.mark.parametrize("status_code", [400, 403, 500])
async def test_search_fact_checks_errors(status_code):
    """Non-200 responses raise UpstreamError."""
    async with make_http_client(
        lambda request: httpx.Response(status_code)
    ) as client:
        with pytest.raises(UpstreamError):
            await search_fact_checks(client, get_settings(), "x")
