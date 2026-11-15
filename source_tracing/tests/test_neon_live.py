"""REAL Postgres/Neon integration test. Skipped unless TEST_DATABASE_URL is set.

Use a DEVELOPMENT database or branch only. TEST_DATABASE_URL is deliberately
separate from DATABASE_URL so this never runs by accident against the demo DB.

Isolation: the test creates its own throwaway schema (tt_test_<random>), runs
the additive migration inside it, and drops ONLY that schema afterwards. It
never touches existing tables. Use a direct (non-pooled) URL.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

import psycopg
import pytest

from sample_pages import sample_trace_result
from source_tracing.store import PostgresStore, StoreError, StoredInvestigation, apply_migrations, cache_key, require_tls

URL = os.environ.get("TEST_DATABASE_URL", "").strip()
pytestmark = pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL not set; real-Neon test skipped")

CLAIM = "The council approved a 12% increase in the water rate"
T0 = datetime(2026, 11, 14, 20, 0, tzinfo=timezone.utc)


@pytest.fixture
def store():
    conninfo = require_tls(URL)
    schema = f"tt_test_{uuid.uuid4().hex[:10]}"
    admin = psycopg.connect(conninfo, autocommit=True, connect_timeout=15)
    admin.execute(f'CREATE SCHEMA "{schema}"')

    def connect():
        return psycopg.connect(conninfo, connect_timeout=15, options=f"-c search_path={schema}")

    s = PostgresStore(connect_fn=connect)
    try:
        yield s
    finally:
        admin.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')  # our own throwaway schema only
        admin.close()


def test_migrate_save_reload_and_cache_on_real_postgres(store):
    assert store.migrate() == ["001_init.sql", "002_analyses.sql", "003_video_claims.sql", "004_video_extraction_time.sql", "005_recent_analyses.sql"]
    assert store.migrate() == ["001_init.sql", "002_analyses.sql", "003_video_claims.sql", "004_video_extraction_time.sql", "005_recent_analyses.sql"]  # idempotent

    original = StoredInvestigation(result=sample_trace_result(), cache_key=cache_key(CLAIM), claim=CLAIM,
                                   retrieved_at=T0, meta={"serp_requests": 2})
    store.save(original)
    store.save(original)  # duplicate save is a no-op

    back = store.load(original.result.investigation_id)
    assert back.result == original.result
    assert back.retrieved_at == T0 and back.meta == {"serp_requests": 2}
    assert [s.date for s in back.result.sources] == [s.date for s in original.result.sources]
    assert store.find_cached(cache_key(CLAIM)).result == original.result
    assert store.find_cached(cache_key("unrelated claim")) is None

    with store._connect() as conn:
        counts = [conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
                  for t in ("tt_investigations", "tt_sources", "tt_edges")]
    assert counts == [1, 5, 4]


def test_save_analysis_on_real_postgres(store):
    store.migrate()
    response = {"investigation_id": "inv-1", "status": "contradicted", "sources": [{"id": "F1"}]}
    store.save_analysis("an-1", "inv-1", CLAIM, "contradicted", "Rated false [F1].", response)
    store.save_analysis("an-1", "inv-1", CLAIM, "contradicted", "Rated false [F1].", response)  # no-op
    with store._connect() as conn:
        rows = conn.execute("SELECT analysis_id, investigation_id, status, response FROM tt_analyses").fetchall()
    assert rows == [("an-1", "inv-1", "contradicted", response)]
    assert store.load_analysis("an-1")["response"] == response
    assert store.list_analyses(1)[0]["analysis_id"] == "an-1"
    assert store.list_analyses(1)[0]["checked_at"] is None
    with pytest.raises(StoreError):  # status CHECK constraint
        store.save_analysis("an-2", "inv-1", CLAIM, "false", "x", response)


def test_video_claim_cache_on_real_postgres(store):
    store.migrate()
    record = {"video_id": "N-q2z4N_Rsk", "window_start": 90, "window_end": 165, "prompt_version": 1,
              "checkable": True, "claim": "DART changed Dimorphos's orbit by 32 minutes.",
              "timestamp_seconds": 141, "quote": "altered Dimorphos orbit by 32 minutes", "speaker": "Bill Nelson", "extracted_at": T0}
    assert store.find_video_claim("N-q2z4N_Rsk", 90, 165, 1) is None
    store.save_video_claim(record)
    store.save_video_claim({**record, "claim": "ignored duplicate"})  # no-op on conflict
    cached = store.find_video_claim("N-q2z4N_Rsk", 90, 165, 1)
    assert {key: cached[key] for key in record} == record
    assert cached["created_at"] is not None and cached["extracted_at"] == T0
    assert store.find_video_claim("N-q2z4N_Rsk", 90, 165, 2) is None  # other prompt version
    assert store.find_video_claim("N-q2z4N_Rsk", 105, 180, 1) is None  # other window
