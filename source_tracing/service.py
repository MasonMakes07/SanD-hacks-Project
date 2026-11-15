"""Person 3's entry point for Person 2: `await trace_claim(claim, seed_url)`.

    from source_tracing import trace_claim
    result = await trace_claim("claim text")        # -> TraceResult
    fields = result.to_contract_dict()              # merge into /api/analyze response

Behavior
  - Live path: cache lookup -> Bright Data SERP (<=2 requests) -> page fetch
    (<=10) -> evidence graph -> save. A repeated claim is served from storage.
  - Expected operational failures (missing credentials, Bright Data errors,
    database errors, timeout) return a valid EMPTY result: no sources, no edges,
    earliest_observed_source null. Nothing is invented. Call
    `trace_claim_detailed` to see why (`.warnings`, `.error`).
  - Programming/input errors (empty claim, non-http(s) seed_url) raise ValueError.
  - TRUTHTRACE_MOCK=1 returns a labelled fixture ("[MOCK]", id "MOCK-...")
    for building UI/API without credentials. Never set it for a real demo.
  - seed_url is validated and accepted for interface compatibility. It does
    not change retrieval or caching today.

Person 3 never sets claim, status or explanation; those are Person 2's.
"""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit

from .cached_trace import CachedTrace, trace_with_cache
from . import config
from .config import ConfigError, load_settings
from .models import TraceResult
from .pages import PageFetcher
from .serp_client import BrightDataSerpClient, SerpError
from .store import JsonFileStore, NullStore, PostgresStore, Store, StoreError

MOCK_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "mock_trace_result.json"
MOCK_ID_PREFIX = "MOCK-"
TRACE_TIMEOUT_S = 40.0

_TRUE = {"1", "true", "yes", "on"}


def is_mock_mode(env: dict[str, str] | None = None) -> bool:
    return (os.environ if env is None else env).get("TRUTHTRACE_MOCK", "").strip().lower() in _TRUE


@lru_cache(maxsize=1)
def _mock_payload() -> str:
    return MOCK_FIXTURE_PATH.read_text(encoding="utf-8")


def _validate_inputs(claim: str, seed_url: str | None) -> None:
    if not isinstance(claim, str) or not claim.strip():
        raise ValueError("claim must be a non-empty string")
    if seed_url is not None:
        parts = urlsplit(seed_url)
        if parts.scheme not in ("http", "https") or not parts.netloc:
            raise ValueError("seed_url must be an absolute http(s) URL or None")


def empty_result(investigation_id: str) -> TraceResult:
    return TraceResult(investigation_id=investigation_id, sources=[], earliest_observed_source=None,
                       graph_nodes=[], graph_edges=[])


@dataclass
class TraceService:
    """Wiring for one deployment. Tests inject fakes; production builds it from the environment."""

    searcher: object
    fetcher: PageFetcher
    store: Store
    timeout_s: float = TRACE_TIMEOUT_S

    async def trace(self, claim: str, seed_url: str | None = None) -> CachedTrace:
        _validate_inputs(claim, seed_url)
        try:
            return await asyncio.wait_for(
                trace_with_cache(claim, self.store, self.searcher, self.fetcher), self.timeout_s)
        except asyncio.TimeoutError:
            return _failed(f"trace timed out after {self.timeout_s:.0f}s", self.store)
        except SerpError as exc:
            return _failed(f"source search failed: {exc}", self.store)
        except StoreError as exc:
            return _failed(f"storage failed: {exc}", self.store)


def _failed(message: str, store: Store) -> CachedTrace:
    now = datetime.now(timezone.utc)
    return CachedTrace(
        result=empty_result(f"EMPTY-{now.strftime('%Y%m%dT%H%M%S')}"), cache_hit=False, retrieved_at=now,
        storage_backend=store.backend, saved=False, warnings=[message], error=message,
    )


def build_store(env: dict[str, str] | None = None) -> Store:
    merged = config._read_env_file(config.ENV_FILE)
    merged.update(os.environ if env is None else env)
    env = merged
    if env.get("DATABASE_URL", "").strip():
        return PostgresStore(url=env["DATABASE_URL"].strip())
    if env.get("TRUTHTRACE_CACHE_DIR", "").strip():
        return JsonFileStore(Path(env["TRUTHTRACE_CACHE_DIR"].strip()))  # labelled local_json backup
    return NullStore()


def build_service(env: dict[str, str] | None = None) -> TraceService:
    """Build cache access even when search credentials are absent.

    A cache miss still reports missing credentials as a safe empty result.
    """
    store = build_store(env)
    try:
        settings = load_settings(env=env, env_file=config.ENV_FILE)
        searcher = BrightDataSerpClient(settings)
    except ConfigError as exc:
        message = str(exc)

        class UnavailableSearcher:
            async def search(self, query, budget):
                raise SerpError(f"not configured: {message}")

        searcher = UnavailableSearcher()
    return TraceService(searcher=searcher, fetcher=PageFetcher(), store=store)


_service: TraceService | None = None


def set_service(service: TraceService | None) -> None:
    """Install (or clear) the service `trace_claim` uses. For tests and custom wiring."""
    global _service
    _service = service


async def trace_claim_detailed(claim: str, seed_url: str | None = None) -> CachedTrace:
    """Like trace_claim, plus cache_hit, retrieved_at, storage_backend, saved, warnings, error."""
    _validate_inputs(claim, seed_url)
    if is_mock_mode():
        result = TraceResult.model_validate(json.loads(_mock_payload()))
        return CachedTrace(result=result, cache_hit=False, retrieved_at=datetime.now(timezone.utc),
                           storage_backend="mock", saved=False, warnings=["MOCK MODE: fixture data, not real"])
    service = _service
    if service is None:
        try:
            service = build_service()
        except (ConfigError, StoreError) as exc:
            return _failed(f"not configured: {exc}", NullStore())
    return await service.trace(claim, seed_url)


async def trace_claim(claim: str, seed_url: str | None = None) -> TraceResult:
    """Trace sources for one claim. See the module docstring."""
    return (await trace_claim_detailed(claim, seed_url)).result
