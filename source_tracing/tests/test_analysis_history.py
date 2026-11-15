from datetime import datetime, timezone
from source_tracing.store import PostgresStore
from source_tracing.tests.test_video_claims import RecordingConn


class RowsConn(RecordingConn):
    def __iter__(self): return iter(self.row)


def test_bounded_recent_query_and_parameterized_load():
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    conn = RowsConn([('a', 'trace', 'Claim', 'insufficient_evidence', now, None)])
    store = PostgresStore(connect_fn=lambda: conn)
    rows = store.list_analyses(1000000)
    sql, params = conn.calls[-1]
    assert params == (50,) and 'LIMIT %s' in sql and 'ORDER BY created_at DESC, analysis_id DESC' in sql
    assert rows[0]['checked_at'] is None and rows[0]['saved_at'] == now.isoformat()
    conn.row = ('a', {'claim': 'Claim'}, now)
    record = store.load_analysis("a' OR 1=1")
    sql, params = conn.calls[-1]
    assert params == ("a' OR 1=1",) and "a' OR" not in sql
    assert record['storage_backend'] == 'neon_postgres'
