"""Suite-wide isolation: offline tests must never see real credentials.

Without this, a test that misses the cache falls through to a live trace using the
developer's source_tracing/.env (Bright Data key, DATABASE_URL) and spends credits.
The socket.connect guards in individual tests do not catch asyncio connections on
Windows, so isolation has to happen at the configuration level.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from source_tracing import config

CREDENTIAL_VARS = (
    "BRIGHTDATA_API_KEY", "BRIGHTDATA_API_TOKEN", "BRIGHTDATA_SERP_ZONE",
    "DATABASE_URL", "DATABASE_URL_UNPOOLED", "TRUTHTRACE_CACHE_DIR", "TRUTHTRACE_MOCK",
)


@pytest.fixture(autouse=True)
def no_real_credentials(monkeypatch, tmp_path):
    """Point the .env file at a missing path and clear credential env vars (tests may set their own)."""
    monkeypatch.setattr(config, "ENV_FILE", Path(tmp_path) / "no-credentials.env")
    for name in CREDENTIAL_VARS:
        monkeypatch.delenv(name, raising=False)
