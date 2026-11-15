# TruthTrace API Contract (owner: Person 2)

Base URL (local): `http://127.0.0.1:8000`

## Auth
Every request except `GET /api/health` must send the header
`X-TruthTrace-Key: <value of TRUTHTRACE_API_KEY>`.
Store it in the extension's config file next to the backend URL (not in code).

CORS allows only `chrome-extension://<id>` origins (`ALLOWED_ORIGIN_REGEX`).

## GET /api/health
`200 {"status": "ok", "mock": false}` — public; use it to show "backend offline".

## POST /api/analyze

### Request
| Field | Type | Rules |
|---|---|---|
| text | string | 1–20000 chars; code/markup/injection-looking text is rejected |
| url | string | http(s) only, max 2048 chars, no credentials |
| mode | string | `manual` or `automatic` |
| video_time_seconds | number, optional | YouTube only: viewer's position (0–86400) |
| video_duration_seconds | number, optional | YouTube only: video length (>0, ≤86400) |

Unknown fields are rejected.

**Social media posts:** no extra field is needed. Send the post's text and its
permalink as `url`. The backend detects X/Twitter, Reddit, Threads, Facebook,
Instagram, TikTok, Bluesky, YouTube, LinkedIn, Truth Social and Mastodon from the
URL, tells Gemini the text is a post (hashtags, @mentions, emoji, links, "RT" and
thread counters are ignored), and the response shape is unchanged.

### Response 200
| Field | Type | Notes |
|---|---|---|
| investigation_id | string | unique id |
| claim | string | claim checked (or a text preview if none was checkable) |
| status | string | `supported`, `contradicted`, `misleading`, `insufficient_evidence` |
| explanation | string | cites sources as `[F1]`, `[S2]` — ids match `sources[].id` |
| sources | array | `{id, title, url, date \| null, excerpt}` |
| earliest_observed_source | object \| null | same shape as a source |
| graph_nodes | array | `{id, title, url, date \| null, kind}`; kind = `article`, `fact_check`, `document` |
| graph_edges | array | `{source_id, target_id, relationship_type, evidence}` |
| claim_source | object \| null | YouTube checks: `{type: "video", video_url, timestamp, timestamp_seconds, timestamp_url, quote, speaker}`; otherwise `null` |

Source ids: `F<n>` = published fact-check (Google Fact Check Tools),
`S<n>` = retrieved web source (Bright Data, Person 3).
`relationship_type` is one of `explicit_citation`, `similar_wording`,
`same_fact_check`. Every edge has non-empty evidence and both endpoints exist
in `graph_nodes`. Similar wording is never proof of copying — label it so.

### YouTube videos
Send the watch/Shorts/live/youtu.be link as `url`, the video title as `text`, and
`video_time_seconds` (current position) plus `video_duration_seconds`. The backend
has Gemini watch a ~75 s clip ending 15 s after that position (rounded to 15 s, low
frame rate and resolution), extracts the claim nearest the end, and returns where it
was said in `claim_source` (`timestamp_url` opens the video at that moment).
`timestamp` and `timestamp_seconds` may be `null` if the model gave no usable time.
Clip claims are cached in memory and in Neon (`tt_video_claims`), so re-checking the
same moment skips the video model. If the video cannot be analyzed or has no
checkable claim in the clip, the title is checked instead and `claim_source` is
`null`. Without `video_time_seconds` the first 90 s are analyzed.

### Errors
All errors return `{"error": "<code>", "message": "<text>"}`.

| HTTP | error | When |
|---|---|---|
| 400 | input_rejected | text looks like code, or URL unsafe |
| 401 | unauthorized | missing/wrong `X-TruthTrace-Key` |
| 411 | length_required | POST without Content-Length |
| 413 | payload_too_large | body > MAX_BODY_BYTES |
| 422 | invalid_request | schema violation (message lists field names only) |
| 429 | rate_limited | > RATE_LIMIT_PER_MINUTE requests per minute |
| 500 | internal_error | unexpected server error |
| 503 | server_misconfigured | server has no TRUTHTRACE_API_KEY |
| 504 | timeout | investigation exceeded ANALYZE_TIMEOUT_SECONDS |

External API failures (Gemini, Fact Check, Bright Data) do **not** produce
errors: the backend degrades to `insufficient_evidence` or a fact-check-only
result.

## Mock mode
Set `USE_MOCK=true` in `backend/.env`. `/api/analyze` then validates input and
returns this fixed response:

```json
{
  "investigation_id": "mock-0000000000000000",
  "claim": "The city cut its library budget by 40% this year.",
  "status": "contradicted",
  "explanation": "The approved city budget reduces library funding by about 4%, not 40% [S1]. A published fact-check rated the 40% figure false [F1].",
  "sources": [
    {
      "id": "F1",
      "title": "Fact check: Did the city cut its library budget by 40%?",
      "url": "https://example.org/fact-check/library-budget",
      "date": "2026-10-02",
      "excerpt": "Example Fact Desk fact-checked \"The city cut its library budget by 40% this year\" and rated it: False."
    },
    {
      "id": "S1",
      "title": "City council approves 2026 budget",
      "url": "https://example.com/news/council-approves-budget",
      "date": "2026-09-15",
      "excerpt": "The approved budget reduces library funding by 4%, from $25M to $24M."
    },
    {
      "id": "S2",
      "title": "Viral post misstates library cuts",
      "url": "https://example.net/blog/library-cuts-viral",
      "date": null,
      "excerpt": "A widely shared post claims a 40% cut, citing the council budget."
    }
  ],
  "earliest_observed_source": {
    "id": "S1",
    "title": "City council approves 2026 budget",
    "url": "https://example.com/news/council-approves-budget",
    "date": "2026-09-15",
    "excerpt": "The approved budget reduces library funding by 4%, from $25M to $24M."
  },
  "graph_nodes": [
    {
      "id": "F1",
      "title": "Fact check: Did the city cut its library budget by 40%?",
      "url": "https://example.org/fact-check/library-budget",
      "date": "2026-10-02",
      "kind": "fact_check"
    },
    {
      "id": "S1",
      "title": "City council approves 2026 budget",
      "url": "https://example.com/news/council-approves-budget",
      "date": "2026-09-15",
      "kind": "article"
    },
    {
      "id": "S2",
      "title": "Viral post misstates library cuts",
      "url": "https://example.net/blog/library-cuts-viral",
      "date": null,
      "kind": "article"
    }
  ],
  "graph_edges": [
    {
      "source_id": "S2",
      "target_id": "S1",
      "relationship_type": "explicit_citation",
      "evidence": "S2 links to the council budget article (S1)."
    },
    {
      "source_id": "F1",
      "target_id": "S1",
      "relationship_type": "explicit_citation",
      "evidence": "The fact-check cites the approved budget figures from S1."
    }
  ]
}
```

## Person 3 integration
Replace the stub bodies in `app/source_provider.py`:

- `async fetch_sources(claim) -> SourceBundle` — sources with ids `S1, S2, …`,
  graph nodes, evidence-backed edges, earliest dated source (or None).
- `async save_investigation(result: AnalyzeResponse) -> None` — store in Neon.

The pipeline already merges your bundle with fact-checks, drops duplicate or
non-http sources, drops edges without evidence or with unknown endpoints, and
contains any exception you raise (the request still succeeds).

## Ask the Detective

Both endpoints need `X-TruthTrace-Key`. Neither changes a verdict; only `POST /api/analyze` produces one.

### POST /api/chat
Stateless follow-up question about an investigation the extension already holds.

| Field | Type | Rules |
|---|---|---|
| question | string | 1–500 chars; code-like text is rejected |
| claim, status, explanation | | copied from the `/api/analyze` response |
| sources | array (≤40) | `{id, title, url, date \| null, excerpt}`; ids match `^[A-Za-z]{1,3}\d{1,4}$` |
| edges | array (≤80) | `{source_id, target_id, relationship_type}` |
| history | array (≤8) | `{role: "user" \| "detective", text}` |
| page_text | string (≤8000, optional) | the page text the user checked, so questions about it can be answered |

Response `200 {answer, cited_source_ids, stances, grounded, basis}`. `basis` says what the answer rests on: `evidence` (retrieved sources), `page` (the checked page, which is not verification) or `background` (the model's general knowledge; label it as unverified). `answer` cites sources as `[id]`; every marker matches a source in the request (others are removed, as are URLs). `stances` is `{source_id, stance}` with stance `supports`, `contradicts` or `unclear`, for cited sources only. `grounded` is false when no citation survived; show such answers as reasoning, not evidence. Errors: `502 chat_unavailable`.

### POST /api/evidence/search
One explicit search through the existing source-tracing pipeline (Bright Data SERP, Neon cache, deduplication). An uncached call spends at most the normal per-investigation search budget; a repeated call is served from cache. Limited to 6 per minute per client.

| Field | Type | Rules |
|---|---|---|
| claim | string | 1–600 chars |
| mode | string | `more` or `counter` (steers the query toward rebuttals and fact-checks) |
| known | array (≤60) | `{id, url}` of sources already in the investigation |

Response `200 {mode, retrieved_at, from_cache, sources, graph_nodes, graph_edges, already_known}`. Only sources whose URL is not in `known` are returned, with new ids (`m1…` for `more`, `c1…` for `counter`). `graph_edges` are relationships the tracer established that touch a new source; an end may be a `known` id. Errors: `502 search_failed`, `503 search_unavailable` (mock mode), `504 timeout`, `429 rate_limited`.

### Optional evidence material (#14)

Sources may include `evidence_kind` (`fetched_article`, `search_snippet`,
`social_snippet`, `fact_check_summary`, `unknown`), `retrieved_at`, bounded
`provenance`, and up to two `passages`: `{id,text,kind:'fetched_article',retrieved_at}`.
Each passage is a literal window of at most 900 characters from fetched body text,
selected for claim-term relevance. A fetched body can have no relevant passage.
`excerpt` remains a search snippet or fact-check summary, never silently replaced
by an article passage. Legacy missing metadata means unknown.

Responses include validated `cited_source_ids` and up to eight `evidence_quotes`:
`{source_id,passage_id,quote,explanation,wording_verified}`. Passage IDs identify
exact supplied article material. Null/empty passage ID identifies the supplied
600-character excerpt and can only validate explicitly labeled snippets/summaries.
Matching collapses Unicode whitespace runs and trims ends; case, punctuation,
and wording remain significant. Unknown IDs or unmatched model quotations
produce an insufficient-evidence finding without verified quotations. This
checks wording, never correctness of interpretation. Video statements remain
model-extracted input context, not independently verified article evidence.

### Optional provenance (#16)

`checked_at` is the analysis time, not publication, retrieval or extraction.
`provenance.source_trace` carries `{cache_hit,retrieved_at,storage_backend,saved,
serp_requests,page_fetches,error}` from `trace_claim_detailed`. Counts cover source
tracing only. `neon_postgres`, `local_json`, `mock`, and `none` are distinct.
A cache hit retains original retrieval time. Mock and failed traces have unknown
retrieval time. Legacy unknown values remain null.

`provenance.video_extraction` and `claim_source.provenance` independently carry
`cache_hit`, `storage_backend` (`memory`, `neon_postgres`, `none`), `extracted_at`,
and optional `cached_at`. Neon insertion time is not extraction time. Migration
004 adds nullable extraction time without backfilling legacy records.
`provenance.saved_analysis` separately carries `{saved,storage_backend,saved_at}`;
unknown/pending state must never be displayed as successful persistence.

### Saved full analyses / history (#18)

New completed checks have unique `analysis_id`, original `checked_at`, and
`page_context`: `{url,text,title,mode,platform,video_time_seconds,
video_duration_seconds}`. Request `page_title` is optional (300 characters).
Capture position/duration are distinct from `claim_source.timestamp_seconds`.
A cached `investigation_id` may be shared by multiple full analyses.

Authenticated `GET /api/analyses?limit=20` (1–50) returns `{items,limit}` with
`analysis_id,investigation_id,claim,status,checked_at,saved_at,storage_backend`.
Authenticated `GET /api/analyses/{analysis_id}` returns the complete original
AnalyzeResponse, sets `reopened:true`, and reports the actual storage backend.
It performs a bounded store read only: no extraction, Gemini, Fact Check or
retrieval work. Missing records return 404, corrupt/incompatible records 422,
and unavailable/unconfigured full-analysis storage 503. Legacy absent check
and capture metadata remains unknown. Saved timestamps do not become check times.

Full analyses currently require Neon; source-trace local JSON backup is not
misrepresented as full-result history. Persistence is attempted with a two-second
wait; failure preserves the visible result and marks storage unknown. A timed-out
write may complete later, so it must not be described as definitely absent.
Migration 005 adds an index for the bounded recent query; context stays in the
existing response JSONB column.
