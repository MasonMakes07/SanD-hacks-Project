"""Tests for YouTube id parsing, clip windows and timestamp handling."""

import pytest

from app.video import (
    DEFAULT_CLIP_SECONDS,
    clip_window,
    format_timestamp,
    parse_timestamp,
    resolve_timestamp,
    timestamp_url,
    watch_url,
    youtube_video_id,
)

VIDEO_ID = "N-q2z4N_Rsk"


# Checks every common YouTube link form yields the video id.
@pytest.mark.parametrize(
    "url",
    [
        f"https://www.youtube.com/watch?v={VIDEO_ID}",
        f"https://www.youtube.com/watch?v={VIDEO_ID}&t=141s&list=PL1",
        f"https://m.youtube.com/watch?v={VIDEO_ID}",
        f"https://music.youtube.com/watch?v={VIDEO_ID}",
        f"https://youtu.be/{VIDEO_ID}?t=10",
        f"https://www.youtube.com/shorts/{VIDEO_ID}",
        f"https://www.youtube.com/live/{VIDEO_ID}?feature=share",
        f"https://www.youtube.com/embed/{VIDEO_ID}",
        f"https://www.youtube-nocookie.com/embed/{VIDEO_ID}",
    ],
)
def test_youtube_video_id_accepts_link_forms(url):
    """Watch, mobile, short-link, Shorts, live and embed links all parse."""
    assert youtube_video_id(url) == VIDEO_ID


# Checks look-alike hosts, bad ids and non-video pages are rejected.
@pytest.mark.parametrize(
    "url",
    [
        f"https://youtube.com.evil.example/watch?v={VIDEO_ID}",
        f"https://notyoutube.com/watch?v={VIDEO_ID}",
        "https://www.youtube.com/watch?v=short",
        "https://www.youtube.com/watch?v=has spaces!",
        "https://www.youtube.com/@channel",
        "https://www.youtube.com/results?search_query=dart",
        f"https://www.youtube.com/shorts/{VIDEO_ID}extra",
        "https://youtu.be/",
        "https://[::1",
    ],
)
def test_youtube_video_id_rejects_others(url):
    """Only real YouTube hosts with an 11-character id are accepted."""
    assert youtube_video_id(url) is None


# Checks the canonical and timestamped links.
def test_watch_and_timestamp_urls():
    """Links are canonical and the start time is whole seconds."""
    assert watch_url(VIDEO_ID) == f"https://www.youtube.com/watch?v={VIDEO_ID}"
    assert timestamp_url(VIDEO_ID, 141.9) == f"{watch_url(VIDEO_ID)}&t=141s"
    assert timestamp_url(VIDEO_ID, -5).endswith("&t=0s")


# Checks the clip windows chosen for different viewer positions.
@pytest.mark.parametrize(
    "current, duration, window",
    [
        (None, None, (0, DEFAULT_CLIP_SECONDS)),
        (141, None, (81, 141)),
        (10, None, (0, 10)),
        (0, None, (0, 1)),
        (60, 70, (0, 60)),
        (900, 50, (49, 50)),
        (None, 40, (0, 40)),
        (3600, 4000, (3540, 3600)),
    ],
)
def test_clip_window(current, duration, window):
    """Windows end shortly after the viewer and never start past the video end."""
    assert clip_window(current, duration) == window


def test_different_seconds_never_share_a_later_window():
    assert clip_window(136, None) == (76, 136)
    assert clip_window(149.5, None) == (89, 149)
    assert clip_window(141.9, None) == clip_window(141, None)
    assert clip_window(151, None) != clip_window(149, None)


# Checks timestamps format and parse in both directions.
@pytest.mark.parametrize(
    "seconds, text", [(0, "0:00"), (141, "2:21"), (3725, "1:02:05")]
)
def test_timestamp_round_trip(seconds, text):
    """format_timestamp and parse_timestamp are inverses."""
    assert format_timestamp(seconds) == text
    assert parse_timestamp(text) == seconds


# Checks invalid timestamps are rejected.
@pytest.mark.parametrize("text", ["", "2:61", "abc", "141", "1:2:3:4", "-1:00"])
def test_parse_timestamp_rejects_garbage(text):
    """Anything other than m:ss or h:mm:ss returns None."""
    assert parse_timestamp(text) is None


# Checks model timestamps are mapped onto the full video and the clip.
def test_resolve_timestamp():
    """Full-video times are kept; clip-relative ones are shifted; others dropped."""
    window = (90, 165)
    assert resolve_timestamp("2:21", window) == 141
    assert resolve_timestamp("0:51", window) == 141
    assert resolve_timestamp("5:00", window) is None
    assert resolve_timestamp("nonsense", window) is None
