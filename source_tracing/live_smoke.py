"""ONE authorized live Bright Data SERP smoke test. Spends real (event) credits.

    python -m source_tracing.live_smoke --yes "claim text"

Makes at most 2 SERP requests (normally 1-2). Never prints the API key.
Saves the genuine result to source_tracing/live_samples/ for later demo caching.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from .config import ConfigError, load_settings
from .retrieval import retrieve_sources
from .serp_client import BrightDataSerpClient, SerpBudget, SerpError

OUT_DIR = Path(__file__).parent / "live_samples"


async def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("claim")
    ap.add_argument("--yes", action="store_true", help="confirm spending SERP credits")
    ap.add_argument("--max-requests", type=int, default=2, choices=(1, 2))
    args = ap.parse_args(argv)
    if not args.yes:
        print("Refusing: pass --yes to confirm a live, credit-spending call.")
        return 2
    try:
        settings = load_settings()
    except ConfigError as exc:
        print(f"BLOCKED: {exc}")
        return 3
    budget = SerpBudget(args.max_requests)
    try:
        result = await retrieve_sources(args.claim, BrightDataSerpClient(settings), budget=budget)
    except SerpError as exc:
        print(f"FAILED after {budget.used} SERP request(s): {exc}")
        return 1
    OUT_DIR.mkdir(exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = OUT_DIR / f"serp_{stamp}.json"
    record = {"claim": args.claim, "zone": settings.serp_zone, **result.model_dump(mode="json")}
    out.write_text(json.dumps(record, indent=2), encoding="utf-8")
    print(f"SERP requests used: {result.serp_requests}  raw results: {result.raw_results}  "
          f"kept: {len(result.sources)}  dropped: {result.dropped}  warnings: {result.warnings}")
    for s in result.sources:
        print(f"  {s.id} {s.date or 'date=null'} host={urlsplit(s.url).hostname}")
    print(f"saved {out}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
