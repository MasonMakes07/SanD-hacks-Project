"""FastAPI entry point: POST /api/analyze and GET /api/health."""

import asyncio
import logging
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.analysis import run_investigation
from app.config import get_settings
from app.middleware import AUTH_HEADER_NAME, security_middleware
from app.mock import build_mock_response
from app.models import AnalyzeRequest, AnalyzeResponse, InputRejectedError
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
