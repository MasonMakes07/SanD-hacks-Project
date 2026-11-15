"""Persistence for traced investigations: Neon Postgres, plus a labeled JSON backup.

PostgresStore uses psycopg 3 with parameterized SQL only. DATABASE_URL is the
only credential source; TLS is required; the URL is never printed or put in
an error message.

JsonFileStore is a LOCAL DEMO BACKUP. It is labeled `local_json` everywhere
and must never be presented as Neon persistence.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Protocol

from psycopg import Connection, Error as PsycopgError, connect
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg.types.json import Jsonb

from . import config
from .models import GraphEdge, Source, TraceResult, node_for

# Bump SCHEMA_VERSION when the stored/returned shape changes, ALGORITHM_VERSION
# when query building, filtering or graph rules change. Either bump makes old
# cache entries unreachable (they are never deleted).
SCHEMA_VERSION = 2
ALGORITHM_VERSION = 4  # adaptive article coverage and full selected-page relationship detection

MIGRATIONS_DIR = Path(__file__).parent / "migrations"
VIDEO_CLAIM_COLUMNS = (
    "video_id", "window_start", "window_end", "prompt_version", "checkable",
    "claim", "timestamp_seconds", "quote", "speaker", "extracted_at",
)
_TLS_OK = {"require", "verify-ca", "verify-full"}


class StoreError(RuntimeError):
    """Storage failed. The message never contains the connection string."""


@dataclass
class StoredInvestigation:
    result: TraceResult
    cache_key: str
    claim: str
    retrieved_at: datetime
    meta: dict = field(default_factory=dict)
    backend: str = ""


class Store(Protocol):
    backend: str

    def save(self, stored: StoredInvestigation) -> None: ...
    def load(self, investigation_id: str) -> StoredInvestigation | None: ...
    def find_cached(self, cache_key: str) -> StoredInvestigation | None: ...


# ---------- cache key ----------

_EDGE_PUNCT = re.compile(r"^[\W_]+|[\W_]+$")


def normalize_claim(claim: str) -> str:
    """Case/whitespace/edge-punctuation-insensitive form of a claim."""
    text = " ".join(claim.replace("’", "'").replace("“", '"').replace("”", '"').split())
    return _EDGE_PUNCT.sub("", text).casefold()


def cache_key(claim: str, *, schema_version: int = SCHEMA_VERSION, algorithm_version: int = ALGORITHM_VERSION, seed_url: str | None = None) -> str:
    normalized = normalize_claim(claim)
    if not normalized:
        raise ValueError("claim must contain words")
    from .urls import normalize_url, UnsafeUrlError
    try:
        seed = normalize_url(seed_url) if seed_url else ""
    except (UnsafeUrlError, ValueError):
        # Blocked seeds never fetch; isolate their cache context without storing credentials.
        seed = "blocked:" + hashlib.sha256(str(seed_url).encode()).hexdigest()
    material = f"schema={schema_version}|algo={algorithm_version}|{normalized}|seed={seed}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


# ---------- connection ----------

def database_url(env: dict[str, str] | None = None) -> str:
    merged = config._read_env_file(config.ENV_FILE)
    merged.update(os.environ if env is None else env)
    url = (merged.get("DATABASE_URL") or "").strip()
    if not url:
        raise StoreError("DATABASE_URL is not set")
    return url


def require_tls(url: str) -> str:
    """Return a conninfo that demands TLS. Refuse URLs that explicitly weaken it."""
    try:
        params = conninfo_to_dict(url)
    except Exception:
        raise StoreError("DATABASE_URL is not a valid Postgres connection string") from None
    mode = params.get("sslmode")
    if mode is None:
        return make_conninfo(url, sslmode="require")
    if mode not in _TLS_OK:
        raise StoreError(f"DATABASE_URL sets sslmode={mode}; TLS is required (use sslmode=require)")
    return url


def apply_migrations(conn: Connection, migrations_dir: Path = MIGRATIONS_DIR) -> list[str]:
    """Run each additive migration file in order, in one transaction. Idempotent.

    Use a direct (non-pooled) Neon URL. Files must be additive; this refuses
    any file containing a destructive keyword.
    """
    applied = []
    destructive = re.compile(r"\b(drop|truncate|delete\s+from|alter\s+table\s+\w+\s+drop)\b", re.I)
    for path in sorted(migrations_dir.glob("*.sql")):
        sql = path.read_text(encoding="utf-8")
        code = "\n".join(line.split("--", 1)[0] for line in sql.splitlines())
        if destructive.search(code):
            raise StoreError(f"migration {path.name} contains a destructive statement; refusing to run")
        try:
            with conn.transaction():
                conn.execute(sql)
        except PsycopgError as exc:
            raise StoreError(f"migration {path.name} failed: {type(exc).__name__}") from None
        applied.append(path.name)
    return applied


# ---------- Postgres ----------

ConnectFn = Callable[[], Connection]


class PostgresStore:
    backend = "neon_postgres"

    def __init__(self, connect_fn: ConnectFn | None = None, *, url: str | None = None, connect_timeout: int = 10):
        if connect_fn is None:
            conninfo = require_tls(url or database_url())

            def connect_fn() -> Connection:
                return connect(conninfo, connect_timeout=connect_timeout)

        self._connect = connect_fn

    def _run(self, action):
        try:
            conn = self._connect()
        except PsycopgError as exc:
            raise StoreError(f"database unavailable ({type(exc).__name__})") from None
        try:
            with conn:  # commits on success, rolls back on exception
                return action(conn)
        except StoreError:
            raise
        except PsycopgError as exc:
            raise StoreError(f"database error ({type(exc).__name__})") from None
        finally:
            try:
                conn.close()
            except PsycopgError:
                pass

    def migrate(self) -> list[str]:
        return self._run(apply_migrations)

    def save(self, stored: StoredInvestigation) -> None:
        """Insert the investigation, its sources and edges in ONE transaction.

        Saving an investigation_id that already exists is a no-op (ON CONFLICT
        DO NOTHING), so a retried save cannot create duplicates.
        """
        r = stored.result
        material = {}
        for source in r.sources:
            extras = source.model_dump(mode="json", include={"evidence_kind", "retrieved_at", "passages", "provenance"})
            if extras != {"evidence_kind": "unknown", "retrieved_at": None, "passages": [], "provenance": {}}:
                material[source.id] = extras
        meta = {**stored.meta, **({"source_material": material} if material else {})}

        def action(conn: Connection):
            cur = conn.execute(
                """INSERT INTO tt_investigations
                   (investigation_id, cache_key, claim, normalized_claim, schema_version,
                    algorithm_version, earliest_source_id, retrieved_at, meta)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (investigation_id) DO NOTHING""",
                (r.investigation_id, stored.cache_key, stored.claim, normalize_claim(stored.claim),
                 SCHEMA_VERSION, ALGORITHM_VERSION,
                 r.earliest_observed_source.id if r.earliest_observed_source else None,
                 stored.retrieved_at, Jsonb(meta)),
            )
            if cur.rowcount == 0:
                return  # already saved
            for pos, s in enumerate(r.sources):
                conn.execute(
                    """INSERT INTO tt_sources (investigation_id, source_id, position, title, url, published_date, excerpt)
                       VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                    (r.investigation_id, s.id, pos, s.title, s.url, s.date, s.excerpt),
                )
            for pos, e in enumerate(r.graph_edges):
                conn.execute(
                    """INSERT INTO tt_edges (investigation_id, source_id, target_id, relationship_type, position, evidence)
                       VALUES (%s,%s,%s,%s,%s,%s)""",
                    (r.investigation_id, e.source_id, e.target_id, e.relationship_type, pos,
                     Jsonb(e.evidence.model_dump(mode="json"))),
                )

        self._run(action)

    def save_analysis(self, analysis_id: str, investigation_id: str, claim: str, status: str,
                      explanation: str, response: dict) -> None:
        """Record one /api/analyze verdict in tt_analyses. Re-saving the same analysis_id is a no-op."""

        def action(conn: Connection):
            conn.execute(
                """INSERT INTO tt_analyses
                   (analysis_id, investigation_id, claim, status, explanation, response)
                   VALUES (%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (analysis_id) DO NOTHING""",
                (analysis_id, investigation_id, claim, status, explanation, Jsonb(response)),
            )

        self._run(action)

    def list_analyses(self, limit: int = 20) -> list[dict]:
        """Bounded recent full analyses, independently identified from traces."""
        limit = max(1, min(int(limit), 50))
        def action(conn):
            rows = conn.execute(
                """SELECT analysis_id, investigation_id, claim, status, created_at,
                          response->>'checked_at' FROM tt_analyses
                   ORDER BY created_at DESC, analysis_id DESC LIMIT %s""", (limit,))
            return [dict(analysis_id=aid, investigation_id=iid, claim=claim,
                         status=status, saved_at=saved.isoformat() if saved else None,
                         checked_at=checked, storage_backend=self.backend)
                    for aid, iid, claim, status, saved, checked in rows]
        return self._run(action)

    def load_analysis(self, analysis_id: str) -> dict | None:
        def action(conn):
            row = conn.execute(
                """SELECT analysis_id, response, created_at FROM tt_analyses
                   WHERE analysis_id = %s""", (analysis_id,)).fetchone()
            if row is None:
                return None
            return {"analysis_id": row[0], "response": row[1],
                    "saved_at": row[2].isoformat() if row[2] else None,
                    "storage_backend": self.backend}
        return self._run(action)

    def save_video_claim(self, record: dict) -> None:
        """Cache one video-clip claim in tt_video_claims. Re-saving the same clip is a no-op.

        `record` has every key in VIDEO_CLAIM_COLUMNS.
        """

        def action(conn: Connection):
            conn.execute(
                """INSERT INTO tt_video_claims
                   (video_id, window_start, window_end, prompt_version, checkable, claim,
                    timestamp_seconds, quote, speaker, extracted_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (video_id, window_start, window_end, prompt_version) DO NOTHING""",
                tuple(record.get(key) for key in VIDEO_CLAIM_COLUMNS),
            )

        self._run(action)

    def find_video_claim(self, video_id: str, window_start: int, window_end: int,
                         prompt_version: int) -> dict | None:
        """The cached claim for exactly this clip and prompt version, or None."""

        def action(conn: Connection):
            row = conn.execute(
                f"""SELECT {", ".join(VIDEO_CLAIM_COLUMNS)}, created_at FROM tt_video_claims
                    WHERE video_id = %s AND window_start = %s AND window_end = %s
                    AND prompt_version = %s""",  # the column list is a fixed constant
                (video_id, window_start, window_end, prompt_version),
            ).fetchone()
            return dict(zip((*VIDEO_CLAIM_COLUMNS, "created_at"), row)) if row else None

        return self._run(action)

    def _load_where(self, where: str, params: tuple) -> StoredInvestigation | None:
        def action(conn: Connection):
            row = conn.execute(
                f"""SELECT investigation_id, cache_key, claim, earliest_source_id, retrieved_at, meta
                    FROM tt_investigations WHERE {where}""",  # `where` is a fixed literal below
                params,
            ).fetchone()
            if row is None:
                return None
            inv_id, key, claim, earliest_id, retrieved_at, meta = row
            sources = [
                Source(id=sid, title=title, url=url, date=d, excerpt=ex)
                for sid, title, url, d, ex in conn.execute(
                    """SELECT source_id, title, url, published_date, excerpt FROM tt_sources
                       WHERE investigation_id = %s ORDER BY position""", (inv_id,))
            ]
            edges = [
                GraphEdge.model_validate({"source_id": a, "target_id": b, "relationship_type": t, "evidence": ev})
                for a, b, t, ev in conn.execute(
                    """SELECT source_id, target_id, relationship_type, evidence FROM tt_edges
                       WHERE investigation_id = %s ORDER BY position""", (inv_id,))
            ]
            material = (meta or {}).get("source_material", {})
            sources = [Source.model_validate({**s.model_dump(), **material.get(s.id, {})})
                       for s in sources]
            earliest = next((s for s in sources if s.id == earliest_id), None)
            result = TraceResult(
                investigation_id=inv_id, sources=sources, earliest_observed_source=earliest,
                graph_nodes=[node_for(s) for s in sources],
                graph_edges=edges,
            )
            return StoredInvestigation(result=result, cache_key=key, claim=claim,
                                       retrieved_at=retrieved_at, meta=meta or {}, backend=self.backend)

        try:
            return self._run(action)
        except ValueError as exc:  # pydantic rejected stored data
            raise StoreError(f"stored investigation is invalid: {type(exc).__name__}") from None

    def load(self, investigation_id: str) -> StoredInvestigation | None:
        return self._load_where("investigation_id = %s", (investigation_id,))

    def find_cached(self, cache_key: str) -> StoredInvestigation | None:
        return self._load_where(
            "investigation_id = (SELECT investigation_id FROM tt_investigations WHERE cache_key = %s "
            "ORDER BY retrieved_at DESC, investigation_id LIMIT 1)",
            (cache_key,),
        )


# ---------- no storage ----------

class NullStore:
    """Stores nothing, so every claim is traced live. Used when no storage is configured."""

    backend = "none"

    def save(self, stored: StoredInvestigation) -> None:
        pass

    def load(self, investigation_id: str) -> StoredInvestigation | None:
        return None

    def find_cached(self, cache_key: str) -> StoredInvestigation | None:
        return None


# ---------- local JSON demo backup ----------

class JsonFileStore:
    """LOCAL DEMO BACKUP. One JSON file per investigation. Not Neon, not a database."""

    backend = "local_json"

    def __init__(self, directory: Path):
        self._dir = Path(directory)

    def _path(self, investigation_id: str) -> Path:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", investigation_id):
            raise StoreError("invalid investigation id")
        return self._dir / f"{investigation_id}.json"

    def save(self, stored: StoredInvestigation) -> None:
        path = self._path(stored.result.investigation_id)
        record = {
            "storage": "local_json_demo_backup_not_neon",
            "cache_key": stored.cache_key, "claim": stored.claim,
            "retrieved_at": stored.retrieved_at.isoformat(), "meta": stored.meta,
            "result": stored.result.to_contract_dict(),
        }
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(record, indent=2), encoding="utf-8")
            tmp.replace(path)
        except OSError as exc:
            raise StoreError(f"could not write demo backup ({type(exc).__name__})") from None

    def _read(self, path: Path) -> StoredInvestigation:
        try:
            rec = json.loads(path.read_text(encoding="utf-8"))
            return StoredInvestigation(
                result=TraceResult.model_validate(rec["result"]), cache_key=rec["cache_key"], claim=rec["claim"],
                retrieved_at=datetime.fromisoformat(rec["retrieved_at"]), meta=rec.get("meta", {}), backend=self.backend,
            )
        except (OSError, ValueError, KeyError) as exc:
            raise StoreError(f"demo backup {path.name} is unreadable ({type(exc).__name__})") from None

    def load(self, investigation_id: str) -> StoredInvestigation | None:
        path = self._path(investigation_id)
        return self._read(path) if path.is_file() else None

    def find_cached(self, cache_key: str) -> StoredInvestigation | None:
        best: StoredInvestigation | None = None
        for path in sorted(self._dir.glob("*.json")) if self._dir.is_dir() else []:
            try:
                rec = self._read(path)
            except StoreError:
                continue
            if rec.cache_key == cache_key and (best is None or rec.retrieved_at > best.retrieved_at):
                best = rec
        return best
