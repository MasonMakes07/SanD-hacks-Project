# TruthTrace extension — Person 1

Manifest V3 side panel with selected-text and visible-article capture, authenticated local backend requests, safe source links, cited explanations, and a custom evidence board with paper source cards, interactive yarn, and a full investigation view. Plain ES modules and CSS keep the unpacked extension easy to inspect; there is no frontend framework or remote script dependency.

## Build and load

```bash
cd extension
npm ci
npm run build
```

`build` verifies the extension entry points and local board assets. Load **extension/** in `chrome://extensions` → Developer mode → Load unpacked. Chrome 116+ is required. The renderer uses native DOM and Canvas; no graph library or generated vendor bundle is needed.

Open a normal HTTP(S) article and click the TruthTrace toolbar icon to open the side panel and grant temporary page access. Click the icon again when changing to a different site or tab. Highlight text and choose **Check selection**, or choose **Analyze page**. The latter reads an `article`, `main`, or semantic equivalent and checks one claim. It does not scrape arbitrary `body` content. Chrome settings, PDFs, the Web Store, frames, and pages without readable semantic article containers are unsupported; selecting a visible sentence often works when article extraction does not.

## Connection configuration

Start the backend using [its setup instructions](../backend/README.md). The extension sends `X-TruthTrace-Key` on `POST /api/analyze`; use the same value as `TRUTHTRACE_API_KEY` in `backend/.env`. Never put Gemini, Bright Data, Google, or Neon credentials in the extension.

Configuration is merged in this order (later overrides earlier):

1. Tracked `config.json`: default URL, blank key, 45-second timeout.
2. Optional ignored `config.local.json`: copy `config.local.example.json`, fill in locally, then reopen the panel.
3. Connection settings saved through the panel's gear menu in this Chrome profile's local storage.

Supported backend URLs are `http://127.0.0.1:<port>` and `http://localhost:<port>`. The demo runs on the laptop; remote backends are outside this build. If saved profile settings override a local file, use the gear menu to update them. Local storage is restricted to trusted extension contexts. The shared backend key is not an upstream vendor secret and is not secure against someone who can inspect the local Chrome profile.

The user initiates both actions, so both use `mode: "manual"`; there is no scrolling check or automatic network request on page navigation. No investigation history is stored. Reconnect runs a public `/api/health` check. The panel waits up to the configured timeout for analysis, with cancel and retry controls. Cancel stops the client request; upstream server work may finish separately.

## Permissions

| Permission | Purpose |
|---|---|
| `activeTab` | Temporary access to the article after invoking the toolbar action |
| `sidePanel` | Display results beside the article |
| `storage` | Local connection settings |
| `scripting` | Inject the one-shot text reader into the authorized active tab |
| `http://127.0.0.1/*`, `http://localhost/*` | Connect to the local backend on configurable ports |

`scripting` is the additional permission required for programmatic capture with `activeTab`; see [Chrome's scripting API](https://developer.chrome.com/docs/extensions/reference/api/scripting). No blanket news-site host permission, background scraping, `tabs`, or context-menu permission is requested. The localhost host grants also permit reading localhost pages, used by the isolated browser tests.

The service worker opens the panel directly during the [toolbar user gesture](https://developer.chrome.com/docs/extensions/reference/api/sidePanel) and handles only trusted-panel capture messages. The panel owns long-running fetches so the MV3 worker's lifecycle cannot interrupt analysis. Only text from the chosen capture and its page URL are sent to the local backend, which in turn may use external analysis services.

## Evidence display

- All API strings are rendered as text. Source and citation links allow credential-free HTTP(S) URLs only and open with `noopener noreferrer`.
- Citation markers such as `[S1]` link only to matching returned sources.
- The graph uses a fixed layout. Nodes jump to source cards; edges expose observed evidence. A keyboard-accessible connection list repeats every displayed edge and its evidence.
- Only known relationship types with nonempty evidence and existing source endpoints appear. Both Person 2 string evidence and Person 3 structured observation evidence are accepted.
- Similar wording is labeled as overlap and never as proof of copying. Citation arrows show source → cited document; other relationships are undirected.
- Missing dates never imply an original publisher. With no usable sources, the result displays `insufficient_evidence`.
- `USE_MOCK=true` and `mock-`/`synthetic-` investigation IDs display a synthetic-data warning. Real analysis always comes from the backend; there is no hidden fallback verdict.

## Automated checks

```bash
npx playwright install chromium
npm run check
```

Nine Node checks cover transport, configuration, URL safety, empty evidence, and graph validation. Nine Playwright checks load the real unpacked extension in fresh temporary Chromium profiles, exercise the actual service worker and content script against local articles, and inspect UI results. Only upstream analysis is mocked. [Playwright's extension guide](https://playwright.dev/docs/chrome-extensions) explains why this uses bundled Chromium rather than a user's ordinary Chrome profile.

```bash
# Needs backend dependencies installed in this Python environment:
TRUTHTRACE_TEST_PYTHON=../.venv/bin/python npm run smoke:backend

# Optional: public website requests; no vendor API use:
npm run smoke:demo-site
```

The backend smoke starts a separate FastAPI process on a free port with a random temporary local key, clears external-service keys, runs the real mock endpoint, then stops the backend and verifies the offline UI. It does not change your `.env` or reuse your running demo server. The site smoke checks actual article and selection extraction on both NASA demo pages; it does not test a human toolbar grant on those origins.

## Remaining manual demo-laptop check

- Load unpacked in a fresh ordinary Chrome profile and confirm there are no extension load errors.
- Open the [primary NASA article](https://www.nasa.gov/news-release/nasa-confirms-dart-mission-impact-changed-asteroids-motion-in-space/), click the toolbar icon, select one factual sentence, and check it.
- Verify Analyze page and that a source link opens the actual returned URL.
- Switch tabs/sites with the panel open; confirm access guidance appears until the toolbar is invoked on the new article.
- Stop the backend and check the error; restart it and retry.
- Repeat on the [backup NASA article](https://www.nasa.gov/solar-system/nasa-dart-imagery-shows-changed-orbit-of-target-asteroid/).
- For the real demo, turn off `USE_MOCK`, complete Person 2/3 integration, then verify real sources, evidence-backed edges, and caching before recording the submission video.

Progress and exact continuation tasks are in [PERSON1_HANDOFF.md](PERSON1_HANDOFF.md).

## Investigation board

Choose **Open investigation board** after checking a claim. Drag the background to pan, scroll to zoom, drag cards to arrange them, and use **Fit to view** to reframe. Select a card for its source and connections, or select an exposed yarn segment for relationship evidence. Enter/Space selects a focused card; Escape clears focus. The case file provides buttons for following each relationship without using the canvas.

`src/board.js` renders yarn beneath the paper and pins above it using a shared pan/zoom transform. Motion runs only while disturbed and respects reduced-motion preferences. `src/detail.js` explains only the normalized backend relationships. The latest investigation is handed to `board.html` through extension session storage, which clears when Chrome closes. No backend request is made by the board page.

## Ask the Detective

The side panel's **Ask the detective** card sends follow-up questions to `POST /api/chat` with the claim, verdict, explanation, sources and relationships of the current investigation. Answers cite sources by id; each citation opens the retrieved source. **Search for more evidence** and **Challenge the verdict** each run one `POST /api/evidence/search`, only when clicked. New sources can be pinned to the board individually; strings are drawn only for relationships the backend returns. Pinning never changes the verdict. Tests: `tests/chat.spec.js`.
