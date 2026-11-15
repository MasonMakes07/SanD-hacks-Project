# Capture-flow verification, November 14, 2026 (Pacific)

These are real API result backups and receipts, **not synthetic test fixtures**.
The extension does not silently replay these files. They record the check time
and scope below, not current vendor availability or independently validated
video transcription. No keys, database URLs, or confirmation tokens are retained.

## Live paths

The first receipt/result pair in this directory predates Account 2's final
material validation integration. Article extraction/approved analysis took
1.05s/11.98s (4 sources, 2 connections, supported); video took 21.06s/21.11s
(3 sources, 1 connection, supported). Both results were read back from Neon.

The `combined/` files were recorded after reconciling Account 2's integrated
work at `35c3eb8`. Article extraction/approved analysis took 4.00s/16.72s and
returned 5 sources/5 connections. Video took 12.53s/17.77s and returned 4
sources/3 connections. **Both latest findings were insufficient evidence**;
these successful pipeline checks do not assert a supported finding. Approved
claims matched the displayed result exactly. Gemini and Fact Check returned
real successful responses; one final-model 503 was followed by a successful
fallback. Real Neon source-cache records and complete analysis rows were read
back separately. The article seed uses the newer page-specific cache identity.

The combined run exposed a missing `extracted_at` column. Video cache lookup
and write failed safely while the real clip extraction/analysis still completed.
The existing additive migration runner was then applied over the configured
direct Neon URL after verifying it targets the runtime database. It reported
001–005 applied idempotently. `cached_video_receipt.json` subsequently verifies
a real Neon clip hit in 0.489s followed by a memory hit in 0.001s, **zero model
requests**. The legacy extraction time correctly remains unknown; insertion
time is not promoted to extraction time.

Separate live YouTube content capture used the actual public player at
`https://www.youtube.com/watch?v=N-q2z4N_Rsk`, paused/seeking to 141s. It captured
141s and duration 204.361s, with the real page title. This checks live media/DOM
capture; it does not validate the model quotation or a human toolbar grant.

## Controlled checks

- Actual unpacked panel accepted a synthetic response after 46.3 seconds using
  the shipped 100,000ms client deadline. This is latency evidence, not live API
  evidence. Reproduce: `node extension/scripts/smoke-latency.mjs`.
- Actual authenticated FastAPI mock + unpacked extension completed extraction,
  reader confirmation and rendering. Stopping that backend preserved the result
  while showing an actionable connection error. Reproduce with
  `TRUTHTRACE_TEST_PYTHON=/path/to/venv/bin/python node extension/scripts/smoke-backend.mjs`.
- Native synthetic WebM playback exercises the actual isolated content script,
  worker message transport, video intent, immutable approval and retry after
  seeking/navigation/tab changes. `video-moment.webm` is an original black 16×16,
  205-second VP8/WebM timeline generated locally with the browser WebP encoder;
  no third-party media. Reproduce with `generate-video-fixture.mjs`.
- Exact inline selections, duplicate contexts, DOM edits, replaced documents,
  closed tabs, same-document navigation, cleanup and real host-access denial
  are exercised in Playwright. Permission denial uses a restricted test-only
  manifest; the inherited shipped manifest already declares broad host access.

Chrome DevTools MCP was attempted repeatedly in this account but could not
connect to its configured Chrome debugging profile. Actual interactions here
used isolated Playwright Chromium extension profiles. Account 3's separate MCP
review and final combined acceptance are tracked in `docs/INTEGRATION.md`.
