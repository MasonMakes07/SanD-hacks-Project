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
