"""Live check against real Gemini + Fact Check APIs (needs keys in backend/.env).

Usage (from backend/):  python -m scripts.smoke_live
Runs each claim twice, checks status consistency and that every citation in the
explanation matches a returned source. Uses a few free-tier requests per run.
"""

import asyncio
import re
import sys

import httpx

from app.analysis import run_investigation
from app.config import get_settings
from app.models import AnalysisInput

CLAIMS = [
    "The Great Wall of China is visible from space with the naked eye.",
    "Water boils at 100 degrees Celsius at sea level.",
    "Barack Obama was born in Kenya.",
]
CITATION_MARKER = re.compile(r"\[([A-Za-z]{1,3}\d{1,4}(?:,\s*[A-Za-z]{1,3}\d{1,4})*)\]")


# Returns citation ids in the explanation that do not match a returned source.
def find_invented_citations(explanation: str, source_ids: set[str]) -> list[str]:
    """Return every cited id that is not among the returned source ids."""
    cited_ids = [
        token.strip()
        for group in CITATION_MARKER.findall(explanation)
        for token in group.split(",")
    ]
    return [cited_id for cited_id in cited_ids if cited_id not in source_ids]


# Runs every claim twice against the live APIs and reports problems.
async def main() -> int:
    """Return 0 if all live checks pass, 1 otherwise."""
    settings = get_settings()
    if not settings.gemini_api_key or not settings.factcheck_api_key:
        print("Set GEMINI_API_KEY and FACTCHECK_API_KEY in backend/.env first.")
        return 1
    failures = 0
    async with httpx.AsyncClient() as http_client:
        for claim in CLAIMS:
            statuses = []
            for _ in range(2):
                result = await run_investigation(
                    http_client,
                    settings,
                    AnalysisInput(
                        text=claim, url="https://example.com/", mode="manual"
                    ),
                )
                invented = find_invented_citations(
                    result.explanation, {source.id for source in result.sources}
                )
                statuses.append(result.status)
                print(f"- {result.claim}\n  status={result.status} "
                      f"sources={len(result.sources)} invented={invented}\n"
                      f"  {result.explanation}")
                failures += bool(invented)
            if len(set(statuses)) != 1:
                print(f"  INCONSISTENT statuses: {statuses}")
                failures += 1
    print("PASS" if failures == 0 else f"FAIL ({failures} problems)")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
