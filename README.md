# TruthTrace

Follow a claim back to its evidence. TruthTrace is a Chrome side-panel extension that submits selected text or a news article to a local FastAPI backend, checks retrieved evidence, and displays source links and their observed relationships.

Built for SanD Hacks Fall 2026. Scope and team ownership: [Plan.md](Plan.md).

## Current state

The backend is wired to `source_tracing/` for source retrieval and caching, optional Neon persistence, and YouTube claim extraction. The detective branch adds follow-up questions, explicit evidence searches, and an interactive source board. The integration branch adds consistent evidence selection, connection walkthroughs, finding citation filters, shared-reference summaries, and copyable evidence reports. Automated checks use fixture upstream responses; they do not establish live vendor availability. Live article and video API checks, Neon persistence/cache receipts, and a separate live YouTube player capture are recorded in [capture-flow verification](docs/verification/capture-flow/README.md). These checks do not establish universal video support or validate model transcription. See [integration verification](docs/INTEGRATION.md) and [Person 1's handoff](extension/PERSON1_HANDOFF.md) for what was actually verified.

## Run the local backend

Use Python 3.11+ and Node 20+.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt -r source_tracing/requirements.txt
cp backend/.env.example backend/.env
```

Set `TRUTHTRACE_API_KEY` in `backend/.env` to a long random local shared key. For a credential-free walking skeleton, set `USE_MOCK=true`; the panel labels this synthetic data. Then run:

```bash
cd backend
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

For real upstream services, required variable names are `GEMINI_API_KEY`, `FACTCHECK_API_KEY`, `BRIGHTDATA_API_KEY`, `BRIGHTDATA_SERP_ZONE`, and `DATABASE_URL`. The last is for Neon persistence. Configure these according to the [backend README](backend/README.md) and [source-tracing README](source_tracing/README.md). Real services must be verified separately from the synthetic mock flow. Never commit real keys or database URLs.

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

The backend URL defaults to `http://127.0.0.1:8000` in `extension/config.json`. Local settings and an ignored `config.local.json` can override it. Only localhost backends are supported for the laptop demo. Details, permissions, and manual smoke checklist: [extension README](extension/README.md).

## Demo articles

- Primary: [NASA confirms DART changed an asteroid's motion](https://www.nasa.gov/news-release/nasa-confirms-dart-mission-impact-changed-asteroids-motion-in-space/).
- Backup: [NASA DART imagery shows the changed orbit](https://www.nasa.gov/solar-system/nasa-dart-imagery-shows-changed-orbit-of-target-asteroid/).
- Suggested selected claim: `NASA's DART impact altered Dimorphos's orbit around Didymos`.

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

Backend tests currently need `pytest-asyncio` in addition to their requirements file:

```bash
# From the repository root, with the venv active:
pip install pytest-asyncio
(cd backend && python -m pytest -q)
python -m pytest source_tracing -q
```

The Neon integration test skips without a separate `TEST_DATABASE_URL`. These offline checks do not prove the live Gemini → Bright Data → Neon flow.

## Credits and license

See [CREDITS.md](CREDITS.md) for libraries, services, and example-source attribution. Project code is licensed under [MIT](LICENSE). Third-party dependencies retain their own licenses.
