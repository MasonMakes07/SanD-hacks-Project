"""Offline tests for Stage 2 retrieval. httpx.MockTransport stands in for Bright Data."""

from __future__ import annotations

import asyncio
import json
import socket
from datetime import date, datetime, timezone

import httpx
import pytest

from source_tracing.config import ConfigError, BrightDataSettings, load_settings
from source_tracing.dates import parse_reliable_date
from source_tracing.query import build_queries
from source_tracing.retrieval import retrieve_sources, select_sources, parse_organic
from source_tracing.serp_client import (
    API_URL, BrightDataSerpClient, BudgetExhaustedError, SerpAuthError, SerpBudget,
    SerpError, SerpResponseError, SerpTimeoutError, SerpZoneError,
)
from source_tracing.urls import UnsafeUrlError, check_public_http_url, dedup_key, normalize_url, normalize_serp_url

CLAIM = "The city council approved a 12% increase in the water utility rate"
NOW = datetime(2026, 11, 14, 20, 0, tzinfo=timezone.utc)
SECRET = "test-key-should-never-leak"


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def blocked(*a, **k):
        raise AssertionError("offline tests must not open sockets")

    monkeypatch.setattr(socket.socket, "connect", blocked)


def settings(**kw) -> BrightDataSettings:
    base = dict(api_key=SECRET, serp_zone="test_zone", timeout_s=5.0, max_retries=1)
    base.update(kw)
    return BrightDataSettings(**base)


def organic(*entries) -> dict:
    return {"organic": list(entries)}


def entry(link, title="City council approves water rate increase", desc="Council approved the utility rate increase of 12%", **kw):
    return {"rank": kw.pop("rank", 1), "link": link, "title": title, "description": desc, **kw}


class Recorder:
    """MockTransport handler that replays queued responses and records requests."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        nxt = self.responses.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt

    def client(self, **kw) -> BrightDataSerpClient:
        return BrightDataSerpClient(settings(**kw), transport=httpx.MockTransport(self))


def ok(payload) -> httpx.Response:
    return httpx.Response(200, json=payload)


# ---------- request format ----------

def test_request_matches_documented_rest_format():
    rec = Recorder(ok(organic()))
    run(rec.client().search("water rate", SerpBudget()))
    req = rec.requests[0]
    assert str(req.url) == API_URL and req.method == "POST"
    assert req.headers["authorization"] == f"Bearer {SECRET}"
    body = json.loads(req.content)
    assert set(body) == {"zone", "url", "format"}
    assert body["zone"] == "test_zone" and body["format"] == "raw"
    assert body["url"].startswith("https://www.google.com/search?")
    assert "brd_json=1" in body["url"] and "q=water+rate" in body["url"]


# ---------- good / malformed / empty ----------

def test_good_results_are_parsed_and_normalized():
    rec = Recorder(ok(organic(
        entry("https://www.example.org/news/water?utm_source=x&id=7#top", rank=1, date="2026-03-02"),
        entry("https://news.example.com/council-rates", rank=2, display_link="2 days ago"),
    )))
    res = run(retrieve_sources(CLAIM, rec.client(), budget=SerpBudget(1), now=lambda: NOW))
    assert [s.id for s in res.sources] == ["s1", "s2"]
    s1, s2 = res.sources
    assert s1.url == "https://www.example.org/news/water?id=7"
    assert s1.date == date(2026, 3, 2) and s1.provenance.date_field == "date"
    assert s2.date is None  # display_link "2 days ago" is never used
    assert s1.provenance.retrieved_at == NOW
    assert s1.provenance.original_url.endswith("#top")
    assert s1.provenance.query == build_queries(CLAIM)[0]
    assert res.serp_requests == 1
    # Converts cleanly to Person 2's contract model.
    assert set(s1.to_source().model_dump()) == {"id", "title", "url", "date", "excerpt"}


def test_malformed_entries_are_skipped_not_invented():
    rec = Recorder(ok(organic(
        "not a dict",
        {"rank": 1, "title": "No link water rate council"},
        {"rank": 2, "link": "https://example.org/no-title", "title": ""},
        {"rank": 3, "link": 12345, "title": "Water rate council"},
        entry("javascript:alert(1)"),
        entry("http://192.168.1.10/admin"),
        entry("https://example.org/good"),
    )))
    res = run(retrieve_sources(CLAIM, rec.client(), budget=SerpBudget(1)))
    assert [s.url for s in res.sources] == ["https://example.org/good"]
    assert res.dropped == {"empty": 3, "unsafe_url": 2}
    assert res.raw_results == 7


@pytest.mark.parametrize("payload", [{}, {"organic": []}, {"organic": None}, {"general": {}}])
def test_empty_results_return_zero_sources_with_warning(payload):
    rec = Recorder(ok(payload), ok(payload))
    res = run(retrieve_sources(CLAIM, rec.client()))
    assert res.sources == []
    assert "no relevant sources found" in res.warnings


@pytest.mark.parametrize("resp", [
    httpx.Response(200, text="<html>not json</html>"),
    httpx.Response(200, json=["a", "list"]),
    httpx.Response(200, json={"organic": "oops"}),
])
def test_malformed_response_raises(resp):
    rec = Recorder(resp)
    with pytest.raises(SerpResponseError):
        run(retrieve_sources(CLAIM, rec.client(max_retries=0)))


def test_non_json_response_then_success_uses_retry():
    rec = Recorder(httpx.Response(200, text="<html>not json</html>"),
                   ok(organic(entry("https://example.org/a"))))
    budget = SerpBudget()
    res = run(retrieve_sources(CLAIM, rec.client(), budget=budget))
    assert len(res.sources) == 1 and budget.used == 2 and len(rec.requests) == 2


def test_non_json_response_twice_raises_and_counts_both_attempts():
    bad = httpx.Response(200, text="<html>not json</html>")
    rec = Recorder(bad, bad)
    budget = SerpBudget()
    with pytest.raises(SerpResponseError):
        run(retrieve_sources(CLAIM, rec.client(), budget=budget))
    assert budget.used == 2 and len(rec.requests) == 2


def test_non_json_response_not_retried_past_budget():
    rec = Recorder(httpx.Response(200, text="<html>not json</html>"))
    budget = SerpBudget(1)
    with pytest.raises(SerpResponseError):
        run(retrieve_sources(CLAIM, rec.client(), budget=budget))
    assert budget.used == 1 and len(rec.requests) == 1


def test_body_wrapped_json_is_unwrapped():
    wrapped = {"status_code": 200, "body": json.dumps(organic(entry("https://example.org/a")))}
    rec = Recorder(ok(wrapped))
    res = run(retrieve_sources(CLAIM, rec.client(), budget=SerpBudget(1)))
    assert len(res.sources) == 1


def test_wrapped_provider_error_is_not_treated_as_a_valid_serp():
    with pytest.raises(SerpAuthError):
        run(Recorder(ok({"status_code": 403, "body": json.dumps(organic(entry("https://example.org/a")))}))
            .client(max_retries=0).search("q", SerpBudget()))


# ---------- errors, timeouts, retries ----------

def test_timeout_retries_once_then_raises_and_counts_both_attempts():
    rec = Recorder(httpx.ReadTimeout("slow"), httpx.ReadTimeout("slow"))
    budget = SerpBudget()
    with pytest.raises(SerpTimeoutError):
        run(retrieve_sources(CLAIM, rec.client(), budget=budget))
    assert budget.used == 2 and len(rec.requests) == 2


def test_timeout_then_success_uses_retry():
    rec = Recorder(httpx.ConnectTimeout("x"), ok(organic(entry("https://example.org/a"))))
    budget = SerpBudget()
    res = run(retrieve_sources(CLAIM, rec.client(), budget=budget))
    assert len(res.sources) == 1 and budget.used == 2
    # Budget is spent, so the second query is skipped rather than overspending.
    assert "second query skipped: SERP budget exhausted" in res.warnings


@pytest.mark.parametrize("status,exc", [(401, SerpAuthError), (403, SerpAuthError), (407, SerpAuthError)])
def test_auth_errors_are_not_retried(status, exc):
    rec = Recorder(httpx.Response(status, text="denied"))
    with pytest.raises(exc):
        run(retrieve_sources(CLAIM, rec.client()))
    assert len(rec.requests) == 1


def test_zone_error_is_reported_clearly():
    rec = Recorder(httpx.Response(400, text='zone "test_zone" not found'))
    with pytest.raises(SerpZoneError, match="zone"):
        run(retrieve_sources(CLAIM, rec.client()))


def test_server_error_retried_within_budget():
    rec = Recorder(httpx.Response(502), httpx.Response(503))
    with pytest.raises(SerpError, match="503"):
        run(retrieve_sources(CLAIM, rec.client()))
    assert len(rec.requests) == 2


def test_errors_never_contain_the_api_key():
    for resp in (httpx.Response(401, text="bad"),
                 httpx.Response(400, text=f"zone missing; token={SECRET}")):
        with pytest.raises(SerpError) as info:
            run(Recorder(resp).client().search("q", SerpBudget()))
        assert SECRET not in str(info.value)
    assert SECRET not in repr(settings())


def test_second_query_failure_keeps_first_results():
    rec = Recorder(ok(organic(entry("https://example.org/a"))), httpx.Response(401))
    res = run(retrieve_sources(CLAIM, rec.client(max_retries=0)))
    assert len(res.sources) == 1
    assert any("second query failed" in w for w in res.warnings)


# ---------- duplicates ----------

def test_duplicate_urls_collapse_across_queries():
    first = ok(organic(
        entry("https://www.example.org/story/"),
        entry("http://example.org/story?utm_campaign=z"),
        entry("https://example.org/story#comments"),
        entry("https://www.google.com/url?q=https://example.org/story&sa=U"),
    ))
    second = ok(organic(entry("https://EXAMPLE.org/story"), entry("https://other.example.net/b")))
    rec = Recorder(first, second)
    res = run(retrieve_sources(CLAIM, rec.client()))
    assert [s.url for s in res.sources] == ["https://www.example.org/story/", "https://other.example.net/b"]
    assert res.dropped["duplicate"] == 4
    assert res.serp_requests == 2 and len(res.queries) == 2


# ---------- budget ----------

def test_never_more_than_two_serp_requests_or_ten_sources():
    many = [entry(f"https://site{i}.example.org/a", rank=i) for i in range(15)]
    rec = Recorder(ok(organic(*many)), ok(organic(*many)))
    res = run(retrieve_sources(CLAIM, rec.client(), max_sources=50))
    assert len(res.sources) == 10
    assert len(rec.requests) == 1  # first query filled 10, second not needed
    assert res.dropped["over_limit"] == 5


def test_budget_exhaustion_blocks_calls():
    budget = SerpBudget()
    budget.charge(); budget.charge()
    rec = Recorder()
    with pytest.raises(BudgetExhaustedError):
        run(retrieve_sources(CLAIM, rec.client(), budget=budget))
    assert rec.requests == []


def test_budget_cannot_be_raised_above_cap():
    assert SerpBudget(99).max_requests == 2


# ---------- relevance ----------

def test_irrelevant_results_are_filtered():
    rec = Recorder(ok(organic(
        entry("https://example.org/recipe", title="Best banana bread recipe", desc="Bake for an hour"),
        entry("https://example.org/rates"),
    )), ok(organic()))
    res = run(retrieve_sources(CLAIM, rec.client()))
    assert [s.url for s in res.sources] == ["https://example.org/rates"]
    assert res.dropped["irrelevant"] == 1


# ---------- query builder ----------

def test_query_builder():
    qs = build_queries('  "The city council approved a 12% increase"  ')
    assert qs[0] == "The city council approved a 12% increase"
    assert len(qs) == 2 and "the" not in qs[1].split()
    assert len(build_queries(" ".join(["word"] * 50))[0].split()) == 30
    assert build_queries("vaccines") == ["vaccines"]
    with pytest.raises(ValueError):
        build_queries('  ""  ')


# ---------- dates ----------

@pytest.mark.parametrize("value,expected", [
    ("2026-01-15", date(2026, 1, 15)),
    ("2026-01-15T08:30:00Z", date(2026, 1, 15)),
    ("Jan 15, 2026", date(2026, 1, 15)),
    ("15 January 2026", date(2026, 1, 15)),
    ("3 days ago", None), ("2 months ago", None), ("2026", None), ("January 2026", None),
    ("03/04/2026", None), ("2099-01-01", None), ("1970-01-01", None), ("", None), (None, None), (20260115, None),
])
def test_reliable_dates_only(value, expected):
    assert parse_reliable_date(value, today=date(2026, 11, 14)) == expected


# ---------- URL safety ----------

@pytest.mark.parametrize("url", [
    "http://localhost/x", "http://127.0.0.1/", "http://10.0.0.5/", "http://169.254.169.254/latest/meta-data",
    "http://[::1]/", "http://[::ffff:127.0.0.1]/", "http://2130706433/", "http://0x7f.1/", "http://intranet/",
    "http://printer.local/", "https://user:pw@example.org/", "https://example.org:8080/", "file:///etc/passwd",
    "ftp://example.org/", "gopher://example.org/", "http:///nohost", "https://exa mple.org:bad/",
    "https://example.org/" + "a" * 3000,
])
def test_unsafe_urls_rejected(url):
    with pytest.raises(UnsafeUrlError):
        check_public_http_url(url)


def test_public_url_accepted_and_resolve_check(monkeypatch):
    assert check_public_http_url("https://example.org/a")
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("10.1.2.3", 443))])
    with pytest.raises(UnsafeUrlError, match="non-public"):
        check_public_http_url("https://rebind.example.org/", resolve=True)
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("93.184.216.34", 443))])
    assert check_public_http_url("https://example.org/", resolve=True)


def test_normalize_keeps_meaningful_query_and_path_case():
    assert normalize_url("HTTPS://Example.ORG:443/Path/A?id=3&utm_medium=x&fbclid=y#f") == "https://example.org/Path/A?id=3"
    assert dedup_key("https://www.example.org/a/") == dedup_key("http://example.org/a")


def test_serp_accepts_public_articles_and_validated_relative_google_redirect():
    rows, _ = parse_organic(organic(
        entry("http://example.org/article"),
        entry("https://news.example.org/article"),
        entry("/url?sa=U&url=https%3A%2F%2Fscience.example.org%2Farticle%3Futm_source%3Dsearch"),
    ), query=CLAIM, retrieved_at=NOW)
    sources, dropped = select_sources(rows, CLAIM)
    assert [source.url for source in sources] == [
        "http://example.org/article",
        "https://news.example.org/article",
        "https://science.example.org/article",
    ]
    assert not dropped


@pytest.mark.parametrize("url", [
    "/url?q=javascript%3Aalert%281%29",
    "/url?q=file%3A%2F%2F%2Fetc%2Fpasswd",
    "/url?q=http%3A%2F%2F127.0.0.1%2Fprivate",
    "/url?q=http%3A%2F%2F169.254.169.254%2Fprivate",
    "/url?q=https%3A%2F%2Fprinter.local%2Fprivate",
    "/url?q=https%3A%2F%2Fwww.google.com%2Furl%3Fq%3Dhttp%253A%252F%252F127.0.0.1%252F",
    "/url?sa=U", "/article/without-a-target",
])
def test_serp_relative_redirect_cannot_bypass_url_safety(url):
    with pytest.raises(UnsafeUrlError):
        normalize_serp_url(url)


def test_serp_rejects_missing_urls_without_crashing():
    rows, _ = parse_organic(organic(
        {"title": "Council water rate story", "description": "city council water rate"},
        {"title": "Council water rate story", "link": None, "description": "city council water rate"},
    ), query=CLAIM, retrieved_at=NOW)
    sources, dropped = select_sources(rows, CLAIM)
    assert sources == [] and dropped == {"empty": 2}


def test_non_google_redirect_does_not_supply_a_destination():
    assert normalize_serp_url("https://search.example.org/url?q=https://example.org/article") == \
           "https://search.example.org/url?q=https%3A%2F%2Fexample.org%2Farticle"


# ---------- config ----------

def test_missing_config_names_variables_without_values(tmp_path):
    with pytest.raises(ConfigError, match="BRIGHTDATA_API_KEY, BRIGHTDATA_SERP_ZONE"):
        load_settings(env={}, env_file=None)
    with pytest.raises(ConfigError, match="BRIGHTDATA_SERP_ZONE"):
        load_settings(env={"BRIGHTDATA_API_KEY": SECRET}, env_file=None)


def test_env_file_is_read_and_env_wins(tmp_path):
    f = tmp_path / ".env"
    f.write_text('BRIGHTDATA_API_KEY="from-file"\nBRIGHTDATA_SERP_ZONE=file_zone\n# comment\n')
    s = load_settings(env={"BRIGHTDATA_SERP_ZONE": "env_zone"}, env_file=f)
    assert s.api_key == "from-file" and s.serp_zone == "env_zone"


@pytest.mark.parametrize("timeout", ["abc", "nan", "inf"])
def test_bad_timeout_configuration_is_a_safe_config_error(timeout):
    with pytest.raises(ConfigError, match="BRIGHTDATA_TIMEOUT_S"):
        load_settings(env={"BRIGHTDATA_API_KEY": SECRET, "BRIGHTDATA_SERP_ZONE": "test_zone",
                           "BRIGHTDATA_TIMEOUT_S": timeout}, env_file=None)


def test_parse_organic_ignores_display_link():
    rows, n = parse_organic(organic({"link": "https://a.example.org/x", "title": "t",
                                     "display_link": "Jan 2, 2026", "description": "d"}),
                            query="q", retrieved_at=NOW)
    assert n == 1 and rows[0]["date"] is None


def test_malformed_second_serp_payload_keeps_first_results():
    rec = Recorder(ok(organic(entry("https://example.org/a"))), ok({"organic": {"bad": "shape"}}))
    result = run(retrieve_sources(CLAIM, rec.client(max_retries=0)))
    assert [s.url for s in result.sources] == ["https://example.org/a"]
    assert any("second query failed" in warning for warning in result.warnings)


def test_iso_date_with_trailing_junk_is_not_reliable():
    assert parse_reliable_date("2026-03-02T10:00:00Z unrelated") is None


def test_normalized_public_ipv6_url_keeps_brackets():
    assert normalize_url("https://[2606:4700:4700::1111]/a") == "https://[2606:4700:4700::1111]/a"
