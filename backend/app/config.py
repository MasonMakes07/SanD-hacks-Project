"""Loads settings from backend/.env and the process environment."""

import os
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

from app.models import Settings

ENV_FILE_PATH = Path(__file__).resolve().parent.parent / ".env"
NEON_ENV_FILE_PATH = Path(__file__).resolve().parents[2] / ".env.local"
DEFAULT_ORIGIN_REGEX = r"^chrome-extension://[a-p]{32}$"


# Reads a boolean environment variable, treating 1/true/yes/on as True.
def _read_bool(name: str, default: bool) -> bool:
    """Return the boolean value of an environment variable or the default."""
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    return raw_value.strip().lower() in {"1", "true", "yes", "on"}


# Reads a positive number from the environment, falling back on bad values.
def _read_number(name: str, default: float) -> float:
    """Return a positive float from an environment variable or the default."""
    try:
        parsed_value = float(os.getenv(name, default))
    except ValueError:
        return default
    return parsed_value if parsed_value > 0 else default


# Builds the cached Settings object once per process.
@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Load backend/.env and Neon's .env.local (never overriding env vars)."""
    load_dotenv(ENV_FILE_PATH, override=False)
    load_dotenv(NEON_ENV_FILE_PATH, override=False)
    return Settings(
        gemini_api_key=os.getenv("GEMINI_API_KEY", "").strip(),
        gemini_model=os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite").strip(),
        gemini_video_model=os.getenv(
            "GEMINI_VIDEO_MODEL", "gemini-3.5-flash-lite"
        ).strip(),
        gemini_fallback_model=os.getenv(
            "GEMINI_FALLBACK_MODEL", "gemini-flash-lite-latest"
        ).strip(),
        gemini_hedge_seconds=_read_number("GEMINI_HEDGE_SECONDS", 4),
        video_timeout_seconds=_read_number("VIDEO_TIMEOUT_SECONDS", 30),
        factcheck_api_key=os.getenv("FACTCHECK_API_KEY", "").strip(),
        truthtrace_api_key=os.getenv("TRUTHTRACE_API_KEY", "").strip(),
        use_mock=_read_bool("USE_MOCK", False),
        allowed_origin_regex=os.getenv(
            "ALLOWED_ORIGIN_REGEX", DEFAULT_ORIGIN_REGEX
        ).strip(),
        request_timeout_seconds=_read_number("REQUEST_TIMEOUT_SECONDS", 12),
        analyze_timeout_seconds=_read_number("ANALYZE_TIMEOUT_SECONDS", 90),
        rate_limit_per_minute=int(_read_number("RATE_LIMIT_PER_MINUTE", 20)),
        max_body_bytes=int(_read_number("MAX_BODY_BYTES", 100_000)),
    )
