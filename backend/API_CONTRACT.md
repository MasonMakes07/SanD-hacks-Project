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
