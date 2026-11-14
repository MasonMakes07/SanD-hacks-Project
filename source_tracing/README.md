# source_tracing (Person 3)

Source retrieval, source graph, Neon storage and caching for TruthTrace.

**Status: offline implementation complete.** `trace_claim` uses cache-first
tracing and live Bright Data when credentials are configured. Set
`TRUTHTRACE_MOCK=1` only for the labeled UI fixture. The real public-web demo
backup is in `live_samples/nasa_dart_cache/`; it is not a Bright Data or Neon run.

## Usage (Person 2)

```python
from source_tracing import trace_claim

result = await trace_claim(claim, seed_url=None)   # -> TraceResult
fields = result.to_contract_dict()                 # investigation_id, sources,
                                                   # earliest_observed_source,
                                                   # graph_nodes, graph_edges
```

Run from the repo root so `source_tracing` is importable. It raises
`ValueError` for an empty claim or a non-http(s) `seed_url`.

## Result shape

- `sources[]`: `id`, `title`, `url`, `date` (`"YYYY-MM-DD"` or `null`), `excerpt`
- `earliest_observed_source`: one of `sources` with a date, or `null`.
  It is the earliest *observed* dated source, not the original publisher.
- `graph_nodes[]`: `id`, `title`, `url`
- `graph_edges[]`: `source_id`, `target_id`, `relationship_type`
  (`explicit_citation` | `similar_wording` | `same_fact_check`), `evidence`
- `evidence`: `url`, `excerpt`, `hyperlink`, `retrieved_at`, `similarity`

## Retrieval (Stage 2)

`retrieve_sources(claim, BrightDataSerpClient(load_settings()))` runs at most
2 Google SERP requests through Bright Data, retries included. It returns at
most 10 normalized, deduplicated sources, each with internal provenance
(query, rank, original URL, `retrieved_at`). It is wired into `trace_claim`
after the cache lookup.

- Config: copy `.env.example` to `source_tracing/.env` (Git-ignored), then
  set `BRIGHTDATA_API_KEY` and `BRIGHTDATA_SERP_ZONE`, which must be an
  existing zone in your account.
- Dates are kept only from an explicit absolute `date` field. Relative
  text and snippets are never used, so a missing date stays `null`.
- Absolute public HTTP(S) article links are accepted. A relative Google
  `/url` result is accepted only when it contains a validated public HTTP(S)
  destination. Other relative links remain rejected; no destination is guessed.
- One live check (spends credits):
  `python -m source_tracing.live_smoke --yes "claim"`

## Source graph (Stage 3)

`pipeline.run_trace(claim, searcher, PageFetcher())` fetches up to 5 source
pages directly (no Bright Data credits) and builds the graph in `graph.py`.

- `explicit_citation` A -> B: page A's body contains a hyperlink to page B.
  Navigation, footer, hidden and sponsored links do not count, and a search
  snippet or matching headline never does.
- `similar_wording` A -- B: a sentence in each (8+ words, boilerplate
  excluded) shares at least half of its word 3-grams. Undirected; both
  sentences are quoted in the evidence. It is not evidence of copying.
- `same_fact_check`: not produced. It needs fact-check identity from Person 2.
- Dates come from publisher metadata on the page (`article:published_time`,
  JSON-LD `datePublished`), and only when all declarations agree.
- `fixtures/sample_graph_synthetic.json` is a SYNTHETIC worked example.

## Storage and cache (Stage 4)

`cached_trace.trace_with_cache(claim, store, searcher, fetcher)` answers a
repeated claim from storage with zero Bright Data requests. The cache key is
the normalized claim plus `SCHEMA_VERSION` and `ALGORITHM_VERSION` in
`store.py`; bump either to invalidate old entries (they are never deleted).

- `PostgresStore` (Neon) reads `DATABASE_URL` only and requires TLS.
  Run `PostgresStore().migrate()` once against a direct (non-pooled) URL.
  `migrations/001_init.sql` only creates `tt_*` tables if missing. `DATABASE_URL`
  is also read from `source_tracing/.env`; environment variables take priority.
- `JsonFileStore(dir)` is a local demo backup labeled `local_json`. It is
  not Neon and does not demonstrate Neon persistence.
- If the database is down, a lookup failure traces live (same 2 SERP / 5
  fetch caps) and a save failure returns the result with `saved=False`.
- Real-database test: set `TEST_DATABASE_URL` to a dev database (not the demo
  DB) and run `pytest source_tracing/tests/test_neon_live.py`. It works in its
  own throwaway schema and drops only that schema.

## Real public-web demo backup

`python -m source_tracing.demo_cache` fetches two public NASA pages selected
from an observed article link, validates the graph, and saves a local JSON
backup. It makes **zero Bright Data requests** and writes no Neon data. The
checked-in backup was retrieved on November 14, 2026; the record preserves
its own retrieval time and edge times. The pure Person 3 response is
`fixtures/example_investigation_response.json`.

For an offline repeat, run from the repository root:

```bash
TRUTHTRACE_CACHE_DIR=source_tracing/live_samples/nasa_dart_cache \
  python -c 'import asyncio; from source_tracing import trace_claim; from source_tracing.demo_cache import CLAIM; print(asyncio.run(trace_claim(CLAIM)).investigation_id)'
```

This cache hit needs no Bright Data key. Use `trace_claim_detailed(CLAIM)` to
confirm `cache_hit=True`, `storage_backend="local_json"`, and the original
`retrieved_at`. A live Bright Data + Neon investigation remains a separate
integration gate once free/event credentials are configured.

## Ownership

This folder owns only the fields above. It never sets `claim`, `status` or
`explanation`, never calls Gemini or the Fact Check API, and defines no
FastAPI route. Those belong to Person 2. The extension belongs to Person 1.

## Tests

```bash
python3 -m venv source_tracing/.venv
source_tracing/.venv/bin/pip install -r source_tracing/requirements-dev.txt
source_tracing/.venv/bin/python -m pytest source_tracing
```

Tests are offline and fail if anything opens a network connection.
