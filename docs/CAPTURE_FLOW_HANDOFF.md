# Capture flow handoff — Account 1

I am the implementation owner of #13, #21, #20, #22, #23 and #24. Ownership is
recorded on each GitHub issue. Work is on `codex/capture-flow` in the dedicated
`../TruthTrace-capture-flow` worktree; the user's original checkout is unchanged.
Initial integration baseline: `9b8a20e`; later reconciled with `35c3eb8` including
Account 2's evidence/history/reassessment work and Account 3's presentation.
Account 3 has already integrated the published `559ca7d` snapshot; this branch's
subsequent commits add final media/permission/cache checks and documentation.

## Issue status

| Issue | Implementation and checks | Remaining acceptance |
| --- | --- | --- |
| #13 | 90s server / 100s client; 35s extraction; shared primary/fallback deadlines; finite overrides and short-profile migration; 40s tracing, 2s cache IO, 2s full persistence; actual 46.3s panel response; real article/video and Neon receipts | Closed after Account 3 combined acceptance at `7d4f208` |
| #21 | Frozen capture/approved payload; distinct fresh capture vs retry/capture-again; completed finding remains on failure/cancel; request identity blocks stale completion | Closed after Account 3 combined acceptance at `7d4f208` |
| #20 | Explicit text/video intent, finite visible native-player capture, SPA metadata guard, exact preceding-second windows/version 2 cache, no unrelated opening/title fallback, model labels and safe replay; real video model and player verified separately | Closed after Account 3 combined acceptance/native Chrome checks at `7d4f208` |
| #22 | New check collapses/reopens capture controls without discarding results; compact expandable claim/finding context; uncertainty and synthetic warning retained; keyboard, 280/380px and reduced motion checked | Closed after Account 3 combined acceptance at `7d4f208` |
| #23 | Extraction-only endpoint, editable single proposal, cancel with no retrieval, exact approved claim bypasses extraction, signed immutable context, bounded validation; approval retained on retry | Closed after Account 3 combined acceptance at `7d4f208` |
| #24 | Bounded exact local locator with context and document identity; range highlight and removal without wrapping text; ambiguity, changed DOM, closed/navigation/access failures, editor exclusion and cleanup checked | Closed after independent Account 3 acceptance at `147146f` (verified code `ee44af3`) |

Account 3 recorded combined acceptance at `7d4f208` (tested tree `0035cc7`):
260 backend, 258 source-tracing, 24 extension unit and 49 browser tests passed,
with three opt-in database skips, plus native headed Chrome and actual FastAPI
mock/backend-off checks. After that gate, the owner closed #13, #20, #21, #22
and #23. The later #24 deleted-original/remaining-duplicate safety guard was
independently accepted at `147146f` after regression and native Chrome checks
on `ee44af3`; PR #31 is merged into integration and #24 is closed. See
`docs/INTEGRATION.md` for the superseding acceptance record.

No main merge or issue auto-closing keywords are used here. Plan.md protects
submission work starting Sunday 11 a.m.; no earlier precise commit cutoff was
supplied, so reviewable increments were committed/pushed promptly.

## Interfaces and shared-file resolution

`backend/API_CONTRACT.md` documents `/api/extract`, `approved_claim`,
`confirmation_token`, `capture_kind`, `page_title`, `input_context` and nullable
unknown video replay times. Approved claims have documented normalization and
are never extracted again. Tokens expire after 30 minutes and bind normalized
original text, URL/title, mode, kind and playback context. They preserve signed
model input provenance when a reader edits the statement. Backend history and
reassessment preserve the additive result context through model copies/JSON.

`createLifecycle` is authoritative for in-panel capture work. It freezes both
capture and request body. Capture failure clears the retry snapshot so it cannot
retry unrelated older input. Approval failure retries the same approved body;
extraction failure retries the original capture and asks for approval again.
Every continuation checks operation identity, including cancellation before a
late worker/model reply. Completed `renderResult(result,capture,{fresh})` stays
Account 3's renderer; source/graph/report internals were retained from its
presentation module. `capture-flow.css` isolates the required new layout hooks.

Observed `truthtrace:lifecycle` events carry `phase`, `kind`, `requestId`,
`startedAt`, and `phaseStartedAt`. Phases: capture, extraction, confirmation,
analysis, success, error, cancelled. Confirmation has no running backend job.
Account 3's `createLoading` is called only for capture/extraction/analysis and
stops at confirmation and every terminal state. There are no inferred backend
substage completions or percentages. Saved context maps `page_context.capture_kind` back to the panel’s `capture.kind`;
this keeps reopened video replay and explicit YouTube text intent distinct.
History/reassessment opens must cancel the active flow first; follow the existing integration hooks in EVIDENCE_HANDOFF.

The worker accepts trusted-panel CAPTURE, SHOW_CAPTURE and CLEAR_CAPTURE only.
Tab/document IDs, original selection locators and 64-character prefix/suffix
remain local; they never enter the API. A locator has at most 4,000 exact UTF-16
characters and scans at most 1M eligible text characters/50k nodes. Main-frame
HTML only. At most 32 original DOM ranges stay in the originating document; a
deleted original cannot redirect the action to a surviving duplicate. A new document cannot reuse the document token/Chrome document ID.
Highlights use CSS Highlight ranges, underlining and a removable labeled page
notice. Any page mutation/navigation clears an active range conservatively.
Excluded/private editor material cannot become an eligible locator. Existing
broad host permissions were inherited; this branch requests no extra access.

## Checks and reproduction

- Backend + source tracing: **514 passed, 3 skipped** (database-only opt-in tests).
- Extension build + **22 unit tests** and **46 browser tests** passed together;
  an additional restricted-manifest permission-denial browser check passed.
- Real FastAPI mock/backend-off smoke and controlled 46-second panel smoke passed.
- Live API/model, Neon persistence and video cache receipts, original real result
  backups and separate live YouTube player capture: see
  [verification/capture-flow/README.md](verification/capture-flow/README.md).
- Standard warnings: Starlette/httpx test-client deprecation and pytest-asyncio
  default fixture loop scope. Neither is presented as an upstream-service failure.

```sh
/tmp/truthtrace-person1-venv/bin/python -m pytest backend/tests source_tracing/tests -q
(cd extension && npm run check)
TRUTHTRACE_TEST_PYTHON=/tmp/truthtrace-person1-venv/bin/python node extension/scripts/smoke-backend.mjs
node extension/scripts/smoke-latency.mjs
node extension/scripts/smoke-video.mjs
PYTHONPATH=backend /tmp/truthtrace-person1-venv/bin/python backend/scripts/smoke_capture_flow.py --env-root /path/to/configured/checkout
```

The live smoke is opt-in and spends bounded upstream credits. It loads existing
ignored environment files without copying or printing credentials. It writes
real backups under `/tmp` unless another output directory is requested. Its
initial API proposals are programmatically approved for integration testing;
ordinary human review/toolbar operation remains a separate acceptance check.
