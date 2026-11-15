# TruthTrace Backend (Person 2: AI, claim analysis, API)

FastAPI service behind the Chrome extension. Flow for `POST /api/analyze`:

1. **Middleware** (`app/middleware.py`): API key, body size, rate limit, headers
2. **Translator** (`app/Translator.py`): validates and sanitizes the request
3. **Claim extraction** (`app/gemini_client.py`): Gemini picks one checkable claim
4. **Evidence**, in parallel: Google Fact Check Tools (`app/factcheck_client.py`)
   and the Bright Data source provider (`app/source_provider.py`, Person 3)
5. **Analysis** (`app/gemini_client.py`): status and explanation from sources only
6. **Validation** (`app/validation.py`): drops invented citations and model URLs,
   downgrades uncited verdicts to `insufficient_evidence`, removes bad edges
7. **Translator** builds the contract response (see `API_CONTRACT.md`)

## Setup
```bash
cd backend
pip install -r requirements.txt
cp .env.example .env      # then fill in the keys
```

Required keys (names only): `GEMINI_API_KEY` (Google AI Studio),
`FACTCHECK_API_KEY` (Google Cloud, Fact Check Tools API enabled),
`TRUTHTRACE_API_KEY` (any long random string, shared with the extension).

## Run
```bash
cd backend
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```
Set `USE_MOCK=true` in `.env` to always return the mock response.

## Test
```bash
cd backend
python -m pytest -q                 # offline, all upstream APIs faked
python -m scripts.smoke_live        # live: demo claims x2, checks citations
```

### Request budgets

The server caps `ANALYZE_TIMEOUT_SECONDS` at 90 seconds (default 90),
`REQUEST_TIMEOUT_SECONDS` at 12 seconds, and `VIDEO_TIMEOUT_SECONDS` at 30
seconds. Lower finite positive overrides are supported. A Gemini stage includes
both primary and fallback attempts: the hedge shares its deadline and receives
only the remaining time. The endpoint's monotonic deadline caps model calls and
cancels awaited work on expiry. Source tracing has a 40-second ceiling including
its cache lookup/write (each bounded to 2 seconds); Bright Data attempts are
capped at 12 seconds and source page fetches at 6 seconds. Full-analysis
persistence has a separate two-second bounded wait for saved-result metadata;
video-cache writes run in the background.
A database thread already in progress may outlive a cancelled await.

The shipped extension waits 100 seconds, leaving a 10-second transport margin.
Loading an old `config.local.json` or Chrome profile with a shorter deadline
raises it to 100 seconds. `apiRequest` supports a separate explicit short timeout
for health probes and controlled tests. Client cancellation stops waiting and
ignores late responses; it does not assert that all server work has stopped.
