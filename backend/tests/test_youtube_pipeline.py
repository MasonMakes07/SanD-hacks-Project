"""End-to-end YouTube checks with fake Gemini, Fact Check and Neon cache."""

import json

import httpx
from fastapi.testclient import TestClient

from app import analysis, main, source_provider
from app.analysis import run_investigation
from app.config import get_settings
from app.models import AnalyzeRequest, VideoClaimExtraction
from app.Translator import to_analysis_input
from tests.conftest import AUTH_HEADERS, gemini_body, make_http_client

VIDEO_ID = "N-q2z4N_Rsk"
VIDEO_PAGE = f"https://www.youtube.com/watch?v={VIDEO_ID}&t=140s"
TITLE = "DART impact successfully moved asteroid Dimorphos! NASA chief explains"
VIDEO_RESULT = {
    "checkable": True,
    "claim": "DART changed Dimorphos's orbit by 32 minutes.",
    "timestamp": "2:21",
    "quote": "altered Dimorphos orbit around Didymos by 32 minutes",
    "speaker": "Bill Nelson",
}


# Builds a fake upstream that answers video, text and fact-check requests.
def make_handler(video_reply=None, text_claim="Title claim.", log=None):
    """Return a handler; video_reply may be a dict, an int status, or a list."""
    replies = list(video_reply) if isinstance(video_reply, list) else None

    # Routes each request by host and by whether it carries a video part.
    def handler(request: httpx.Request) -> httpx.Response:
        """Return the canned response for this upstream call."""
        if request.url.host == "factchecktools.googleapis.com":
            return httpx.Response(200, json={})
        parts = json.loads(request.content)["contents"][0]["parts"]
        is_video = "file_data" in parts[0]
        if log is not None:
            log.append(parts[0].get("videoMetadata") if is_video else "text")
        if not is_video:
            return httpx.Response(
                200, json=gemini_body({"checkable": True, "claim": text_claim})
            )
        reply = replies.pop(0) if replies is not None else video_reply
        if isinstance(reply, int):
            return httpx.Response(reply)
        return httpx.Response(200, json=gemini_body(reply or VIDEO_RESULT))

    return handler


# Runs one YouTube investigation through the real pipeline.
async def investigate(handler, current=141.0, duration=205.0, url=VIDEO_PAGE):
    """Return the AnalyzeResponse for a video check at `current` seconds."""
    analysis_input = to_analysis_input(
        AnalyzeRequest(
            text=TITLE,
            url=url,
            mode="manual",
            video_time_seconds=current,
            video_duration_seconds=duration,
        )
    )
    async with make_http_client(handler) as client:
        result = await run_investigation(client, get_settings(), analysis_input)
    await analysis.drain_background_tasks()
    return result


# Checks a video claim is checked and linked to its timestamp.
async def test_video_claim_with_timestamp_link():
    """The clip claim is used and claim_source links to 2:21 with the quote."""
    log = []
    result = await investigate(make_handler(log=log))
    assert result.claim == VIDEO_RESULT["claim"]
    source = result.claim_source
    assert source.type == "video" and source.timestamp == "2:21"
    assert source.timestamp_seconds == 141
    assert source.timestamp_url == f"https://www.youtube.com/watch?v={VIDEO_ID}&t=141s"
    assert source.speaker == "Bill Nelson" and "32 minutes" in source.quote
    assert log == [{"startOffset": "90s", "endOffset": "165s", "fps": 0.5}]


# Checks repeat checks of the same moment skip the video model.
async def test_same_moment_is_served_from_memory_cache():
    """A second check in the same 15-second bucket sends no video request."""
    log = []
    first = await investigate(make_handler(log=log), current=141)
    second = await investigate(make_handler(log=log), current=137)
    assert first.claim == second.claim
    assert [entry for entry in log if entry != "text"] == [
        {"startOffset": "90s", "endOffset": "165s", "fps": 0.5}
    ]


# Checks a Neon cache hit skips Gemini video and a miss is written back.
async def test_neon_video_cache_hit_and_write_back(monkeypatch):
    """Neon hit: no video call. Miss: the new claim is saved to Neon."""
    saved = []
    neon_claim = VideoClaimExtraction(True, "Cached claim.", 150, "cached words", "")

    # Returns a hit only for the 1:30-2:45 window.
    async def fake_find(video_id, window):
        """Return neon_claim for (90, 165), else None."""
        return neon_claim if window == (90, 165) else None

    # Records Neon writes.
    async def fake_save(video_id, window, extraction):
        """Append the write to saved."""
        saved.append((video_id, window, extraction.claim))

    monkeypatch.setattr(source_provider, "find_video_claim", fake_find)
    monkeypatch.setattr(source_provider, "save_video_claim", fake_save)
    log = []
    hit = await investigate(make_handler(log=log), current=141)
    assert hit.claim == "Cached claim." and hit.claim_source.timestamp == "2:30"
    assert "text" not in log and not any(isinstance(e, dict) for e in log)
    miss = await investigate(make_handler(log=log), current=30)
    assert miss.claim == VIDEO_RESULT["claim"]
    assert saved == [(VIDEO_ID, (0, 45), VIDEO_RESULT["claim"])]


# Checks a slow or broken Neon lookup never blocks the check.
async def test_neon_lookup_failure_is_a_cache_miss(monkeypatch):
    """A raising Neon cache falls through to Gemini video."""

    # Simulates a Neon outage.
    async def broken_find(video_id, window):
        """Raise like a dropped connection."""
        raise RuntimeError("neon down")

    monkeypatch.setattr(source_provider, "find_video_claim", broken_find)
    result = await investigate(make_handler())
    assert result.claim == VIDEO_RESULT["claim"]


# Checks a clip with no checkable claim falls back to the title.
async def test_uncheckable_clip_falls_back_to_title():
    """checkable=False from the video model uses text extraction on the title."""
    result = await investigate(
        make_handler(video_reply={**VIDEO_RESULT, "checkable": False})
    )
    assert result.claim == "Title claim." and result.claim_source is None


# Checks a video model outage falls back to the title.
async def test_video_model_outage_falls_back_to_title():
    """503 from both video models still produces a text-based check."""
    result = await investigate(make_handler(video_reply=503))
    assert result.claim == "Title claim." and result.claim_source is None


# Checks an out-of-range window (HTTP 400) is retried on the video's opening.
async def test_window_outside_video_is_retried_on_opening():
    """The first 400s (both models) trigger one retry on 0-90s."""
    log = []
    result = await investigate(
        make_handler(video_reply=[400, 400, VIDEO_RESULT], log=log),
        current=600,
        duration=None,
    )
    windows = [entry for entry in log if isinstance(entry, dict)]
    assert windows[-1] == {"startOffset": "0s", "endOffset": "90s", "fps": 0.5}
    assert result.claim == VIDEO_RESULT["claim"]


# Checks non-YouTube pages ignore video fields entirely.
async def test_video_fields_ignored_for_other_sites():
    """A news URL with video_time_seconds never calls the video model."""
    log = []
    result = await investigate(
        make_handler(log=log), url="https://news.example/article"
    )
    assert result.claim_source is None and log == ["text"]


# Checks the API accepts and validates the new video fields.
def test_api_validates_video_fields(monkeypatch):
    """Negative or huge positions are 422; valid ones reach the pipeline."""
    monkeypatch.setenv("USE_MOCK", "true")
    get_settings.cache_clear()
    body = {"text": TITLE, "url": VIDEO_PAGE, "mode": "manual"}
    with TestClient(main.app) as client:
        for bad in ({"video_time_seconds": -1}, {"video_duration_seconds": 0},
                    {"video_time_seconds": 10**6}):
            response = client.post(
                "/api/analyze", json={**body, **bad}, headers=AUTH_HEADERS
            )
            assert response.status_code == 422
        ok = client.post(
            "/api/analyze",
            json={**body, "video_time_seconds": 141.5, "video_duration_seconds": 205},
            headers=AUTH_HEADERS,
        )
    assert ok.status_code == 200 and "claim_source" in ok.json()
