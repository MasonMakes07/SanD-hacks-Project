"""Security middleware: API-key auth, body size limit, rate limit, safe headers."""

import hmac
import time
from collections import deque

from fastapi import Request
from fastapi.responses import JSONResponse

from app.config import get_settings
from app.Translator import to_error_body

AUTH_HEADER_NAME = "X-TruthTrace-Key"
PUBLIC_PATHS = frozenset({"/api/health"})
RATE_WINDOW_SECONDS = 60.0
MAX_TRACKED_CLIENTS = 5000
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
}

request_times_by_client: dict[str, deque[float]] = {}


# Builds a JSON error response with security headers attached.
def _error(status_code: int, error_code: str, message: str) -> JSONResponse:
    """Return a JSONResponse in the shared ErrorResponse shape."""
    response = JSONResponse(
        status_code=status_code, content=to_error_body(error_code, message)
    )
    response.headers.update(SECURITY_HEADERS)
    return response


# Records a request and reports whether the client is over its per-minute limit.
def _is_rate_limited(client_key: str, limit_per_minute: int) -> bool:
    """Return True if this client already made `limit_per_minute` recent requests."""
    now = time.monotonic()
    request_times = request_times_by_client.get(client_key)
    if request_times is None:
        if len(request_times_by_client) >= MAX_TRACKED_CLIENTS:
            request_times_by_client.clear()
        request_times = request_times_by_client.setdefault(client_key, deque())
    while request_times and now - request_times[0] > RATE_WINDOW_SECONDS:
        request_times.popleft()
    if len(request_times) >= limit_per_minute:
        return True
    request_times.append(now)
    return False


# Checks the shared API key in constant time.
def _has_valid_key(request: Request, expected_key: str) -> bool:
    """Return True if the request's X-TruthTrace-Key matches the configured key."""
    provided_key = request.headers.get(AUTH_HEADER_NAME, "")
    return bool(expected_key) and hmac.compare_digest(
        provided_key.encode("utf-8"), expected_key.encode("utf-8")
    )


# Applies auth, size limit and rate limit, then adds security headers.
async def security_middleware(request: Request, call_next):
    """Reject unsafe requests early and harden every outgoing response."""
    settings = get_settings()
    if request.method != "OPTIONS" and request.url.path not in PUBLIC_PATHS:
        if not settings.truthtrace_api_key:
            return _error(503, "server_misconfigured", "Server API key is not set.")
        if not _has_valid_key(request, settings.truthtrace_api_key):
            return _error(401, "unauthorized", "Missing or invalid API key.")
        if request.method == "POST":
            content_length = request.headers.get("content-length")
            if content_length is None or not content_length.isdigit():
                return _error(411, "length_required", "Content-Length is required.")
            if int(content_length) > settings.max_body_bytes:
                return _error(413, "payload_too_large", "Request body is too large.")
        client_key = request.client.host if request.client else "unknown"
        if _is_rate_limited(client_key, settings.rate_limit_per_minute):
            return _error(429, "rate_limited", "Too many requests; slow down.")
    response = await call_next(request)
    response.headers.update(SECURITY_HEADERS)
    return response
