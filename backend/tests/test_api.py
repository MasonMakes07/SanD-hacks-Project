"""HTTP-level tests: auth, validation, middleware limits, mock mode, CORS."""

import json

import pytest
from fastapi.testclient import TestClient

from app import main
from app.config import get_settings
from app.models import AnalyzeResponse
from tests.conftest import AUTH_HEADERS

EXTENSION_ORIGIN = "chrome-extension://" + "a" * 32
VALID_BODY = {
    "text": "The city cut its library budget by 40% this year.",
    "url": "https://news.example/article",
    "mode": "manual",
}


# Provides a TestClient with the lifespan (shared HTTP client) running.
@pytest.fixture
def client():
    """Yield a TestClient for the FastAPI app."""
    with TestClient(main.app) as test_client:
        yield test_client


# Switches the server into mock mode for one test.
@pytest.fixture
def mock_mode(monkeypatch):
    """Enable USE_MOCK and clear the settings cache."""
    monkeypatch.setenv("USE_MOCK", "true")
    get_settings.cache_clear()


# Checks the health endpoint is public.
def test_health_is_public(client):
    """GET /api/health needs no key."""
    response = client.get("/api/health")
    assert response.status_code == 200 and response.json()["status"] == "ok"
    assert response.headers["X-Content-Type-Options"] == "nosniff"


# Checks missing and wrong API keys are rejected.
@pytest.mark.parametrize("headers", [{}, {"X-TruthTrace-Key": "wrong"}])
def test_analyze_requires_valid_key(client, headers):
    """Requests without the correct key get 401."""
    response = client.post("/api/analyze", json=VALID_BODY, headers=headers)
    assert response.status_code == 401
    assert response.json()["error"] == "unauthorized"


# Checks the server refuses to serve analyze without a configured key.
def test_missing_server_key_returns_503(client, monkeypatch):
    """An unset TRUTHTRACE_API_KEY never means 'auth disabled'."""
    monkeypatch.setenv("TRUTHTRACE_API_KEY", "")
    get_settings.cache_clear()
    response = client.post("/api/analyze", json=VALID_BODY, headers=AUTH_HEADERS)
    assert response.status_code == 503


# Checks mock mode returns a response matching the contract.
def test_mock_mode_returns_contract(client, mock_mode):
    """USE_MOCK=true returns the fixed mock, valid against AnalyzeResponse."""
    response = client.post("/api/analyze", json=VALID_BODY, headers=AUTH_HEADERS)
    assert response.status_code == 200
    body = response.json()
    AnalyzeResponse.model_validate(body)
    assert set(body) == {
        "investigation_id",
        "claim",
        "status",
        "explanation",
        "sources",
        "earliest_observed_source",
        "graph_nodes",
        "graph_edges",
        "claim_source",
        "cited_source_ids",
        "evidence_quotes",
        "checked_at",
        "analysis_id",
        "page_context",
        "reopened",
        "reassessment",
        "provenance",
    }
    assert body["status"] == "contradicted"


# Checks schema violations return 422 without echoing input.
@pytest.mark.parametrize(
    "body",
    [
        {**VALID_BODY, "mode": "turbo"},
        {**VALID_BODY, "text": ""},
        {**VALID_BODY, "text": "x" * 20001},
        {"text": "a", "url": "https://x.example/"},
        {**VALID_BODY, "extra": "field"},
        {**VALID_BODY, "text": 123},
    ],
)
def test_invalid_bodies_return_422(client, mock_mode, body):
    """Bad fields are rejected and the response never echoes the input."""
    response = client.post("/api/analyze", json=body, headers=AUTH_HEADERS)
    assert response.status_code == 422
    assert response.json()["error"] == "invalid_request"
    assert "turbo" not in response.text and "xxxx" not in response.text


# Checks code-like text and unsafe URLs are rejected with 400.
@pytest.mark.parametrize(
    "body",
    [
        {**VALID_BODY, "text": "<script>alert(1)</script>"},
        {**VALID_BODY, "text": "'; DROP TABLE users; --"},
        {**VALID_BODY, "url": "javascript:alert(1)"},
        {**VALID_BODY, "url": "file:///etc/passwd"},
    ],
)
def test_unsafe_input_returns_400(client, mock_mode, body):
    """Sanitization failures return 400 input_rejected, even in mock mode."""
    response = client.post("/api/analyze", json=body, headers=AUTH_HEADERS)
    assert response.status_code == 400
    assert response.json()["error"] == "input_rejected"


# Checks oversized bodies are rejected before parsing.
def test_oversized_body_returns_413(client, monkeypatch):
    """Bodies above MAX_BODY_BYTES get 413."""
    monkeypatch.setenv("MAX_BODY_BYTES", "200")
    get_settings.cache_clear()
    body = {**VALID_BODY, "text": "word " * 100}
    response = client.post("/api/analyze", json=body, headers=AUTH_HEADERS)
    assert response.status_code == 413


# Checks a POST without Content-Length is refused.
def test_missing_content_length_returns_411(client):
    """Chunked bodies without Content-Length get 411."""

    # Yields the body in chunks so no Content-Length is sent.
    def body_chunks():
        """Yield the JSON body as a stream."""
        yield json.dumps(VALID_BODY).encode()

    response = client.post(
        "/api/analyze",
        content=body_chunks(),
        headers={**AUTH_HEADERS, "Content-Type": "application/json"},
    )
    assert response.status_code == 411


# Checks the per-client rate limit kicks in.
def test_rate_limit_returns_429(client, mock_mode, monkeypatch):
    """The (N+1)th request within a minute gets 429."""
    monkeypatch.setenv("RATE_LIMIT_PER_MINUTE", "3")
    get_settings.cache_clear()
    status_codes = [
        client.post("/api/analyze", json=VALID_BODY, headers=AUTH_HEADERS).status_code
        for _ in range(4)
    ]
    assert status_codes == [200, 200, 200, 429]


# Checks CORS preflight is allowed for the extension and refused for websites.
def test_cors_preflight(client):
    """Extension origins get CORS headers; arbitrary websites do not."""
    preflight_headers = {
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "content-type,x-truthtrace-key",
    }
    allowed = client.options(
        "/api/analyze", headers={"Origin": EXTENSION_ORIGIN, **preflight_headers}
    )
    assert allowed.status_code == 200
    assert allowed.headers["access-control-allow-origin"] == EXTENSION_ORIGIN
    refused = client.options(
        "/api/analyze",
        headers={"Origin": "https://evil.example", **preflight_headers},
    )
    assert "access-control-allow-origin" not in refused.headers


# Checks a pipeline timeout becomes a 504.
def test_timeout_returns_504(client, monkeypatch):
    """Investigations exceeding ANALYZE_TIMEOUT_SECONDS return 504."""
    import asyncio

    # Simulates an investigation that never finishes in time.
    async def slow_investigation(*args, **kwargs):
        """Sleep longer than the timeout."""
        await asyncio.sleep(5)

    monkeypatch.setenv("ANALYZE_TIMEOUT_SECONDS", "0.05")
    get_settings.cache_clear()
    monkeypatch.setattr(main, "run_investigation", slow_investigation)
    response = client.post("/api/analyze", json=VALID_BODY, headers=AUTH_HEADERS)
    assert response.status_code == 504
    assert response.json()["error"] == "timeout"


# Checks unexpected errors return a generic 500 without internals.
def test_unexpected_error_returns_generic_500(monkeypatch):
    """Internal exception messages never reach the client."""

    # Simulates a programming error deep in the pipeline.
    async def crashing_investigation(*args, **kwargs):
        """Raise an error with a sensitive-looking message."""
        raise RuntimeError("secret internal detail")

    monkeypatch.setattr(main, "run_investigation", crashing_investigation)
    with TestClient(main.app, raise_server_exceptions=False) as test_client:
        response = test_client.post(
            "/api/analyze", json=VALID_BODY, headers=AUTH_HEADERS
        )
    assert response.status_code == 500
    assert "secret" not in response.text


# Checks the real (non-mock) path runs end to end through the HTTP layer.
def test_real_path_end_to_end(client, monkeypatch):
    """With fake upstream services the endpoint returns a valid contract body."""
    import httpx

    from tests.conftest import gemini_body

    # Answers both Gemini calls and the fact-check call.
    def handler(request: httpx.Request) -> httpx.Response:
        """Return canned upstream responses."""
        if request.url.host == "factchecktools.googleapis.com":
            return httpx.Response(200, json={})
        return httpx.Response(
            200, json=gemini_body({"checkable": True, "claim": VALID_BODY["text"]})
        )

    client.app.state.http_client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler)
    )
    response = client.post("/api/analyze", json=VALID_BODY, headers=AUTH_HEADERS)
    assert response.status_code == 200
    body = AnalyzeResponse.model_validate(response.json())
    assert body.status == "insufficient_evidence"
    assert body.claim == VALID_BODY["text"]
