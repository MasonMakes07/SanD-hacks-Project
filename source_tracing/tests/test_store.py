"""Stage 4 tests: cache key, storage, cache-first tracing.

MOCKED here: PostgresStore runs against FakeConn, an in-memory stand-in for a
psycopg connection that emulates this module's own statements. It verifies the
Python logic (transaction, ordering, round trip, error mapping), NOT that the
SQL is valid on Postgres. The REAL-database test is test_neon_live.py, which
needs TEST_DATABASE_URL and is skipped otherwise.

REAL here: JsonFileStore (real files in tmp_path).
"""

from __future__ import annotations

import asyncio
import copy
import json
import re
import socket
from datetime import date, datetime, timezone

import httpx
import psycopg
import pytest

from sample_pages import PAGES, SOURCES, sample_trace_result
from source_tracing.cached_trace import trace_with_cache
from source_tracing.models import TraceResult
from source_tracing.pages import PageFetcher
from source_tracing.store import (
    ALGORITHM_VERSION, SCHEMA_VERSION, JsonFileStore, PostgresStore, StoredInvestigation, StoreError,
    apply_migrations, cache_key, normalize_claim, require_tls,
)

T0 = datetime(2026, 11, 14, 20, 0, tzinfo=timezone.utc)
CLAIM = "The council approved a 12% increase in the water rate"
SECRET_URL = "postgresql://user:hunter2-secret@ep-example-123.us-west-2.aws.neon.tech/db?sslmode=require"


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def blocked(*a, **k):
        raise AssertionError("offline tests must not open sockets")

    monkeypatch.setattr(socket.socket, "connect", blocked)


def stored(result=None, claim=CLAIM, retrieved_at=T0, **meta) -> StoredInvestigation:
    return StoredInvestigation(result=result or sample_trace_result(), cache_key=cache_key(claim),
                               claim=claim, retrieved_at=retrieved_at, meta=meta or {"serp_requests": 2})


# ---------- in-memory psycopg stand-in (MOCK) ----------

class FakeCursor:
    def __init__(self, rows=(), rowcount=-1):
        self._rows, self.rowcount = list(rows), rowcount

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def __iter__(self):
        return iter(self._rows)


class FakeDb:
    def __init__(self):
        self.inv, self.src, self.edges = {}, {}, {}
        self.log: list[tuple[str, tuple]] = []
        self.fail_on: str | None = None
        self.connect_error: Exception | None = None

    def snapshot(self):
        return copy.deepcopy((self.inv, self.src, self.edges))

    def restore(self, snap):
        self.inv, self.src, self.edges = snap

    def connect(self):
        if self.connect_error:
            raise self.connect_error
        return FakeConn(self)


class FakeConn:
    def __init__(self, db: FakeDb):
        self.db, self._snap, self.closed = db, None, False

    def __enter__(self):
        self._snap = self.db.snapshot()
        return self

    def __exit__(self, exc_type, *a):
        if exc_type is not None:
            self.db.restore(self._snap)  # rollback
        return False

    def close(self):
        self.closed = True

    def execute(self, sql, params=()):
        db = self.db
        db.log.append((sql, params))
        if db.fail_on and db.fail_on in sql:
            raise psycopg.OperationalError("server closed the connection unexpectedly")
        s = " ".join(sql.split())
        if s.startswith("INSERT INTO tt_investigations"):
            inv_id = params[0]
            if inv_id in db.inv:
                return FakeCursor(rowcount=0)
            db.inv[inv_id] = dict(cache_key=params[1], claim=params[2], earliest=params[6],
                                  retrieved_at=params[7], meta=params[8].obj)
            return FakeCursor(rowcount=1)
        if s.startswith("INSERT INTO tt_sources"):
            inv_id, sid, pos, title, url, d, ex = params
            if (inv_id, sid) in db.src or any(k[0] == inv_id and v["url"] == url for k, v in db.src.items()):
                raise psycopg.errors.UniqueViolation("duplicate key value violates unique constraint")
            db.src[(inv_id, sid)] = dict(pos=pos, title=title, url=url, date=d, excerpt=ex)
            return FakeCursor(rowcount=1)
        if s.startswith("INSERT INTO tt_edges"):
            inv_id, a, b, t, pos, ev = params
            if (inv_id, a) not in db.src or (inv_id, b) not in db.src:
                raise psycopg.errors.ForeignKeyViolation("violates foreign key constraint")
            if (inv_id, a, b, t) in db.edges:
                raise psycopg.errors.UniqueViolation("duplicate key value violates unique constraint")
            db.edges[(inv_id, a, b, t)] = dict(pos=pos, evidence=json.loads(json.dumps(ev.obj)))
            return FakeCursor(rowcount=1)
        if s.startswith("SELECT investigation_id, cache_key"):
            if "cache_key = %s" in s:
                rows = sorted(((k, v) for k, v in db.inv.items() if v["cache_key"] == params[0]),
                              key=lambda kv: (-kv[1]["retrieved_at"].timestamp(), kv[0]))
                inv_id = rows[0][0] if rows else None
            else:
                inv_id = params[0] if params[0] in db.inv else None
            if inv_id is None:
                return FakeCursor()
            v = db.inv[inv_id]
            return FakeCursor([(inv_id, v["cache_key"], v["claim"], v["earliest"], v["retrieved_at"], v["meta"])])
        if s.startswith("SELECT source_id, title"):
            rows = sorted(((v["pos"], k[1], v) for k, v in db.src.items() if k[0] == params[0]), key=lambda x: x[0])
            return FakeCursor([(sid, v["title"], v["url"], v["date"], v["excerpt"]) for _, sid, v in rows])
        if s.startswith("SELECT source_id, target_id"):
            rows = sorted(((v["pos"], k, v) for k, v in db.edges.items() if k[0] == params[0]), key=lambda x: x[0])
            return FakeCursor([(k[1], k[2], k[3], v["evidence"]) for _, k, v in rows])
        raise AssertionError(f"FakeConn does not understand: {s[:80]}")


@pytest.fixture
def db():
    return FakeDb()


@pytest.fixture
def pg(db):
    return PostgresStore(connect_fn=db.connect)


# ---------- cache key ----------

def test_cache_key_normalizes_claim():
    base = cache_key(CLAIM)
    assert cache_key("  the COUNCIL approved a 12%  increase in the water rate. ") == base
    assert cache_key('"The council approved a 12% increase in the water rate"') == base
    assert cache_key("The council approved a 15% increase in the water rate") != base
    assert normalize_claim("It’s  fine!") == "it's  fine".replace("  ", " ")


def test_cache_key_includes_versions():
    assert cache_key(CLAIM, schema_version=SCHEMA_VERSION + 1) != cache_key(CLAIM)
    assert cache_key(CLAIM, algorithm_version=ALGORITHM_VERSION + 1) != cache_key(CLAIM)
    with pytest.raises(ValueError):
        cache_key("  ...  ")


# ---------- save + reload (mocked connection) ----------

def test_save_then_reload_is_exactly_equivalent(pg):
    original = stored()
    pg.save(original)
    back = pg.load(original.result.investigation_id)
    assert back.result == original.result
    assert back.result.to_contract_dict() == original.result.to_contract_dict()
    assert back.retrieved_at == T0 and back.claim == CLAIM and back.meta == {"serp_requests": 2}
    assert back.backend == "neon_postgres"


def test_evidence_survives_the_round_trip(pg):
    original = stored()
    pg.save(original)
    back = pg.load(original.result.investigation_id).result
    assert {e.relationship_type for e in back.graph_edges} == {"explicit_citation", "similar_wording"}
    cite = next(e for e in back.graph_edges if e.relationship_type == "explicit_citation")
    assert cite.evidence.hyperlink == "https://example.org/council-vote"
    assert cite.evidence.anchor_text == "first reported by Example Org"
    orig_cite = next(e for e in original.result.graph_edges if e.relationship_type == "explicit_citation")
    assert cite.evidence.retrieved_at == orig_cite.evidence.retrieved_at
    sim = next(e for e in back.graph_edges if e.relationship_type == "similar_wording")
    assert sim.evidence.target_excerpt and sim.evidence.note and sim.evidence.similarity


def test_null_dates_stay_null_and_earliest_is_preserved(pg):
    original = stored()
    assert [s.date for s in original.result.sources] == [date(2026, 3, 2), date(2026, 3, 4), None, None, None]
    pg.save(original)
    back = pg.load(original.result.investigation_id).result
    assert [s.date for s in back.sources] == [s.date for s in original.result.sources]
    assert back.earliest_observed_source.id == "s1"


def test_investigation_with_no_dates_and_no_edges(pg):
    empty = TraceResult(investigation_id="empty-1", sources=[], earliest_observed_source=None,
                        graph_nodes=[], graph_edges=[])
    pg.save(stored(empty))
    back = pg.load("empty-1")
    assert back.result == empty and back.result.earliest_observed_source is None
    undated = sample_trace_result().model_copy(update={"investigation_id": "undated-1"})
    undated = TraceResult(
        investigation_id="undated-1", sources=[s.model_copy(update={"date": None}) for s in undated.sources],
        earliest_observed_source=None, graph_nodes=undated.graph_nodes, graph_edges=[])
    pg.save(stored(undated))
    assert pg.load("undated-1").result.earliest_observed_source is None


def test_unknown_investigation_loads_as_none(pg):
    assert pg.load("nope") is None and pg.find_cached(cache_key("never seen")) is None


def test_saving_the_same_investigation_twice_creates_no_duplicates(pg, db):
    original = stored()
    pg.save(original)
    pg.save(original)
    assert len(db.inv) == 1 and len(db.src) == 5 and len(db.edges) == 4


def test_duplicate_source_url_rolls_back_the_whole_save(pg, db):
    r = sample_trace_result()
    dup = r.sources[1].model_copy(update={"id": "s9"})
    bad = TraceResult(investigation_id="dup-1", sources=[*r.sources, dup], earliest_observed_source=r.earliest_observed_source,
                      graph_nodes=[*r.graph_nodes, r.graph_nodes[1].model_copy(update={"id": "s9"})], graph_edges=r.graph_edges)
    with pytest.raises(StoreError, match="UniqueViolation"):
        pg.save(stored(bad))
    assert db.inv == {} and db.src == {} and db.edges == {}  # nothing half-saved


def test_edge_to_missing_source_is_rejected_by_the_database_and_rolled_back(pg, db):
    r = sample_trace_result()
    bad = r.model_copy(update={"investigation_id": "fk-1", "sources": r.sources[:1],
                               "earliest_observed_source": r.sources[0]})
    with pytest.raises(StoreError, match="ForeignKeyViolation"):
        pg.save(stored(bad))
    assert db.inv == {} and db.src == {}


# ---------- cache lookup ----------

def test_find_cached_returns_the_newest_entry_for_a_key(pg):
    old = stored(sample_trace_result().model_copy(update={"investigation_id": "old"}), retrieved_at=T0)
    new = stored(sample_trace_result().model_copy(update={"investigation_id": "new"}),
                 retrieved_at=datetime(2026, 11, 15, tzinfo=timezone.utc))
    pg.save(old); pg.save(new)
    assert pg.find_cached(cache_key(CLAIM)).result.investigation_id == "new"


def test_different_version_is_a_cache_miss(pg):
    pg.save(stored())
    assert pg.find_cached(cache_key(CLAIM, algorithm_version=ALGORITHM_VERSION + 1)) is None


# ---------- cache-first tracing ----------

class CountingSearch:
    def __init__(self, urls=None):
        self.calls = 0
        self.urls = urls or [s.url for s in SOURCES[:3]]

    async def search(self, query, budget):
        self.calls += 1
        budget.charge()
        return {"organic": [{"rank": i, "link": u, "title": "Council approves water rate increase vote",
                             "description": "council water rate increase"} for i, u in enumerate(self.urls, 1)]}


class CountingFetcher(PageFetcher):
    def __init__(self):
        self.requests = []
        urls = {s.url: s.id for s in SOURCES}

        def handler(req):
            self.requests.append(str(req.url))
            return httpx.Response(200, headers={"content-type": "text/html"}, content=PAGES[urls[str(req.url)]].encode())

        from source_tracing.urls import check_public_http_url
        super().__init__(transport=httpx.MockTransport(handler), url_check=check_public_http_url)


def test_repeat_claim_is_a_cache_hit_with_zero_external_requests(pg):
    search, fetcher = CountingSearch(), CountingFetcher()
    first = run(trace_with_cache(CLAIM, pg, search, fetcher, now=lambda: T0))
    assert not first.cache_hit and first.saved and first.serp_requests >= 1 and first.page_fetches == 3
    calls, fetches = search.calls, len(fetcher.requests)
    assert calls >= 1 and fetches == 3

    second = run(trace_with_cache("  the council APPROVED a 12% increase in the water rate. ", pg, search, fetcher,
                                  now=lambda: datetime(2030, 1, 1, tzinfo=timezone.utc)))
    assert second.cache_hit and second.saved
    assert search.calls == calls and len(fetcher.requests) == fetches  # nothing new sent
    assert second.serp_requests == 0 and second.page_fetches == 0
    assert second.retrieved_at == T0  # original retrieval time preserved, not "now"
    assert second.result == first.result
    assert second.storage_backend == "neon_postgres"


def test_different_claim_misses_the_cache(pg):
    search, fetcher = CountingSearch(), CountingFetcher()
    run(trace_with_cache(CLAIM, pg, search, fetcher))
    before = search.calls
    other = run(trace_with_cache("The council rejected the water rate increase", pg, search, fetcher))
    assert not other.cache_hit and search.calls > before


def test_empty_claim_is_rejected_before_any_work(pg, db):
    search = CountingSearch()
    with pytest.raises(ValueError):
        run(trace_with_cache("   ", pg, search, CountingFetcher()))
    assert search.calls == 0 and db.log == []


def test_live_trace_failure_saves_nothing(pg, db):
    class Boom:
        async def search(self, query, budget):
            budget.charge()
            from source_tracing.serp_client import SerpAuthError
            raise SerpAuthError("rejected")

    from source_tracing.serp_client import SerpAuthError
    with pytest.raises(SerpAuthError):
        run(trace_with_cache(CLAIM, pg, Boom(), CountingFetcher()))
    assert db.inv == {}


# ---------- database failure ----------

def test_connection_failure_maps_to_store_error_without_leaking_the_url(db):
    db.connect_error = psycopg.OperationalError(f"could not connect to {SECRET_URL}")
    with pytest.raises(StoreError) as info:
        PostgresStore(connect_fn=db.connect).find_cached("k")
    assert "hunter2" not in str(info.value) and "neon.tech" not in str(info.value)
    assert "database unavailable" in str(info.value)


def test_database_down_on_lookup_still_traces_live_with_a_warning(db):
    db.connect_error = psycopg.OperationalError("down")
    search, fetcher = CountingSearch(), CountingFetcher()
    out = run(trace_with_cache(CLAIM, PostgresStore(connect_fn=db.connect), search, fetcher))
    assert not out.cache_hit and not out.saved and out.result.sources
    assert any("cache lookup failed" in w for w in out.warnings)
    assert any("result not saved" in w for w in out.warnings)
    assert search.calls <= 2 and len(fetcher.requests) <= 5  # same caps as any uncached trace


def test_save_failure_midway_rolls_back_and_is_reported(pg, db):
    db.fail_on = "INSERT INTO tt_edges"
    out = run(trace_with_cache(CLAIM, pg, CountingSearch(), CountingFetcher()))
    assert not out.saved and out.result.sources
    assert db.inv == {} and db.src == {} and db.edges == {}
    assert any("result not saved" in w for w in out.warnings)


def test_queries_are_parameterized(pg, db):
    evil = "x'; DROP TABLE tt_sources; --"
    pg.find_cached(cache_key(evil))
    pg.save(stored(claim=evil))
    for sql, _ in db.log:
        assert "DROP" not in sql and "x'" not in sql


# ---------- TLS / config ----------

def test_tls_is_required():
    assert "sslmode=require" in require_tls("postgresql://u:p@h.example.org/db")
    assert require_tls("postgresql://u:p@h/db?sslmode=verify-full").endswith("verify-full")
    for mode in ("disable", "allow", "prefer"):
        with pytest.raises(StoreError, match="TLS is required") as info:
            require_tls(f"postgresql://u:secretpw@h/db?sslmode={mode}")
        assert "secretpw" not in str(info.value)
    with pytest.raises(StoreError, match="not a valid"):
        require_tls("not a url at all ===")


def test_missing_database_url_is_a_clear_error(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(StoreError, match="DATABASE_URL is not set"):
        PostgresStore()


def test_database_url_in_documented_env_file_is_used(tmp_path, monkeypatch):
    from source_tracing import config, service
    monkeypatch.delenv("DATABASE_URL", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text("DATABASE_URL=postgresql://u:p@h.example.org/db?sslmode=require\n")
    monkeypatch.setattr(config, "ENV_FILE", env_file)
    store = service.build_store()
    assert isinstance(store, PostgresStore)


def test_direct_postgres_store_uses_documented_env_file(tmp_path, monkeypatch):
    from source_tracing import config
    monkeypatch.delenv("DATABASE_URL", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text("DATABASE_URL=postgresql://u:p@h.example.org/db?sslmode=require\n")
    monkeypatch.setattr(config, "ENV_FILE", env_file)
    assert isinstance(PostgresStore(), PostgresStore)


def test_database_error_never_echoes_server_detail_with_secret():
    class Broken:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def execute(self, *args):
            raise psycopg.OperationalError("server echoed DATABASE_URL password=hunter2")
        def close(self):
            pass

    store = PostgresStore(connect_fn=lambda: Broken())
    with pytest.raises(StoreError) as info:
        store.load("example")
    assert "hunter2" not in str(info.value)


# ---------- migrations ----------

def test_migration_file_is_additive_and_idempotent_by_construction():
    sql = (__import__("pathlib").Path(apply_migrations.__globals__["MIGRATIONS_DIR"]) / "001_init.sql").read_text()
    code = "\n".join(l.split("--", 1)[0] for l in sql.splitlines())
    for stmt in [s.strip() for s in code.split(";") if s.strip()]:
        assert re.match(r"CREATE (TABLE|INDEX) IF NOT EXISTS ", stmt), stmt[:60]
    # ON DELETE CASCADE is a foreign-key rule, not a statement that deletes anything.
    assert not re.search(r"\b(DROP|TRUNCATE|UPDATE)\b|(?<!ON )\bDELETE\b", code, re.I)


def test_migration_runner_refuses_destructive_sql(tmp_path):
    (tmp_path / "002_bad.sql").write_text("CREATE TABLE IF NOT EXISTS x (a int);\nDROP TABLE tt_sources;")

    class C:
        def transaction(self):
            raise AssertionError("must not execute")

    with pytest.raises(StoreError, match="destructive"):
        apply_migrations(C(), tmp_path)


# ---------- local JSON demo backup (REAL files) ----------

def test_json_backup_round_trip_and_cache_hit(tmp_path):
    store = JsonFileStore(tmp_path / "demo")
    original = stored()
    store.save(original)
    assert store.backend == "local_json"
    back = store.load(original.result.investigation_id)
    assert back.result == original.result and back.retrieved_at == T0 and back.backend == "local_json"
    assert store.find_cached(cache_key(CLAIM)).result == original.result
    assert store.find_cached(cache_key("other claim")) is None
    raw = json.loads((tmp_path / "demo" / f"{original.result.investigation_id}.json").read_text())
    assert raw["storage"] == "local_json_demo_backup_not_neon"


def test_json_backup_cache_flow_is_labeled_and_makes_no_requests(tmp_path):
    store = JsonFileStore(tmp_path)
    search, fetcher = CountingSearch(), CountingFetcher()
    run(trace_with_cache(CLAIM, store, search, fetcher, now=lambda: T0))
    n = search.calls
    hit = run(trace_with_cache(CLAIM, store, search, fetcher))
    assert hit.cache_hit and hit.storage_backend == "local_json" and search.calls == n and hit.retrieved_at == T0


def test_json_backup_rejects_path_tricks_and_corrupt_files(tmp_path):
    store = JsonFileStore(tmp_path)
    with pytest.raises(StoreError):
        store.load("../../etc/passwd")
    (tmp_path / "bad.json").write_text("{not json")
    assert store.find_cached("anything") is None  # corrupt files are skipped, not fatal
    with pytest.raises(StoreError, match="unreadable"):
        store.load("bad")


def test_json_backup_write_failure_is_a_store_error(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    with pytest.raises(StoreError, match="could not write"):
        JsonFileStore(blocker / "sub").save(stored())


def test_fetched_material_survives_direct_postgres_save_and_read(pg):
    from source_tracing.models import Passage
    original = stored()
    source = original.result.sources[0]
    source.evidence_kind = 'fetched_article'
    source.retrieved_at = T0
    source.passages = [Passage(id='s1-p1', text='Exact fetched words.', retrieved_at=T0)]
    source.provenance = {'role': 'originating_article', 'excerpt_kind': 'search_snippet'}
    pg.save(original)
    back = pg.load(original.result.investigation_id).result.sources[0]
    assert back == source

def test_slow_cache_lookup_and_write_are_bounded_without_changing_live_result(monkeypatch):
    import time
    from source_tracing import cached_trace
    monkeypatch.setattr(cached_trace, 'CACHE_IO_TIMEOUT_S', 0.01)
    class SlowStore:
        backend = 'local_json'
        def find_cached(self, key):
            time.sleep(0.08)
            return None
        def save(self, record):
            time.sleep(0.08)
    async def check():
        start = asyncio.get_running_loop().time()
        result = await trace_with_cache(CLAIM, SlowStore(), CountingSearch(), CountingFetcher())
        elapsed = asyncio.get_running_loop().time() - start
        assert result.result.sources
        assert not result.cache_hit and not result.saved
        assert any('cache lookup failed' in message for message in result.warnings)
        assert any('result not saved' in message for message in result.warnings)
        assert elapsed < 0.075
    run(check())
