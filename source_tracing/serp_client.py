"""Bright Data SERP API over REST.

Request format (official `search` skill, references/serp.md, "The fast path - REST"):

    POST https://api.brightdata.com/request
    Authorization: Bearer <key>
    {"zone": "<serp zone>", "url": "https://www.google.com/search?q=...&brd_json=1", "format": "raw"}

`brd_json=1` makes the body parsed JSON with an `organic` array.

Plain REST instead of the `brightdata-sdk` package on purpose: the SDK by
default creates zones in the account when it opens, which is an account
change we do not want as a side effect.

Every HTTP attempt, retries included, is charged against the SerpBudget
before it is sent, so a retry can never exceed the per-investigation cap.
"""

from __future__ import annotations

import json
from urllib.parse import urlencode

import httpx

from .config import MAX_SERP_REQUESTS, BrightDataSettings

API_URL = "https://api.brightdata.com/request"
MAX_RESPONSE_BYTES = 5 * 1024 * 1024


class SerpError(RuntimeError):
    pass


class SerpAuthError(SerpError):
    pass


class SerpZoneError(SerpError):
    pass


class SerpTimeoutError(SerpError):
    pass


class SerpResponseError(SerpError):
    pass


class BudgetExhaustedError(SerpError):
    pass


class SerpBudget:
    """Counts SERP HTTP attempts for one investigation."""

    def __init__(self, max_requests: int = MAX_SERP_REQUESTS):
        self.max_requests = min(max_requests, MAX_SERP_REQUESTS)
        self.used = 0

    @property
    def remaining(self) -> int:
        return self.max_requests - self.used

    def charge(self) -> None:
        if self.used >= self.max_requests:
            raise BudgetExhaustedError(f"SERP budget of {self.max_requests} requests exhausted")
        self.used += 1


def google_search_url(query: str, *, country: str, language: str) -> str:
    params = {"q": query, "hl": language, "gl": country, "num": "10", "brd_json": "1"}
    return "https://www.google.com/search?" + urlencode(params)


class BrightDataSerpClient:
    def __init__(self, settings: BrightDataSettings, *, transport: httpx.AsyncBaseTransport | None = None):
        self._settings = settings
        self._transport = transport

    async def search(self, query: str, budget: SerpBudget) -> dict:
        s = self._settings
        body = {
            "zone": s.serp_zone,
            "url": google_search_url(query, country=s.country, language=s.language),
            "format": "raw",
        }
        headers = {"Authorization": f"Bearer {s.api_key}", "Content-Type": "application/json"}
        last_error: SerpError | None = None
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(s.timeout_s), transport=self._transport, follow_redirects=False
        ) as http:
            for _attempt in range(1 + max(0, s.max_retries)):
                if last_error is not None and budget.remaining <= 0:
                    raise last_error  # don't hide the real failure behind "budget"
                budget.charge()
                try:
                    resp = await http.post(API_URL, json=body, headers=headers)
                except httpx.TimeoutException:
                    last_error = SerpTimeoutError(f"SERP request timed out after {s.timeout_s:.0f}s")
                    continue
                except httpx.TransportError as exc:
                    last_error = SerpError(f"SERP connection failed: {type(exc).__name__}")
                    continue
                if resp.status_code in (429,) or resp.status_code >= 500:
                    last_error = SerpError(f"SERP provider error HTTP {resp.status_code}")
                    continue
                try:
                    return self._decode(resp)
                except SerpResponseError as exc:
                    # A 200 with an unreadable body was seen live as a one-off; retry
                    # it within budget. Auth and zone errors are still not retried.
                    last_error = exc
                    continue
        assert last_error is not None
        raise last_error

    @staticmethod
    def _decode(resp: httpx.Response) -> dict:
        if len(resp.content) > MAX_RESPONSE_BYTES:
            raise SerpResponseError("SERP response exceeded size limit")
        if resp.status_code in (401, 403, 407):
            raise SerpAuthError(f"Bright Data rejected the credentials (HTTP {resp.status_code})")
        if resp.status_code >= 400:
            if "zone" in resp.text[:200].lower():
                raise SerpZoneError(f"Bright Data zone problem (HTTP {resp.status_code})")
            raise SerpError(f"SERP request failed (HTTP {resp.status_code})")
        try:
            payload = json.loads(resp.content)
        except ValueError:
            raise SerpResponseError(
                "SERP response was not JSON; is brd_json supported by this zone?"
            ) from None
        # Some zones wrap the page as {"status_code":..., "body": "<json string>"}.
        # The outer HTTP status may be 200 even when the upstream request failed.
        if isinstance(payload, dict) and "status_code" in payload:
            status = payload["status_code"]
            if not isinstance(status, int):
                raise SerpResponseError("SERP wrapper had invalid status_code")
            if status in (401, 403, 407):
                raise SerpAuthError(f"Bright Data upstream rejected the request (HTTP {status})")
            if status >= 400:
                raise SerpError(f"SERP upstream request failed (HTTP {status})")
        if isinstance(payload, dict) and "organic" not in payload and isinstance(payload.get("body"), str):
            try:
                payload = json.loads(payload["body"])
            except ValueError:
                raise SerpResponseError("SERP body was not JSON") from None
        if not isinstance(payload, dict):
            raise SerpResponseError("SERP response was not a JSON object")
        return payload
