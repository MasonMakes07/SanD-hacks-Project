# Person 3 handoff: source tracing for `/api/analyze`

Owner: Person 3. Consumer: Person 2 (FastAPI route, Gemini, Fact Check, status/explanation).
Status: **offline suite passing; live Bright Data SERP transport verified; real public-web demo backup verified.** Neon remains unverified live.

## Import and call

Run your app from the repo root so `source_tracing` is importable.

```python
from source_tracing import trace_claim

result = await trace_claim(claim, seed_url=None)   # -> TraceResult (never None)
person3_fields = result.to_contract_dict()         # plain JSON-ready dict

response = {
    "claim": claim,                  # Person 2
    "status": status,                # Person 2: supported/contradicted/misleading/insufficient_evidence
    "explanation": explanation,      # Person 2
    **person3_fields,                # investigation_id, sources, earliest_observed_source, graph_nodes, graph_edges
}
```

`trace_claim` is `async`. Network and database work are off the event loop. One call makes at most 2 Bright
Data SERP requests and 5 page fetches, and a repeated claim makes none (served from storage).

Need to know why a result is empty, or whether it came from cache?

```python
from source_tracing import trace_claim_detailed
d = await trace_claim_detailed(claim)
d.result, d.cache_hit, d.retrieved_at, d.storage_backend, d.saved, d.warnings, d.error
```

Those extra fields are internal. Do not put them in the API response unless we agree to.

## Behavior you can rely on

- **Failure returns an empty result, not an exception**: missing credentials on a cache miss, Bright Data error, database error,
  or timeout (60 s) gives `sources: []`, `graph_edges: []`, `earliest_observed_source: null`,
  `investigation_id` starting `EMPTY-`. Check `trace_claim_detailed(...).error` for the reason.
  Map an empty result to `insufficient_evidence`; no sources never means a claim is false.
- **Bad input raises `ValueError`**: empty claim, or `seed_url` not an absolute http(s) URL.
- **No verdicts**: Person 3 never sets `claim`, `status` or `explanation`.
- **`seed_url` is accepted but unused** today. It is validated and does not change retrieval or caching.
- **`earliest_observed_source`** is the earliest source with a publication date found in the page's own
  metadata. It is not the original publisher. It is `null` when no source has a reliable date, which is common.
- **`same_fact_check` edges are never produced.** That needs your fact-check identity and evidence. If you want
  them, tell me the shape and I will add it.
- **Edges are evidence-backed or absent.** `explicit_citation` means a retrieved page's body links to another
  retrieved page. `similar_wording` means two pages share a similar sentence, and is **not** proof of copying
  (each such edge carries a `note` saying so; please show it). Zero edges is a normal, valid result.
- `TRUTHTRACE_MOCK=1` returns a clearly labelled fixture (`[MOCK]` titles, `MOCK-` id) so you can build
  without credentials. **Never set it for the real demo.**

## Fields (checked against Plan.md section 4)

| Field | Shape |
|---|---|
| `investigation_id` | string |
| `sources[]` | `id`, `title`, `url` (http/https), `date` (`"YYYY-MM-DD"` or `null`), `excerpt` |
| `earliest_observed_source` | one of `sources`, or `null` |
| `graph_nodes[]` | `id`, `title`, `url`, `kind` (`article` or `social_post`). Same ids as `sources` |
| `graph_edges[]` | `source_id`, `target_id`, `relationship_type`, `evidence` |
| `evidence` | `url`, `excerpt`, `hyperlink`, `anchor_text`, `target_url`, `target_excerpt`, `retrieved_at`, `similarity`, `method`, `note` |

**Differences from Plan.md to agree on** (Person 2's `/api/analyze` mock was not in the repo when this was
written, so this is checked against the plan only):

1. `sources[]` has an extra `id`. Edges and nodes need it to refer to sources.
2. `evidence` is free-form in the plan; the shape above is what Person 1 can render. `similarity` is 0 to 1 and
   only set for `similar_wording`. `hyperlink` and `anchor_text` are only set for `explicit_citation`.
3. `date` is serialized as an ISO date string, not a timestamp.

If your mock differs, tell me. I will adapt inside `source_tracing/`, not your route.

## Example (SYNTHETIC)

Invented pages on `example.*` domains, not real articles. It is the Person 3 part of the response.
Edge `s2 -> s1` is a citation (s2's body links to s1). The three `similar_wording` edges are undirected
overlap, listed with the lower source first.

```json
{
  "investigation_id": "SYNTHETIC-sample-graph",
  "sources": [
    {
      "id": "s1",
      "title": "[SYNTHETIC] Council approves water rate increase",
      "url": "https://example.org/council-vote",
      "date": "2026-03-02",
      "excerpt": "[SYNTHETIC] snippet one"
    },
    {
      "id": "s2",
      "title": "[SYNTHETIC] What the water rate vote means",
      "url": "https://example.com/analysis",
      "date": "2026-03-04",
      "excerpt": "[SYNTHETIC] snippet two"
    },
    {
      "id": "s3",
      "title": "[SYNTHETIC] Water rates to rise after vote",
      "url": "https://example.net/wire-copy",
      "date": null,
      "excerpt": "[SYNTHETIC] snippet three"
    },
    {
      "id": "s4",
      "title": "[SYNTHETIC] Community garden opens downtown",
      "url": "https://example.edu/garden",
      "date": null,
      "excerpt": "[SYNTHETIC] snippet four"
    },
    {
      "id": "s5",
      "title": "[SYNTHETIC] Council approves water rate increase",
      "url": "https://example.info/same-headline",
      "date": null,
      "excerpt": "[SYNTHETIC] The council voted on Tuesday to approve a 12 percent increase in the water utility rate."
    }
  ],
  "earliest_observed_source": {
    "id": "s1",
    "title": "[SYNTHETIC] Council approves water rate increase",
    "url": "https://example.org/council-vote",
    "date": "2026-03-02",
    "excerpt": "[SYNTHETIC] snippet one"
  },
  "graph_nodes": [
    {
      "id": "s1",
      "title": "[SYNTHETIC] Council approves water rate increase",
      "url": "https://example.org/council-vote",
      "kind": "article"
    },
    {
      "id": "s2",
      "title": "[SYNTHETIC] What the water rate vote means",
      "url": "https://example.com/analysis",
      "kind": "article"
    },
    {
      "id": "s3",
      "title": "[SYNTHETIC] Water rates to rise after vote",
      "url": "https://example.net/wire-copy",
      "kind": "article"
    },
    {
      "id": "s4",
      "title": "[SYNTHETIC] Community garden opens downtown",
      "url": "https://example.edu/garden",
      "kind": "article"
    },
    {
      "id": "s5",
      "title": "[SYNTHETIC] Council approves water rate increase",
      "url": "https://example.info/same-headline",
      "kind": "article"
    }
  ],
  "graph_edges": [
    {
      "source_id": "s1",
      "target_id": "s2",
      "relationship_type": "similar_wording",
      "evidence": {
        "url": "https://example.org/council-vote",
        "excerpt": "The council voted on Tuesday to approve a 12 percent increase in the water utility rate starting in July.",
        "hyperlink": null,
        "anchor_text": null,
        "target_url": "https://example.com/analysis",
        "target_excerpt": "The council voted on Tuesday to approve a 12 percent increase in the water utility rate, the report said.",
        "retrieved_at": "2026-11-14T20:00:00Z",
        "similarity": 0.7,
        "method": "sentence_word_3gram_jaccard>=0.5_min_8_words;matching_sentence_pairs=1",
        "note": "Wording overlap only. It does not show that either source copied the other, or which was written first."
      }
    },
    {
      "source_id": "s1",
      "target_id": "s3",
      "relationship_type": "similar_wording",
      "evidence": {
        "url": "https://example.org/council-vote",
        "excerpt": "The council voted on Tuesday to approve a 12 percent increase in the water utility rate starting in July.",
        "hyperlink": null,
        "anchor_text": null,
        "target_url": "https://example.net/wire-copy",
        "target_excerpt": "The council voted on Tuesday to approve a 12 percent increase in the water utility rate starting in July.",
        "retrieved_at": "2026-11-14T20:00:00Z",
        "similarity": 1.0,
        "method": "sentence_word_3gram_jaccard>=0.5_min_8_words;matching_sentence_pairs=1",
        "note": "Wording overlap only. It does not show that either source copied the other, or which was written first."
      }
    },
    {
      "source_id": "s2",
      "target_id": "s1",
      "relationship_type": "explicit_citation",
      "evidence": {
        "url": "https://example.com/analysis",
        "excerpt": "The vote was first reported by Example Org earlier this week.",
        "hyperlink": "https://example.org/council-vote",
        "anchor_text": "first reported by Example Org",
        "target_url": "https://example.org/council-vote",
        "target_excerpt": null,
        "retrieved_at": "2026-11-14T20:00:00Z",
        "similarity": null,
        "method": "hyperlink_in_page_body",
        "note": "This page links to the other source. A link shows a reference, not agreement."
      }
    },
    {
      "source_id": "s2",
      "target_id": "s3",
      "relationship_type": "similar_wording",
      "evidence": {
        "url": "https://example.com/analysis",
        "excerpt": "The council voted on Tuesday to approve a 12 percent increase in the water utility rate, the report said.",
        "hyperlink": null,
        "anchor_text": null,
        "target_url": "https://example.net/wire-copy",
        "target_excerpt": "The council voted on Tuesday to approve a 12 percent increase in the water utility rate starting in July.",
        "retrieved_at": "2026-11-14T20:00:00Z",
        "similarity": 0.7,
        "method": "sentence_word_3gram_jaccard>=0.5_min_8_words;matching_sentence_pairs=1",
        "note": "Wording overlap only. It does not show that either source copied the other, or which was written first."
      }
    }
  ]
}
```

## Environment variable names

| Name | Needed for |
|---|---|
| `BRIGHTDATA_API_KEY` | Bright Data. Required for live use |
| `BRIGHTDATA_SERP_ZONE` | Name of an existing SERP or unblocker zone in the account. Required |
| `BRIGHTDATA_TIMEOUT_S` | Optional, 1 to 60 |
| `DATABASE_URL` | Neon Postgres. TLS required. Enables the cache and persistence |
| `TRUTHTRACE_CACHE_DIR` | Optional local JSON backup cache, used only when `DATABASE_URL` is unset. Labelled `local_json`, not Neon |
| `TRUTHTRACE_MOCK` | `1` returns the labelled fixture |

Put values in `source_tracing/.env` (Git-ignored), never in code or chat. `source_tracing/.env.example`
must stay blank.

## Setup, run, test

```bash
python3 -m venv .venv
.venv/bin/pip install -r source_tracing/requirements-dev.txt      # pydantic, httpx, psycopg[binary], pytest
cp source_tracing/.env.example source_tracing/.env                # then fill in locally

# one-time, direct (non-pooled) Neon URL, additive tables prefixed tt_
.venv/bin/python -c "from source_tracing.store import PostgresStore; print(PostgresStore().migrate())"

.venv/bin/python -m pytest source_tracing -q                      # offline suite
TEST_DATABASE_URL=<dev db url> .venv/bin/python -m pytest source_tracing/tests/test_neon_live.py -v   # real Neon, optional
.venv/bin/python -m source_tracing.live_smoke --yes "claim"       # ONE live Bright Data check; spends credits
```

## Demo cache and example response

- Claim: `NASA's DART impact altered Dimorphos's orbit around Didymos`.
- Real NASA pages were retrieved through direct public HTTP GETs. The source graph found an in-article link from the NASA announcement to NASA's imagery article and a measured wording overlap. The edge evidence includes the observed URL, quoted page text, method, and retrieval time. These are source relationships, not truth verdicts or copying claims.
- Local backup: `source_tracing/live_samples/nasa_dart_cache/WEB-21be62a3e8fb48048d8e7171a2dcd15b.json`. `source_method=public_web_manual_seed_not_brightdata`; `serp_requests=0`; storage label is `local_json_demo_backup_not_neon`.
- Standalone contract response: `source_tracing/fixtures/example_investigation_response.json`.
- Offline repeat from repo root: set `TRUTHTRACE_CACHE_DIR=source_tracing/live_samples/nasa_dart_cache`, leave `TRUTHTRACE_MOCK` unset, and call `await trace_claim_detailed(CLAIM)` with `CLAIM` from `source_tracing.demo_cache`. It returns `cache_hit=True` with the saved retrieval time, even without Bright Data credentials.
- To refresh the backup from the two public pages: `python -m source_tracing.demo_cache`. This performs two public GETs and no vendor API calls. Refreshing creates a new dated backup and updates the standalone example response; the cache selects the newest retrieval.

## Verified

- **Offline, mocked (passing):** the full call chain (search, page fetch, graph, store, `trace_claim`) against
  fake Bright Data responses and fake pages, with the output checked against Plan.md section 4: field names,
  clickable URLs, graph ids matching source ids, null dates, evidence on every edge, JSON-serializable. Also:
  cache hit with zero external requests, empty and failure results, bad input, concurrent calls.
- **Real files and public pages:** NASA page fetching, an observed hyperlink, publication metadata, graph generation, local JSON backup and credential-free cache read.
- **Live Bright Data:** the first smoke test used one SERP request, returned eight raw rows and retained two public sources. A second, explicitly authorized diagnostic request used exactly one attempt with retries disabled; seven of its eight `link` fields were relative paths. Its URL-shape-only record is `source_tracing/live_samples/serp_url_diagnostics.json`, with no links or tracking tokens. The first response did not save discarded raw rows, and the diagnostic record did not retain relative path names. The parser now accepts only validated relative Google `/url` targets; other relative paths remain rejected. This correction is regression-tested offline; no further credit-spending verification was performed.
- **NOT verified live:** Neon (SQL has never run on a real Postgres), and Person 2's actual endpoint (not in the repo yet).

## Blockers

1. A Bright Data key and zone are configured locally and the API call succeeded. The saved smoke result retained two sources; the newly corrected relative-link path has not been verified by another paid call. The public-web demo cache remains separate and does not demonstrate the Bright Data track.
2. No Neon `DATABASE_URL`, so the real-database test is skipped.
3. Person 2's `/api/analyze` mock has not been published to the repo; field agreement above is pending.
4. Real runs may yield few or no `explicit_citation` edges, since a link must point at the exact retrieved page.
5. The direct page fetcher checks that each hostname resolves to public addresses before requesting it, but the connection resolves DNS again. Keep the backend local for the hackathon demo; pin the checked address before exposing arbitrary search-result fetching as a public service.

## Final integration gate

Person 2 can import `trace_claim` now and merge `result.to_contract_dict()` into the agreed response. The actual Person 2 backend is absent from this checkout, so its route has not been exercised. The offline suite passes **214 tests**; the real Neon test skips without `TEST_DATABASE_URL`.

**Demo readiness:** GO for the labeled, cached NASA source-network demonstration. The Bright Data API transport is verified, but the full live Bright Data → page graph → Neon flow is not; Neon and Person 2 integration remain open. Do not present the public-web backup as a Bright Data SERP result or as Neon persistence.
