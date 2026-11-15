"""Tests for hedged Gemini calls and YouTube clip claim extraction."""

import asyncio
import json
import time

import httpx
import pytest

from app.config import get_settings
from app.gemini_client import extract_claim, extract_video_claim
from app.models import UpstreamError
from tests.conftest import gemini_body, make_http_client

VIDEO_URL = "https://www.youtube.com/watch?v=N-q2z4N_Rsk"
VIDEO_RESULT = {
    "checkable": True,
    "claim": "DART changed Dimorphos's orbit by 32 minutes.",
    "timestamp": "2:21",
    "quote": "altered Dimorphos orbit around Didymos by 32 minutes",
    "speaker": "Bill Nelson",
}


# Returns the model name from a generateContent request URL.
def model_of(request: httpx.Request) -> str:
    """Extract <model> from .../models/<model>:generateContent."""
    return request.url.path.rsplit("/", 1)[-1].split(":")[0]


# Checks the video request asks for a small, low-cost clip of the right video.
async def test_extract_video_claim_request_is_clipped_and_low_cost():
    """file_data, clip offsets, sparse frames, low resolution and the video model."""
    captured = []

    # Records the request and returns a canned clip claim.
    def handler(request):
        """Return VIDEO_RESULT."""
        captured.append(request)
        return httpx.Response(200, json=gemini_body(VIDEO_RESULT))

    async with make_http_client(handler) as client:
        result = await extract_video_claim(client, get_settings(), VIDEO_URL, (90, 165))
    body = json.loads(captured[0].content)
    video_part, task_part = body["contents"][0]["parts"]
    assert model_of(captured[0]) == "gemini-video-test"
    assert video_part["file_data"] == {"file_uri": VIDEO_URL}
    assert video_part["videoMetadata"] == {
        "startOffset": "90s",
        "endOffset": "165s",
        "fps": 0.5,
    }
    assert body["generationConfig"]["mediaResolution"] == "MEDIA_RESOLUTION_LOW"
    assert "untrusted" in body["systemInstruction"]["parts"][0]["text"]
    assert "90s to 165s" in task_part["text"]
    assert result.checkable and result.timestamp_seconds == 141
    assert result.speaker == "Bill Nelson" and "32 minutes" in result.quote


# Checks clip-relative timestamps and uncheckable clips are handled.
async def test_extract_video_claim_normalizes_output():
    """A clip-relative 0:51 maps to 2:21; no claim gives checkable=False."""
    replies = iter(
        [
            {**VIDEO_RESULT, "timestamp": "0:51"},
            {**VIDEO_RESULT, "checkable": False, "timestamp": "junk"},
        ]
    )

    # Returns the next canned reply.
    def handler(request):
        """Return the next item of replies."""
        return httpx.Response(200, json=gemini_body(next(replies)))

    async with make_http_client(handler) as client:
        settings = get_settings()
        shifted = await extract_video_claim(client, settings, VIDEO_URL, (90, 165))
        empty = await extract_video_claim(client, settings, VIDEO_URL, (90, 165))
    assert shifted.timestamp_seconds == 141
    assert not empty.checkable and empty.claim == ""
    assert empty.timestamp_seconds is None


# Checks a slow primary model is raced and beaten by the backup model.
async def test_slow_primary_is_hedged_by_backup():
    """After the hedge delay the backup is sent and its answer is used."""
    models = []

    # Delays the primary model and answers the backup at once.
    async def handler(request):
        """Sleep 2s for the primary, reply instantly for the backup."""
        models.append(model_of(request))
        if model_of(request) == "gemini-test":
            await asyncio.sleep(2)
        return httpx.Response(
            200, json=gemini_body({"checkable": True, "claim": model_of(request)})
        )

    start = time.monotonic()
    async with make_http_client(handler) as client:
        result = await extract_claim(client, get_settings(), "text")
    assert result.claim == "gemini-backup-test"
    assert models == ["gemini-test", "gemini-backup-test"]
    assert time.monotonic() - start < 1.5


# Checks an overloaded (503) primary sends the backup immediately.
async def test_overloaded_primary_falls_back_without_waiting():
    """A fast failure does not wait for the hedge delay."""
    models = []

    # Fails the primary with 503 and answers the backup.
    def handler(request):
        """Return 503 for the primary, a claim for the backup."""
        models.append(model_of(request))
        if model_of(request) == "gemini-test":
            return httpx.Response(503)
        return httpx.Response(200, json=gemini_body({"checkable": True, "claim": "ok"}))

    async with make_http_client(handler) as client:
        result = await extract_claim(client, get_settings(), "text")
    assert result.claim == "ok" and models == ["gemini-test", "gemini-backup-test"]


# Checks the primary's answer wins when it arrives before the hedge delay.
async def test_fast_primary_sends_no_backup():
    """No second request is made when the primary answers quickly."""
    models = []

    # Answers every request immediately.
    def handler(request):
        """Return a claim naming the model."""
        models.append(model_of(request))
        return httpx.Response(200, json=gemini_body({"checkable": True, "claim": "ok"}))

    async with make_http_client(handler) as client:
        await extract_claim(client, get_settings(), "text")
    assert models == ["gemini-test"]


# Checks both models failing still raises a single UpstreamError.
async def test_both_models_failing_raises():
    """The error from the last failure is raised."""
    async with make_http_client(lambda request: httpx.Response(500)) as client:
        with pytest.raises(UpstreamError):
            await extract_claim(client, get_settings(), "text")


# Checks no backup is used when it is the same model as the primary.
async def test_no_hedge_when_backup_equals_primary(monkeypatch):
    """GEMINI_FALLBACK_MODEL equal to the primary disables hedging."""
    monkeypatch.setenv("GEMINI_FALLBACK_MODEL", "gemini-test")
    get_settings.cache_clear()
    models = []

    # Fails every request.
    def handler(request):
        """Record the model and return 503."""
        models.append(model_of(request))
        return httpx.Response(503)

    async with make_http_client(handler) as client:
        with pytest.raises(UpstreamError):
            await extract_claim(client, get_settings(), "text")
    assert models == ["gemini-test"]
