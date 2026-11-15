# Hackathon integration

Account 3 owns presentation issues #10, #11, #12, #15, #17, #26, #27 and final integration on `codex/hackathon-integration`. The user's main checkout is preserved; work happens in `../TruthTrace-integration`.

## Shared baseline

Start Accounts 1 and 2 from the published `codex/hackathon-integration` baseline (the commit introducing this document), containing current main `45d94dc` and detective `56efc06`. The latter already reconciles Gemini, source-provider, and YouTube changes. Safe `.env.example` setup templates are restored. Detective chat uses the existing hedged transport once rather than retrying the whole pair.

Baseline verification: 222 backend tests; 244 source-tracing tests passed, 3 skipped (database-dependent); extension build, 9 unit tests and 23 Playwright tests passed. Browser requests use fixtures; these checks do not establish live Gemini/Bright Data/Neon behavior.

## Ownership and contracts

- Account 1: capture, video context, exact retry/confirmation, panel request lifecycle. Keep `renderResult(result, capture, {fresh})` as the completed-result renderer. Capture must retain original text, URL/title, mode and available video context. Account 3 owns the renderer's internals, source list, graph/detail wiring, report and presentation modules. Avoid rewriting these sections on parallel branches.
- Loading (#10): Account 3 supplies `createLoading({title, text, elapsed})` in `src/loading.js`, exposing `start('capture', {kind})`, `start('analysis', {timeoutMs})`, `stop()`. Call only on observed transitions, and stop at every terminal state. No invented server substages. Account 1 can replace the small lifecycle wiring during sequential integration.
- Account 2: backend/source evidence, provenance, history and reassessment. Preserve source IDs and distinguish fetched passages, search snippets, fact-check summaries and model-detected video context. Account 3 will preserve structured edge evidence as `evidence_detail` alongside the legacy human-readable `evidence` string; UI code accepts legacy strings. Optional source/analysis metadata must survive normalization.
- Finding citations: validated `cited_source_ids`, when supplied, must also occur as citation markers in the displayed explanation and resolve to safe displayed sources. Added/pinned sources do not change the original finding's cited set. Reassessment explicitly renders a separate finding and recalculates membership.
- Selection contract: `{type:'source', id}`, `{type:'edge', index}`, or `null`. Returned IDs and normalized graph indices are authoritative. Full board and panel share detail and connection walkthrough modules.
- Final integration: finished branches merge sequentially here, followed by combined checks. Open an integration PR for review; never merge main automatically. Issues close individually only after acceptance evidence is recorded.

Plan.md protects submission work starting Sunday 11 a.m. and final submission at noon. No more precise commit cutoff is recorded in the repository; commit/push reviewable increments promptly.
