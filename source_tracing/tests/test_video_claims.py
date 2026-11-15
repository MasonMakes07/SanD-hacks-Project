"""Offline tests for the video-claim cache methods (no database)."""

from __future__ import annotations

from source_tracing.store import VIDEO_CLAIM_COLUMNS, PostgresStore

RECORD = {"video_id": "abcdefghijk", "window_start": 0, "window_end": 90, "prompt_version": 1,
          "checkable": True, "claim": "A claim.", "timestamp_seconds": 12, "quote": "a claim", "speaker": ""}


class RecordingConn:
    """Captures SQL and parameters; returns a canned row for SELECTs."""

    def __init__(self, row=None):
        self.calls, self.row = [], row

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def close(self):
        pass

    def execute(self, sql, params=()):
        self.calls.append((" ".join(sql.split()), params))
        return self

    def fetchone(self):
        return self.row


def test_save_video_claim_inserts_every_column_in_order_with_conflict_guard():
    conn = RecordingConn()
    PostgresStore(connect_fn=lambda: conn).save_video_claim(RECORD)
    sql, params = conn.calls[0]
    assert sql.startswith("INSERT INTO tt_video_claims") and "ON CONFLICT" in sql and "DO NOTHING" in sql
    assert params == tuple(RECORD[key] for key in VIDEO_CLAIM_COLUMNS)


def test_find_video_claim_is_parameterized_and_maps_columns():
    conn = RecordingConn(row=tuple(RECORD[key] for key in VIDEO_CLAIM_COLUMNS))
    found = PostgresStore(connect_fn=lambda: conn).find_video_claim("abcdefghijk", 0, 90, 1)
    sql, params = conn.calls[0]
    assert params == ("abcdefghijk", 0, 90, 1) and "abcdefghijk" not in sql
    assert found == RECORD


def test_find_video_claim_miss_returns_none():
    assert PostgresStore(connect_fn=lambda: RecordingConn(row=None)).find_video_claim("x" * 11, 0, 90, 1) is None
