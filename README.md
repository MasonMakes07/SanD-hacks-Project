# TruthTrace

Follow a claim back to its evidence. TruthTrace is a Chrome side-panel extension that checks a claim from a news article, a social media post or a YouTube video against published fact-checks and live web sources, then shows the evidence, a cited assessment, and how the sources connect.

## What it does

- **Check a claim** from selected text, a whole article, or a post on X, Reddit, Bluesky and other social sites (the post being viewed, with its permalink).
- **Check a YouTube moment**: Gemini watches a short clip around the playback position and returns the claim with its timestamp, quote and a link to that moment.
- **Evidence, not a bare verdict**: Google Fact Check Tools results plus Bright Data web sources. Every citation must match a retrieved source, and a claim is never called false just because nothing was found (`insufficient_evidence`).
- **Investigation board**: sources as paper cards connected by yarn, drawn only for relationships found in retrieved pages.
- **Ask the Detective / Challenge the Verdict**: cited follow-up questions, and on-demand searches for more or contradicting evidence that can be pinned to the board.
- **Neon storage and caching**: investigations, sources, edges, verdicts and YouTube clip claims are stored; repeat checks are served from the cache.

## Current state

The backend is wired to `source_tracing/` for source retrieval and caching, optional Neon persistence, and YouTube claim extraction. The detective branch adds follow-up questions, explicit evidence searches, and an interactive source board. The integration branch adds consistent evidence selection, connection walkthroughs, finding citation filters, shared-reference summaries, and copyable evidence reports. Automated checks use fixture upstream responses; they do not establish live vendor availability. Live article and video API checks, Neon persistence/cache receipts, and a separate live YouTube player capture are recorded in [capture-flow verification](docs/verification/capture-flow/README.md). These checks do not establish universal video support or validate model transcription. See [integration verification](docs/INTEGRATION.md) and [Person 1's handoff](extension/PERSON1_HANDOFF.md) for what was actually verified.

## How it fits together

```
Chrome extension (extension/)
   │  X-TruthTrace-Key, localhost only
   ▼
FastAPI backend (backend/)
   ├─ Gemini ─────────────── claim extraction (text, posts, YouTube clips) and cited assessment
   ├─ Google Fact Check ──── published fact-checks
   └─ source_tracing/ ────── Bright Data SERP search, page fetches, source graph
                                 └─ Neon Postgres: cache + stored investigations
```

| Endpoint | Purpose |
| --- | --- |
| `GET /api/health` | Public liveness check (shows whether mock mode is on) |
| `POST /api/extract` | Proposes the claim to check, for the user to review or edit |
| `POST /api/analyze` | Runs the investigation on the approved claim |
| `POST /api/chat` | Ask the Detective follow-up questions |
| `POST /api/evidence/search` | Search for more evidence / Challenge the verdict |

Request and response fields: [backend/API_CONTRACT.md](backend/API_CONTRACT.md).

## Run the local backend

Use Python 3.11+ and Node 20+.

```bash
python3 -m venv .venv
source .venv/bin/activate              # Windows: .venv\Scripts\activate
pip install -r backend/requirements.txt -r source_tracing/requirements.txt pytest-asyncio
cp backend/.env.example backend/.env
cp source_tracing/.env.example source_tracing/.env
```

Set `TRUTHTRACE_API_KEY` in `backend/.env` to a long random local shared key (for example `python -c "import secrets; print(secrets.token_urlsafe(32))"`). For a credential-free walking skeleton, set `USE_MOCK=true`; the panel labels this synthetic data. Then run:

```bash
cd backend
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

### Real services (names only; never commit values)

| File | Variables | Used for |
| --- | --- | --- |
| `backend/.env` | `GEMINI_API_KEY`, `FACTCHECK_API_KEY`, `TRUTHTRACE_API_KEY` | Gemini, Google Fact Check Tools, extension auth |
| `source_tracing/.env` | `BRIGHTDATA_API_KEY`, `BRIGHTDATA_SERP_ZONE` | Bright Data SERP search |
| `.env.local` (repo root) | `DATABASE_URL`, `DATABASE_URL_UNPOOLED` | Neon (pooled for the app, direct for migrations) |

`GEMINI_MODEL` defaults to `gemini-3.5-flash-lite`, much faster than the older `gemini-3.1-flash-lite`. A slow or overloaded Gemini call is raced against `GEMINI_FALLBACK_MODEL` after `GEMINI_HEDGE_SECONDS`. All settings: [backend/.env.example](backend/.env.example) and the [backend README](backend/README.md).

### Neon (optional persistence and cache)

```bash
npm i -g neon@latest
neon login
neon env pull                          # writes DATABASE_URL and DATABASE_URL_UNPOOLED to .env.local
python -m source_tracing.migrate       # from the repo root: additive tt_ tables, safe to re-run
```

The backend loads `.env.local` automatically. Without `DATABASE_URL`, checks still work but are not stored or cached. Details: [source-tracing README](source_tracing/README.md).

Real services must be verified separately from the synthetic mock flow. Never commit real keys or database URLs; `.env`, `.env.*` and `.env.local` are git-ignored.

## Load the extension

```bash
cd extension
npm ci
npm run build
```

1. Open `chrome://extensions` in Chrome 116+ and enable **Developer mode**.
2. Click **Load unpacked** and choose this repository's **extension/** directory.
3. Pin TruthTrace from the Extensions menu. Open an article and click the TruthTrace toolbar icon.
4. Open **Connection settings** (gear), enter the local backend URL and the same `TRUTHTRACE_API_KEY`, and save.
5. Highlight one factual sentence and click **Check selection**, choose **Analyze page**, or capture a public YouTube moment with **Check around video moment**. Review/edit the proposal, then choose **Check this claim** to retrieve evidence.
6. After a result, choose **Open investigation board**, or use **Ask the detective**, **Search for more evidence** and **Challenge the verdict**.

The backend URL defaults to `http://127.0.0.1:8000` in `extension/config.json`. Local settings and an ignored `config.local.json` can override it. Only localhost backends are supported for the laptop demo. Details, permissions, and manual smoke checklist: [extension README](extension/README.md).

## Demo content

- Primary article: [NASA confirms DART changed an asteroid's motion](https://www.nasa.gov/news-release/nasa-confirms-dart-mission-impact-changed-asteroids-motion-in-space/).
- Backup article: [NASA DART imagery shows the changed orbit](https://www.nasa.gov/solar-system/nasa-dart-imagery-shows-changed-orbit-of-target-asteroid/).
- Suggested selected claim: `NASA's DART impact altered Dimorphos's orbit around Didymos`.
- Video: [DART impact successfully moved asteroid Dimorphos! NASA chief explains](https://www.youtube.com/watch?v=N-q2z4N_Rsk), paused near 2:21 (the "32 minutes" claim).

Run each demo claim once before presenting so it is served from the Neon cache (about 1 to 3 seconds instead of 10 to 25). Neon's compute sleeps after about five idle minutes, so re-run them shortly before judging. Each new claim spends up to 2 Bright Data SERP requests; cached claims spend none.

Person 3's [cached NASA investigation](source_tracing/fixtures/example_investigation_response.json) is a separate public-web source-network backup. It does not demonstrate live Bright Data retrieval, Neon persistence, or a completed model verdict. It is not silently substituted into extension results.

## Verify

```bash
# Extension checks: real unpacked extension in temporary Chromium profiles;
# upstream analysis responses are fixtures. No API credits are used.
cd extension
npm ci
npx playwright install chromium
npm run check

# Optional: real local FastAPI mock route and backend-off error.
# Use the Python executable from the venv created above.
TRUTHTRACE_TEST_PYTHON=../.venv/bin/python npm run smoke:backend

# Optional: public HTTP page reads on both NASA articles. No vendor API calls.
npm run smoke:demo-site
```

```bash
# From the repository root, with the venv active:
(cd backend && python -m pytest -q)
python -m pytest source_tracing -q
```

Offline tests never call real services: the backend suite runs with test keys and stubs Bright Data and Neon, and the `source_tracing` suite hides its `.env` file and credential variables. The Neon integration tests skip without a separate `TEST_DATABASE_URL` (use a development database; they create and drop their own schema). On Windows, some `source_tracing` tests fail as written because their no-network guard also blocks a socket asyncio opens internally; this is a test-harness issue, not a code failure. These offline checks do not prove the live Gemini → Bright Data → Neon flow.

## Credits and license

See [CREDITS.md](CREDITS.md) for libraries, services, and example-source attribution. Project code is licensed under [MIT](LICENSE). Third-party dependencies retain their own licenses.
