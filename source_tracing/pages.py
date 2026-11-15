"""Fetch and parse source pages. Page content is untrusted DATA.

Nothing here interprets page text as instructions: the parser only extracts
hyperlinks, visible body text, and publisher-declared dates. No model reads it.

Fetching is a direct HTTP GET (no Bright Data credits). Each hop, redirects
included, must pass the public-address check; responses are size- and
time-limited; at most MAX_PAGE_FETCHES pages are attempted per investigation.
DNS is not pinned between validation and connection; this is for a local demo,
not a publicly exposed arbitrary-URL fetch service.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from html.parser import HTMLParser
from typing import Callable
from urllib.parse import urljoin

import httpx

from .config import MAX_PAGE_FETCHES
from .dates import parse_reliable_date
from .urls import UnsafeUrlError, check_public_http_url, normalize_url

MAX_PAGE_BYTES = 3 * 1024 * 1024
MAX_REDIRECTS = 3
FETCH_TIMEOUT_S = 6.0  # one slow site must not hold up the whole trace
USER_AGENT = "Mozilla/5.0 (compatible; source-tracing-research/0.1)"

# Regions whose links and text are site chrome, not the article.
_SKIP_TAGS = {
    "script", "style", "noscript", "template", "svg", "nav", "header", "footer",
    "aside", "form", "button", "iframe", "select", "figure",
}
_VOID_TAGS = {"br", "img", "meta", "link", "input", "hr", "source", "wbr", "area", "base", "col", "embed", "track"}
_BLOCK_TAGS = {
    "p", "div", "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote",
    "br", "tr", "td", "th", "section", "article", "main", "body", "pre", "table", "dd", "dt",
}
_DATE_META_KEYS = {
    "article:published_time", "og:article:published_time", "datepublished",
    "date.issued", "dc.date.issued", "parsely-pub-date", "sailthru.date",
}


class FetchError(RuntimeError):
    pass


class FetchBudgetExhaustedError(FetchError):
    pass


class FetchBudget:
    def __init__(self, max_fetches: int = MAX_PAGE_FETCHES):
        self.max_fetches = min(max_fetches, MAX_PAGE_FETCHES)
        self.used = 0

    @property
    def remaining(self) -> int:
        return self.max_fetches - self.used

    def charge(self) -> None:
        if self.used >= self.max_fetches:
            raise FetchBudgetExhaustedError(f"page fetch budget of {self.max_fetches} exhausted")
        self.used += 1


@dataclass(frozen=True)
class PageLink:
    url: str          # absolute, normalized, public http(s)
    anchor_text: str
    context: str      # text of the block the link sits in


@dataclass
class ParsedPage:
    blocks: list[str] = field(default_factory=list)
    links: list[PageLink] = field(default_factory=list)
    canonical_url: str | None = None
    published: date | None = None
    published_basis: str | None = None
    date_conflict: bool = False


@dataclass
class FetchedPage:
    source_id: str
    requested_url: str
    final_url: str
    retrieved_at: datetime
    parsed: ParsedPage


def _squash(text: str) -> str:
    return " ".join(text.split())


class _Extractor(HTMLParser):
    def __init__(self, base_url: str):
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.blocks: list[str] = []
        self.raw_links: list[tuple[str, str, int]] = []  # href, anchor, block index
        self.canonical: str | None = None
        self.date_candidates: list[tuple[str, str]] = []  # (basis, raw value)
        self._buf: list[str] = []
        self._skip_tag: str | None = None
        self._skip_depth = 0
        self._anchor: tuple[str, list[str]] | None = None
        self._jsonld: list[str] | None = None
        self._pending_links: list[tuple[str, str]] = []

    # -- helpers --
    def _flush(self) -> None:
        text = _squash("".join(self._buf))
        self._buf = []
        if text:
            index = len(self.blocks)
            self.blocks.append(text)
            self.raw_links.extend((href, anchor, index) for href, anchor in self._pending_links)
        self._pending_links = []

    def _close_anchor(self) -> None:
        if self._anchor:
            href, parts = self._anchor
            self._pending_links.append((href, _squash("".join(parts))))
            self._anchor = None

    # -- parser callbacks --
    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        if self._skip_tag:
            if tag == self._skip_tag and tag not in _VOID_TAGS:
                self._skip_depth += 1
            return
        if tag == "script" and "ld+json" in a.get("type", "").lower():
            self._jsonld = []
            return
        if tag == "meta":
            key = (a.get("property") or a.get("name") or a.get("itemprop") or "").lower()
            if key in _DATE_META_KEYS and a.get("content"):
                self.date_candidates.append((f"meta:{key}", a["content"]))
            return
        if tag == "link":
            if "canonical" in a.get("rel", "").lower().split() and a.get("href"):
                self.canonical = a["href"]
            return
        if tag == "base" and a.get("href"):
            self.base_url = urljoin(self.base_url, a["href"])
            return
        hidden = "hidden" in a or a.get("aria-hidden") == "true" or re.search(
            r"display\s*:\s*none|visibility\s*:\s*hidden", a.get("style", ""), re.I
        )
        chrome_region = a.get("role", "").lower() in {"navigation", "banner", "contentinfo", "complementary"}
        sponsored_region = bool(set(a.get("class", "").lower().split()) & {"sponsored", "advertisement", "promoted"})
        if tag in _SKIP_TAGS or ((hidden or chrome_region or sponsored_region) and tag not in _VOID_TAGS):
            self._close_anchor()
            self._skip_tag, self._skip_depth = tag, 1
            return
        if tag == "time" and a.get("datetime") and (
            "datepublished" in a.get("itemprop", "").lower() or "pubdate" in a
        ):
            self.date_candidates.append(("time:datePublished", a["datetime"]))
        if tag in _BLOCK_TAGS:
            self._close_anchor()
            self._flush()
        elif tag == "a":
            self._close_anchor()
            rel = a.get("rel", "").lower().split()
            if a.get("href") and "sponsored" not in rel:
                self._anchor = (a["href"], [])

    def handle_endtag(self, tag):
        if self._jsonld is not None and tag == "script":
            self._read_jsonld("".join(self._jsonld))
            self._jsonld = None
            return
        if self._skip_tag:
            if tag == self._skip_tag:
                self._skip_depth -= 1
                if self._skip_depth == 0:
                    self._skip_tag = None
            return
        if tag == "a":
            self._close_anchor()
        elif tag in _BLOCK_TAGS:
            self._close_anchor()
            self._flush()

    def handle_data(self, data):
        if self._jsonld is not None:
            self._jsonld.append(data)
        elif not self._skip_tag:
            self._buf.append(data)
            if self._anchor:
                self._anchor[1].append(data)

    def close(self):
        super().close()
        self._close_anchor()
        self._flush()

    def _read_jsonld(self, text: str) -> None:
        try:
            data = json.loads(text)
        except ValueError:
            return
        stack, seen = [data], 0
        while stack and seen < 500:
            seen += 1
            node = stack.pop()
            if isinstance(node, dict):
                value = node.get("datePublished")
                if isinstance(value, str):
                    self.date_candidates.append(("jsonld:datePublished", value))
                stack.extend(node.values())
            elif isinstance(node, list):
                stack.extend(node)


def parse_page(html: str, page_url: str, *, today: date | None = None) -> ParsedPage:
    """Extract body text blocks, in-body links, canonical URL and declared publish date."""
    ex = _Extractor(page_url)
    try:
        ex.feed(html)
        ex.close()
    except Exception:  # html.parser is lenient, but never let a page crash a trace
        pass
    page = ParsedPage(blocks=ex.blocks)
    seen: set[tuple[str, int]] = set()
    for href, anchor, index in ex.raw_links:
        try:
            url = normalize_url(urljoin(ex.base_url, href.strip()))
        except (UnsafeUrlError, ValueError):
            continue  # javascript:, file:, private hosts, malformed
        if (url, index) in seen:
            continue
        seen.add((url, index))
        page.links.append(PageLink(url=url, anchor_text=anchor, context=ex.blocks[index]))
    if ex.canonical:
        try:
            page.canonical_url = normalize_url(urljoin(ex.base_url, ex.canonical.strip()))
        except (UnsafeUrlError, ValueError):
            page.canonical_url = None
    # A publish date is accepted only if every parseable declaration agrees.
    parsed = [(basis, parse_reliable_date(raw, today=today)) for basis, raw in ex.date_candidates]
    dates = {d for _, d in parsed if d is not None}
    if len(dates) == 1:
        page.published = dates.pop()
        page.published_basis = next(b for b, d in parsed if d is not None)
    elif len(dates) > 1:
        page.date_conflict = True
    return page


UrlCheck = Callable[[str], str]


def _default_url_check(url: str) -> str:
    return check_public_http_url(url, resolve=True)


class PageFetcher:
    def __init__(
        self,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        url_check: UrlCheck = _default_url_check,
        timeout_s: float = FETCH_TIMEOUT_S,
        max_bytes: int = MAX_PAGE_BYTES,
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ):
        self._transport = transport
        self._url_check = url_check
        self._timeout_s = timeout_s
        self._max_bytes = max_bytes
        self._now = now

    async def fetch_html(self, url: str, budget: FetchBudget) -> tuple[str, str]:
        """Return (html, final_url). One budget unit per page, however many redirects."""
        budget.charge()
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(self._timeout_s), transport=self._transport,
            follow_redirects=False, headers={"User-Agent": USER_AGENT, "Accept": "text/html"},
        ) as http:
            current = url
            for _hop in range(MAX_REDIRECTS + 1):
                try:
                    # The check resolves DNS (blocking); a thread keeps fetches parallel
                    # and the server responsive.
                    await asyncio.to_thread(self._url_check, current)
                except UnsafeUrlError as exc:
                    raise FetchError(f"blocked unsafe URL: {exc}") from None
                try:
                    async with http.stream("GET", current) as resp:
                        if resp.status_code in (301, 302, 303, 307, 308):
                            location = resp.headers.get("location")
                            if not location:
                                raise FetchError("redirect without location")
                            current = urljoin(current, location)
                            continue
                        if resp.status_code != 200:
                            raise FetchError(f"HTTP {resp.status_code}")
                        ctype = resp.headers.get("content-type", "").lower()
                        if "html" not in ctype:
                            raise FetchError(f"not HTML ({ctype.split(';')[0] or 'no content-type'})")
                        declared = resp.headers.get("content-length", "")
                        if declared.isdigit() and int(declared) > self._max_bytes:
                            raise FetchError("page exceeds size limit")
                        chunks, size = [], 0
                        async for chunk in resp.aiter_bytes():
                            size += len(chunk)
                            if size > self._max_bytes:
                                raise FetchError("page exceeds size limit")
                            chunks.append(chunk)
                        encoding = resp.charset_encoding or "utf-8"
                        try:
                            return b"".join(chunks).decode(encoding, errors="replace"), current
                        except LookupError:
                            return b"".join(chunks).decode("utf-8", errors="replace"), current
                except httpx.TimeoutException:
                    raise FetchError(f"timed out after {self._timeout_s:.0f}s") from None
                except httpx.HTTPError as exc:
                    raise FetchError(f"connection failed: {type(exc).__name__}") from None
            raise FetchError("too many redirects")

    async def fetch_pages(
        self, targets: list[tuple[str, str]], budget: FetchBudget | None = None
    ) -> tuple[list[FetchedPage], list[str]]:
        """Fetch (source_id, url) pairs, in order, up to the budget. Failures become warnings."""
        budget = budget or FetchBudget()
        chosen = targets[: budget.remaining]
        warnings = []
        if len(targets) > len(chosen):
            warnings.append(
                f"{len(targets) - len(chosen)} source page(s) not fetched: page fetch limit is {budget.max_fetches}"
            )

        async def one(source_id: str, url: str) -> FetchedPage | str:
            try:
                html, final_url = await self.fetch_html(url, budget)
            except FetchError as exc:
                return f"{source_id}: page not fetched ({exc})"
            return FetchedPage(
                source_id=source_id, requested_url=url, final_url=final_url,
                retrieved_at=self._now(), parsed=parse_page(html, final_url),
            )

        results = await asyncio.gather(*(one(sid, url) for sid, url in chosen))
        pages = [r for r in results if isinstance(r, FetchedPage)]
        warnings.extend(r for r in results if isinstance(r, str))
        return pages, warnings
