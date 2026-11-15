# Credits

TruthTrace was built for SanD Hacks Fall 2026 by the TruthTrace team.

- [Cytoscape.js](https://js.cytoscape.org/): source-network visualization, MIT licensed. The extension build includes its license beside the local distribution.
- [Chrome Extensions APIs](https://developer.chrome.com/docs/extensions): Manifest V3, active-tab capture, storage, service worker, and side panel.
- [FastAPI](https://fastapi.tiangolo.com/), [Uvicorn](https://www.uvicorn.org/), [Pydantic](https://docs.pydantic.dev/), [HTTPX](https://www.python-httpx.org/), [python-dotenv](https://github.com/theskumar/python-dotenv), and [Psycopg](https://www.psycopg.org/): Python backend and database tooling. Dependencies retain their respective licenses.
- [Gemini API](https://ai.google.dev/): intended claim extraction and evidence analysis.
- [Google Fact Check Tools API](https://developers.google.com/fact-check/tools/api): published fact-check lookup.
- [Bright Data SERP API](https://docs.brightdata.com/scraping-automation/serp-api/introduction): source discovery.
- [Neon](https://neon.com/docs): intended PostgreSQL storage and caching. Live integration status is recorded in the handoffs.
- [Playwright](https://playwright.dev/) and [pytest](https://docs.pytest.org/): isolated browser and Python verification.
- NASA's [DART announcement](https://www.nasa.gov/news-release/nasa-confirms-dart-mission-impact-changed-asteroids-motion-in-space/) and [imagery article](https://www.nasa.gov/solar-system/nasa-dart-imagery-shows-changed-orbit-of-target-asteroid/) supply the public demo source material and source URLs. NASA is not affiliated with or endorsing this project.

The library-budget backend mock and `sample_graph_synthetic.json` are invented examples and must be presented as synthetic. The NASA cache uses public-web reads and must not be presented as Bright Data retrieval or Neon persistence.
