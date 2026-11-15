"""YouTube helpers: video ids, clip windows around the viewer's position, timestamps."""

import math
import re
from urllib.parse import parse_qs, urlsplit

YOUTUBE_HOSTS = ("youtube.com", "youtube-nocookie.com")
SHORT_LINK_HOST = "youtu.be"
VIDEO_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{11}$")
PATH_ID_PATTERN = re.compile(
    r"^/(?:shorts|live|embed|v)/([A-Za-z0-9_-]{11})(?:[/?#]|$)"
)
TIMESTAMP_PATTERN = re.compile(r"^(?:(\d{1,2}):)?(\d{1,3}):([0-5]\d)$")

# The clip Gemini watches: what the viewer just saw plus a little after it.
SECONDS_BEFORE = 60
SECONDS_AFTER = 15
DEFAULT_CLIP_SECONDS = 90
# Positions are rounded to this bucket so nearby pauses share one cached result.
BUCKET_SECONDS = 15


# Returns the 11-character YouTube video id in a URL, or None.
def youtube_video_id(url: str) -> str | None:
    """Accept watch, shorts, live, embed and youtu.be links; reject anything else."""
    try:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower().rstrip(".")
    except ValueError:
        return None
    if host == SHORT_LINK_HOST:
        candidate = parts.path.strip("/").split("/")[0]
    elif any(host == domain or host.endswith("." + domain) for domain in YOUTUBE_HOSTS):
        path_match = PATH_ID_PATTERN.match(parts.path)
        if path_match:
            candidate = path_match.group(1)
        elif parts.path == "/watch":
            candidate = (parse_qs(parts.query).get("v") or [""])[0]
        else:
            return None
    else:
        return None
    return candidate if VIDEO_ID_PATTERN.match(candidate) else None


# Builds the canonical watch URL that Gemini is given.
def watch_url(video_id: str) -> str:
    """Return https://www.youtube.com/watch?v=<id>."""
    return f"https://www.youtube.com/watch?v={video_id}"


# Builds a link that opens the video at a given second.
def timestamp_url(video_id: str, seconds: int) -> str:
    """Return the watch URL with a &t=<seconds>s start time."""
    return f"{watch_url(video_id)}&t={max(0, int(seconds))}s"


# Picks the clip (start, end) in seconds around where the viewer paused.
def clip_window(
    current_seconds: float | None, duration_seconds: float | None
) -> tuple[int, int]:
    """Return a bucketed window ending a little after the viewer's position."""
    if current_seconds is None:
        start, end = 0, DEFAULT_CLIP_SECONDS
    else:
        position = math.ceil(current_seconds / BUCKET_SECONDS) * BUCKET_SECONDS
        start, end = max(0, position - SECONDS_BEFORE), position + SECONDS_AFTER
    if duration_seconds:
        length = math.ceil(duration_seconds)
        end = min(end, length)
        start = min(start, max(0, length - DEFAULT_CLIP_SECONDS))
    return start, max(end, start + 1)


# Formats seconds as m:ss or h:mm:ss.
def format_timestamp(seconds: int) -> str:
    """Return a human-readable timestamp for a position in the video."""
    hours, remainder = divmod(max(0, int(seconds)), 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


# Parses m:ss or h:mm:ss into seconds, or returns None for anything else.
def parse_timestamp(text: str) -> int | None:
    """Return the timestamp in seconds, or None if it is not m:ss / h:mm:ss."""
    match = TIMESTAMP_PATTERN.match((text or "").strip())
    if not match:
        return None
    hours, minutes, seconds = (int(group or 0) for group in match.groups())
    return hours * 3600 + minutes * 60 + seconds


# Converts the model's timestamp into a full-video position inside the clip.
def resolve_timestamp(text: str, window: tuple[int, int]) -> int | None:
    """Accept full-video times in the window; shift clip-relative times by start."""
    seconds = parse_timestamp(text)
    if seconds is None:
        return None
    start, end = window
    if start <= seconds <= end:
        return seconds
    if start + seconds <= end:
        return start + seconds
    return None
