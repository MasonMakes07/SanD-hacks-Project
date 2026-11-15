"""URL normalization and SSRF-safe validation."""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit, urlunsplit

MAX_URL_LENGTH = 2048

# Query parameters that only track the click; removing them never changes the page.
_TRACKING_PARAMS = {
    "fbclid", "gclid", "dclid", "gbraid", "wbraid", "msclkid", "yclid", "twclid",
    "igshid", "mc_cid", "mc_eid", "_ga", "_gl", "ved", "usg", "sa", "ei", "srsltid",
    "ref_src", "cmpid", "ocid", "ncid",
}
_TRACKING_PREFIXES = ("utm_",)
_BLOCKED_HOST_SUFFIXES = (".localhost", ".local", ".internal", ".lan", ".home", ".corp", ".intranet")
# Google result redirects: /url?q=<target> and (seen live in Bright Data SERP rows) /goto?url=<target>.
_GOOGLE_REDIRECT_PATHS = ("/url", "/goto")


# Social platforms. Their pages are login-walled, so they are never fetched; a post is
# known only from its search-result snippet.
SOCIAL_HOSTS = (
    "x.com", "twitter.com", "reddit.com", "threads.net", "threads.com", "bsky.app", "facebook.com",
    "instagram.com", "tiktok.com", "truthsocial.com", "mastodon.social", "linkedin.com",
)


class UnsafeUrlError(ValueError):
    pass


def is_social_url(url: str) -> bool:
    """True when the URL's host is a SOCIAL_HOSTS domain or a subdomain of one."""
    try:
        host = (urlsplit(url).hostname or "").lower().rstrip(".")
    except ValueError:
        return False
    return any(host == domain or host.endswith("." + domain) for domain in SOCIAL_HOSTS)


def _is_tracking(key: str) -> bool:
    k = key.lower()
    return k in _TRACKING_PARAMS or k.startswith(_TRACKING_PREFIXES)


def unwrap_redirect(url: str) -> str:
    """Return a validated target of a Google `/url` or `/goto` redirect, else unchanged."""
    try:
        parts = urlsplit(url)
    except ValueError:
        raise UnsafeUrlError("malformed redirect URL") from None
    host = (parts.hostname or "").lower()
    if (host == "google.com" or host.endswith(".google.com")) and parts.path in _GOOGLE_REDIRECT_PATHS:
        params = dict(parse_qsl(parts.query))
        target = params.get("q") or params.get("url")
        if not target:
            raise UnsafeUrlError("Google redirect has no destination")
        check_public_http_url(target)
        target_parts = urlsplit(target)
        target_host = (target_parts.hostname or "").lower()
        if (target_host == "google.com" or target_host.endswith(".google.com")) and target_parts.path in _GOOGLE_REDIRECT_PATHS:
            raise UnsafeUrlError("nested Google redirect")
        return target
    return url


def _ip_is_public(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast


def check_public_http_url(url: str, *, resolve: bool = False) -> str:
    """Raise UnsafeUrlError unless `url` is an http(s) URL to a public host.

    With resolve=True, every address the hostname resolves to must be public
    (use before fetching a page). Without it, only literal IPs and obviously
    private names are rejected (enough for URLs we only display).
    """
    if not isinstance(url, str) or not url or len(url) > MAX_URL_LENGTH:
        raise UnsafeUrlError("empty or overlong URL")
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError as exc:
        raise UnsafeUrlError(f"malformed URL: {exc}") from None
    if parts.scheme not in ("http", "https"):
        raise UnsafeUrlError("scheme must be http or https")
    if parts.username or parts.password:
        raise UnsafeUrlError("credentials in URL are not allowed")
    host = (parts.hostname or "").rstrip(".").lower()
    if not host:
        raise UnsafeUrlError("missing host")
    if port is not None and port not in (80, 443):
        raise UnsafeUrlError("non-standard port")
    if host == "localhost" or host.endswith(_BLOCKED_HOST_SUFFIXES):
        raise UnsafeUrlError("private hostname")
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
        if "." not in host:
            raise UnsafeUrlError("single-label hostname") from None
        # Integer / hex forms like http://2130706433/ are caught as no-dot above
        # or by inet_aton below.
        try:
            literal = ipaddress.ip_address(socket.inet_aton(host))
        except OSError:
            pass
    if literal is not None and not _ip_is_public(literal):
        raise UnsafeUrlError("non-public IP address")
    if resolve and literal is None:
        try:
            infos = socket.getaddrinfo(host, port or (443 if parts.scheme == "https" else 80))
        except socket.gaierror:
            raise UnsafeUrlError("hostname does not resolve") from None
        for info in infos:
            if not _ip_is_public(ipaddress.ip_address(info[4][0].split("%")[0])):
                raise UnsafeUrlError("hostname resolves to a non-public address")
    return url


def normalize_url(url: str) -> str:
    """Canonical form for display and dedup. Raises UnsafeUrlError if unsafe.

    Lowercases scheme/host, drops default ports, fragments and tracking
    parameters, and unwraps Google redirects. Path case and real query
    parameters are kept, since they can select different pages.
    """
    url = unwrap_redirect(url.strip())
    check_public_http_url(url)
    parts = urlsplit(url)
    host = parts.hostname.rstrip(".").lower()
    if ":" in host:  # urlunsplit requires brackets around an IPv6 literal
        host = f"[{host}]"
    if parts.port and parts.port not in (80, 443):
        host = f"{host}:{parts.port}"
    query = urlencode([(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if not _is_tracking(k)])
    path = parts.path or "/"
    return urlunsplit((parts.scheme.lower(), host, path, query, ""))


def normalize_serp_url(url: str) -> str:
    """Normalize a SERP link, accepting only Google's relative `/url` and `/goto` redirect forms.

    The redirect target must be present and pass the same public http(s)
    validation as an absolute article URL. Other relative paths have no
    defensible article destination and are rejected.
    """
    if not isinstance(url, str):
        raise UnsafeUrlError("missing URL")
    raw = url.strip()
    try:
        parts = urlsplit(raw)
    except ValueError:
        raise UnsafeUrlError("malformed SERP URL") from None
    if not parts.scheme and not parts.netloc:
        if parts.path not in _GOOGLE_REDIRECT_PATHS or not raw.startswith("/"):
            raise UnsafeUrlError("relative SERP link has no verified destination")
        raw = "https://www.google.com" + raw
    return normalize_url(raw)


def dedup_key(normalized_url: str) -> str:
    """Looser key: treats http/https, `www.`, a trailing slash and percent-encoding as the same page."""
    parts = urlsplit(normalized_url)
    host = parts.netloc.removeprefix("www.")
    path = unquote(parts.path).rstrip("/") or "/"
    return f"{host}{path}?{parts.query}" if parts.query else f"{host}{path}"
