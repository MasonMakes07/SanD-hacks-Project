"""Apply the additive tt_ migrations to Neon over the DIRECT (non-pooled) URL.

    python -m source_tracing.migrate          (run from the repo root)

Uses DATABASE_URL_UNPOOLED (written by `neon env pull` / `neon deploy` into the
repo-root .env.local). Falls back to DATABASE_URL only if it is not pooled.
Never prints a connection string.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from psycopg.conninfo import conninfo_to_dict

from . import config
from .store import PostgresStore, StoreError

NEON_ENV_FILE = Path(__file__).resolve().parent.parent / ".env.local"


def direct_database_url() -> str:
    """Return the direct Neon URL from the environment, .env.local or source_tracing/.env."""
    merged = {**config._read_env_file(config.ENV_FILE), **config._read_env_file(NEON_ENV_FILE)}
    merged.update({key: value for key, value in os.environ.items() if value.strip()})
    direct_url = (merged.get("DATABASE_URL_UNPOOLED") or "").strip()
    if direct_url:
        return direct_url
    fallback_url = (merged.get("DATABASE_URL") or "").strip()
    if not fallback_url:
        raise StoreError("DATABASE_URL_UNPOOLED is not set; run `neon env pull`")
    try:
        host = conninfo_to_dict(fallback_url).get("host") or ""
    except Exception:
        raise StoreError("DATABASE_URL is not a valid Postgres connection string") from None
    if "-pooler" in host:
        raise StoreError("only a pooled URL is set; migrations need DATABASE_URL_UNPOOLED")
    return fallback_url


def main() -> int:
    try:
        applied = PostgresStore(url=direct_database_url()).migrate()
    except StoreError as exc:
        print(f"FAILED: {exc}")
        return 1
    print(f"applied (idempotent): {', '.join(applied)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
