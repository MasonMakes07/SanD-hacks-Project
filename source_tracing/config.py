"""Configuration from the environment. Secrets are never logged or printed.

Variables:
  BRIGHTDATA_API_KEY     Bright Data API key (BRIGHTDATA_API_TOKEN also accepted)
  BRIGHTDATA_SERP_ZONE   An existing serp- or unblocker-type zone in YOUR account.
                         Never guessed: no zone, no live call.

A `source_tracing/.env` file is read if present; real environment variables win.
"""

from __future__ import annotations

import os
import math
from dataclasses import dataclass, field
from pathlib import Path

ENV_FILE = Path(__file__).parent / ".env"

# Per-investigation caps, including seed and followed body-link attempts.
MAX_SERP_REQUESTS = 2
MAX_SOURCES = 10
MAX_PAGE_FETCHES = 5
TARGET_SOURCES = 7


class ConfigError(RuntimeError):
    """Required configuration is missing. Message names the variable, never its value."""


def _read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip().removeprefix("export ").strip()
        values[key] = value.strip().strip('"').strip("'")
    return values


@dataclass(frozen=True)
class BrightDataSettings:
    api_key: str = field(repr=False)
    serp_zone: str
    timeout_s: float = 30.0
    max_retries: int = 1
    country: str = "us"
    language: str = "en"

    def __repr__(self) -> str:  # never show the key
        return f"BrightDataSettings(serp_zone={self.serp_zone!r}, api_key=<redacted>)"


def load_settings(env: dict[str, str] | None = None, env_file: Path | None = ENV_FILE) -> BrightDataSettings:
    merged = _read_env_file(env_file) if env_file else {}
    merged.update(os.environ if env is None else env)
    api_key = (merged.get("BRIGHTDATA_API_KEY") or merged.get("BRIGHTDATA_API_TOKEN") or "").strip()
    zone = (merged.get("BRIGHTDATA_SERP_ZONE") or "").strip()
    missing = [n for n, v in (("BRIGHTDATA_API_KEY", api_key), ("BRIGHTDATA_SERP_ZONE", zone)) if not v]
    if missing:
        raise ConfigError(f"missing configuration: {', '.join(missing)}")
    try:
        timeout = float(merged.get("BRIGHTDATA_TIMEOUT_S") or 30.0)
    except ValueError:
        raise ConfigError("BRIGHTDATA_TIMEOUT_S must be a finite number") from None
    if not math.isfinite(timeout):
        raise ConfigError("BRIGHTDATA_TIMEOUT_S must be a finite number")
    return BrightDataSettings(api_key=api_key, serp_zone=zone, timeout_s=min(max(timeout, 1.0), 60.0))
