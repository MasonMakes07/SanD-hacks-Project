"""Build a labeled local backup from two public NASA pages, with no paid APIs.

Run once with network access: python -m source_tracing.demo_cache
The pages are manually selected from an observed in-article hyperlink. This
does not verify Bright Data or Neon and must never be represented as doing so.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .graph import build_graph
from .models import Source, TraceResult
from .pages import PageFetcher
from .store import JsonFileStore, StoredInvestigation, cache_key

CLAIM = "NASA's DART impact altered Dimorphos's orbit around Didymos"
SOURCE_URLS = (
    "https://www.nasa.gov/news-release/nasa-confirms-dart-mission-impact-changed-asteroids-motion-in-space/",
    # This is the observed in-article link; NASA redirects it to the current page.
    "https://www.nasa.gov/feature/nasa-dart-imagery-shows-changed-orbit-of-target-asteroid",
)
OUT_DIR = Path(__file__).parent / "live_samples" / "nasa_dart_cache"
EXAMPLE_RESPONSE = Path(__file__).parent / "fixtures" / "example_investigation_response.json"


def _excerpt(blocks: list[str]) -> str:
    for block in blocks:
        if "kinetic impact" in block.lower() and "dimorphos" in block.lower():
            return " ".join(block.split()[:24])
    return ""


async def make_demo_cache(directory: Path = OUT_DIR) -> StoredInvestigation:
    retrieved_at = datetime.now(timezone.utc)
    pages, warnings = await PageFetcher().fetch_pages(list(zip(("s1", "s2"), SOURCE_URLS)))
    if len(pages) != 2:
        raise RuntimeError(f"could not retrieve both public pages: {warnings}")
    sources = [
        Source(id="s1", title="NASA Confirms DART Mission Impact Changed Asteroid’s Motion in Space",
               url=SOURCE_URLS[0], date=None, excerpt=_excerpt(pages[0].parsed.blocks)),
        Source(id="s2", title="NASA DART Imagery Shows Changed Orbit of Target Asteroid",
               url=SOURCE_URLS[1], date=None, excerpt=_excerpt(pages[1].parsed.blocks)),
    ]
    graph = build_graph(sources, pages)
    if not any(e.source_id == "s1" and e.target_id == "s2" and
               e.relationship_type == "explicit_citation" for e in graph.edges):
        raise RuntimeError("expected NASA in-article link was not observed; no demo cache saved")
    result = TraceResult(
        investigation_id=f"WEB-{uuid.uuid4().hex}", sources=graph.sources,
        earliest_observed_source=graph.earliest_observed_source,
        graph_nodes=graph.nodes, graph_edges=graph.edges,
    )
    stored = StoredInvestigation(
        result=result, cache_key=cache_key(CLAIM), claim=CLAIM, retrieved_at=retrieved_at,
        meta={"source_method": "public_web_manual_seed_not_brightdata",
              "page_fetches": 2, "serp_requests": 0, "warnings": [*warnings, *graph.warnings]},
    )
    JsonFileStore(directory).save(stored)
    if directory == OUT_DIR:
        EXAMPLE_RESPONSE.write_text(json.dumps(result.to_contract_dict(), indent=2) + "\n", encoding="utf-8")
    return stored


if __name__ == "__main__":
    saved = asyncio.run(make_demo_cache())
    print(f"Saved {saved.result.investigation_id}: {len(saved.result.sources)} sources, "
          f"{len(saved.result.graph_edges)} evidence-backed edges in {OUT_DIR}")
