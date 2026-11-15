"""FastAPI entry point: /api/analyze, /api/chat, /api/evidence/search, /api/health."""

import asyncio
import logging
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.analysis import run_investigation
from app.history import router as history_router
from app.reassessment import router as reassessment_router
from app.config import get_settings
from app.detective import (
    EvidenceSearchError,
    answer_chat,
    build_mock_chat_response,
    search_more_evidence,
)
from app.middleware import (
    AUTH_HEADER_NAME,
    is_search_rate_limited,
    security_middleware,
)
from app.mock import build_mock_response
from app.models import (
    AnalyzeRequest,
    AnalyzeResponse,
    ChatRequest,
    ChatResponse,
    EvidenceSearchRequest,
    EvidenceSearchResponse,
    InputRejectedError,
    UpstreamError,
)
from app.Translator import to_analysis_input, to_error_body

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("truthtrace.api")


# Opens one shared HTTP client for the app's lifetime and closes it on shutdown.
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Create app.state.http_client at startup and close it at shutdown."""
    settings = get_settings()
    if not settings.truthtrace_api_key:
        logger.error("TRUTHTRACE_API_KEY is not set; /api/analyze will return 503.")
    async with httpx.AsyncClient(follow_redirects=False) as http_client:
        app.state.http_client = http_client
        yield


app = FastAPI(
    title="TruthTrace API",
    version="1.0.0",
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)
app.include_router(history_router)
app.include_router(reassessment_router)
app.middleware("http")(security_middleware)
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=get_settings().allowed_origin_regex,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", AUTH_HEADER_NAME],
    allow_credentials=False,
    max_age=600,
)


# Returns a generic 422 without echoing the rejected input back.
@app.exception_handler(RequestValidationError)
async def handle_validation_error(request: Request, error: RequestValidationError):
    """Return 422 listing only the names of the invalid fields."""
    bad_fields = sorted(
        {str(item["loc"][-1]) for item in error.errors() if item.get("loc")}
    )
    message = "Invalid request fields: " + (", ".join(bad_fields) or "body")
    return JSONResponse(
        status_code=422, content=to_error_body("invalid_request", message)
    )


# Returns 400 when sanitization rejects the input.
@app.exception_handler(InputRejectedError)
async def handle_rejected_input(request: Request, error: InputRejectedError):
    """Return 400 with the sanitizer's reason."""
    return JSONResponse(
        status_code=400, content=to_error_body("input_rejected", str(error))
    )


# Returns a generic 500 and logs the error type without leaking details.
@app.exception_handler(Exception)
async def handle_unexpected_error(request: Request, error: Exception):
    """Return a generic 500 so internal details never reach the client."""
    logger.exception("Unhandled error: %s", type(error).__name__)
    return JSONResponse(
        status_code=500, content=to_error_body("internal_error", "Unexpected error.")
    )


# Reports that the server is up; public so the extension can show connectivity.
@app.get("/api/health")
async def health() -> dict:
    """Return a minimal liveness payload."""
    return {"status": "ok", "mock": get_settings().use_mock}


# Analyzes one claim, or returns the mock response when USE_MOCK is enabled.
@app.post("/api/analyze", response_model=AnalyzeResponse)
async def analyze(request_body: AnalyzeRequest, request: Request):
    """Sanitize input, run the investigation with a time budget, return result."""
    settings = get_settings()
    analysis_input = to_analysis_input(request_body)
    if settings.use_mock:
        return build_mock_response()
    try:
        return await asyncio.wait_for(
            run_investigation(request.app.state.http_client, settings, analysis_input),
            timeout=settings.analyze_timeout_seconds,
        )
    except TimeoutError:
        logger.warning("Investigation exceeded %ss", settings.analyze_timeout_seconds)
        return JSONResponse(
            status_code=504,
            content=to_error_body("timeout", "Analysis took too long; try again."),
        )


# Answers a follow-up question about an investigation the extension already holds.
@app.post("/api/chat", response_model=ChatResponse)
async def chat(request_body: ChatRequest, request: Request):
    """Return a cited answer grounded in the sources sent with the question."""
    settings = get_settings()
    if settings.use_mock:
        return build_mock_chat_response()
    try:
        return await answer_chat(request.app.state.http_client, settings, request_body)
    except UpstreamError as error:
        logger.warning("Detective chat failed: %s", error)
        return JSONResponse(
            status_code=502,
            content=to_error_body(
                "chat_unavailable", "The detective could not be reached; try again."
            ),
        )


# Runs one explicit, user-requested search for more or opposing evidence.
@app.post("/api/evidence/search", response_model=EvidenceSearchResponse)
async def evidence_search(request_body: EvidenceSearchRequest, request: Request):
    """Return newly retrieved sources; each uncached call spends search credits."""
    settings = get_settings()
    if settings.use_mock:
        return JSONResponse(
            status_code=503,
            content=to_error_body(
                "search_unavailable", "Evidence search is off in mock mode."
            ),
        )
    client_key = request.client.host if request.client else "unknown"
    if is_search_rate_limited(client_key):
        return JSONResponse(
            status_code=429,
            content=to_error_body(
                "rate_limited", "Too many evidence searches; wait a minute."
            ),
        )
    try:
        return await asyncio.wait_for(
            search_more_evidence(request_body),
            timeout=settings.analyze_timeout_seconds,
        )
    except TimeoutError:
        return JSONResponse(
            status_code=504,
            content=to_error_body("timeout", "The search took too long; try again."),
        )
    except EvidenceSearchError:
        return JSONResponse(
            status_code=502,
            content=to_error_body(
                "search_failed", "The evidence search could not be completed."
            ),
        )
