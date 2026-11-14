# Person 1 handoff — Chrome extension and interface

Date: November 14, 2026. Assignment: Person 1 in `../Plan.md`.

**Status: all Person 1 build tasks implemented; automated extension checks and real backend mock integration pass.** A human toolbar/side-panel check in ordinary Chrome is still pending. Live source retrieval and Neon integration are outside this handoff's completed work.

## Start here

```bash
# From the repository root:
cd extension
npm ci
npm run build
```

Load **extension/** unpacked in Chrome 116+ using `chrome://extensions`. Click its toolbar icon on an article, open the gear menu, and enter the local backend URL and the same `TRUTHTRACE_API_KEY` as the backend. Start the backend per `../README.md`; `USE_MOCK=true` gives an explicitly labeled synthetic walking skeleton.

No real keys were created, copied into the extension, printed, or committed. No vendor API credits were spent for Person 1 verification. Local API smoke tests use a randomly generated temporary shared key.

## Files and behavior

| File | Purpose |
|---|---|
| `manifest.json` | MV3, minimum Chrome 116, side panel, module worker, local-only backend host permissions |
| `src/background.js` | Opens panel inside toolbar user gesture; validates trusted-panel messages; captures the active tab in the requesting window |
| `src/content.js` | One-shot, read-only selected/visible article text extraction; skips hidden content, navigation, forms and editors; no body fallback |
| `src/config.js`, `config.json` | Validated local backend config; optional ignored file and profile-settings overrides |
| `src/api.js` | Shared-key auth header, bounded requests, abort, safe error mapping, no redirects |
| `sidepanel.html`, `sidepanel.css`, `src/panel.js` | Responsive side panel, settings, checks, loading/cancel/retry, all statuses, cited explanation, actual source links, date caveats, synthetic-data warning |
| `src/result.js` | Contract validation, safe links, evidence normalization, graph endpoint checks, conservative no-source status |
| `src/graph.js` | Locally bundled Cytoscape, fixed layout, node-to-source focus, evidence-backed edges and inspectable evidence |
| `scripts/build.mjs` | Copies pinned Cytoscape JS and license into ignored `vendor/` before loading |
| `tests/` | Node and actual unpacked-extension browser checks |
| `scripts/smoke-backend.mjs` | Actual FastAPI mock integration followed by actual backend-off check |
| `scripts/smoke-demo-site.mjs` | Public article/selection extraction checks on both NASA demo pages |

Repository root additions: setup README, MIT LICENSE, CREDITS.md. `Plan.md` marks completed Person 1 tasks and shared documentation tasks. Backend/source-tracing implementation files were left unchanged.

## API and integration decisions

- Implements `backend/API_CONTRACT.md`: sends exactly `{text, url, mode}` and `X-TruthTrace-Key` for analysis. Public `/api/health` controls connection and mock indicators.
- Both selection and Analyze page use `mode: "manual"` because both are explicitly initiated. No auto-check or automatic investigation on navigation exists.
- Text is capped at 20,000 characters; truncation is disclosed. The content script returns one extra character only to detect truncation, which the worker removes before submission.
- `scripting` is added to the plan's three named permissions because Chrome requires it for programmatic injection with `activeTab`. No all-sites content-script match or news-site host access is requested. Localhost host permissions are for backend access and also make the local test article injectable.
- After switching sites/tabs, Chrome may require a fresh toolbar click to grant temporary `activeTab` access. The UI explains this instead of requesting blanket access.
- Analysis fetches run in the persistent side panel; a long Gemini request must not depend on the MV3 worker staying alive. Client cancel does not promise cancellation of already-running server work.
- All external strings render as text. Matching citation IDs link to returned source URLs. Unsafe URLs, unknown relationships, empty evidence, and dangling graph edges are omitted.
- Backend evidence is a string; Person 3 evidence may be structured. The UI supports both. An evidence object needs an observed excerpt or valid hyperlink; a note alone is not evidence.
- Similar wording is undirected and explicitly does not prove copying. Citation arrows mean referencing, not agreement. The evidence list is keyboard accessible even if canvas is unavailable.
- The earliest dated source is labeled earliest **observed**, never origin. No usable sources always displays `insufficient_evidence`.
- Local-only config is intentional for the laptop demo; no remote deployment was attempted.
- `config.local.json` and generated `vendor/`, dependencies, and browser artifacts are ignored. **Run the build after a clean clone**. Settings saved in Chrome override the local file.

## Verified evidence

Commands run successfully during this session:

```bash
cd extension
npm run check
# 9 Node checks + 9 Playwright browser checks passed.

TRUTHTRACE_TEST_PYTHON=/tmp/truthtrace-person1-venv/bin/python npm run smoke:backend
# PASS: actual unpacked extension -> authenticated FastAPI mock -> source list,
# two clickable citations, Cytoscape network, synthetic warning.
# PASS: terminate that backend -> actionable offline error.

npm run smoke:demo-site
# PASS: primary NASA article, 5,158 extracted article characters + selection.
# PASS: backup NASA article, 1,863 extracted article characters + selection.
```

The automated browser checks load the actual unpacked extension into fresh, temporary Playwright Chromium profiles. They exercise actual extension storage, runtime messages, service worker, and content-script injection against localhost articles. Upstream analysis is a fixture server in these nine tests, not a fake `chrome` object. They also cover selected request contents/auth, article exclusions, empty selections, inconclusive results, auth/offline/malformed errors, cancel/timeout, unsafe HTML/links, and synthetic labels.

The live site smoke runs the actual content-script expression on public NASA pages; it does **not** verify a human toolbar grant or side-panel focus behavior on a NASA origin. Do not mark the fresh ordinary Chrome manual gate complete based on these tests.

Visual result reviewed at 380px width; no horizontal page overflow. Local generated screenshot: `test-results/result-panel.png` (ignored, fixture analysis, not live model output).

Also ran existing suites without changing their implementations:

- Backend: **110 passed** after installing `pytest-asyncio` (currently omitted from `backend/requirements.txt`). One dependency deprecation warning from FastAPI/Starlette remains.
- Source tracing: **214 passed, 1 skipped**; skipped test requires `TEST_DATABASE_URL`.

Verification environment: Node 22.21.1, Python 3.13.7, temporary Python venv `/tmp/truthtrace-person1-venv`, pinned Cytoscape 3.35.0 and Playwright 1.65.1. No project environment file was modified. The temporary test backend and browser contexts were closed.

## Next agent: remaining gates in order

1. Run the manual checklist in `README.md` on the demo laptop's ordinary fresh Chrome profile: load unpacked, invoke toolbar on NASA, select text, Check selection, Analyze page, source-link opening, tab/site switching, backend off/retry. Focus restoration and the real toolbar gesture are the unverified UI pieces. Update `Plan.md` only after the human check passes.
2. Person 2/3 integration is the main project blocker: `backend/app/source_provider.py` still returns an empty `SourceBundle` and does not save investigations. Wire it to `source_tracing.trace_claim`, adapt source IDs/evidence into backend models, and persist the completed result. The UI already accepts either string or structured evidence. Respect backend output validation and do not invent citations or graph edges.
3. Confirm source-tracing credentials/config loading from the backend process and configure Neon. Its real database test has not run. P3's existing handoff has live Bright Data transport evidence but does not prove a complete live source-network → Neon flow.
4. For a live demo, turn off `USE_MOCK`, verify one NASA investigation plus two other claims end-to-end, warm the actual cache, and save screenshots/recording. The saved public-web NASA source cache is a separate backup; it must not be represented as Bright Data or Neon. The extension does not silently use it.
5. Add the missing `pytest-asyncio` development dependency in Person 2's ownership scope. For now the root README documents the installation workaround.
6. Complete external shared tasks from `Plan.md`: team/Devpost/event checks, actual secret/history scrub before public release, submission/video, and pitch. No messages, account registrations, public uploads, commits, or pushes were performed during the initial implementation session. The user subsequently requested committing and pushing the work on `person1/chrome-extension`.

## Session and limits

Work was kept within Person 1 plus shared setup/documentation. No sub-agents were spawned. No account quota/rate-limit meter is exposed by this session's tools, so no numerical remaining quota is asserted. This handoff preserves runnable commands, evidence, current limitations, and next steps before further work risks losing context. The checkout was clean at the start. The original handoff left changes uncommitted on `main`; the user-authorized publication branch is now `person1/chrome-extension`. Use `git log` and `git status` to inspect its commit and working-tree state. Preserve these files and any subsequent teammate edits.
