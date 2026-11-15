"""Detects which social media platform, if any, a page URL belongs to."""

from urllib.parse import urlsplit

SOCIAL_PLATFORMS = {
    "x.com": "X (Twitter)",
    "twitter.com": "X (Twitter)",
    "reddit.com": "Reddit",
    "threads.net": "Threads",
    "threads.com": "Threads",
    "facebook.com": "Facebook",
    "instagram.com": "Instagram",
    "tiktok.com": "TikTok",
    "bsky.app": "Bluesky",
    "youtube.com": "YouTube",
    "linkedin.com": "LinkedIn",
    "truthsocial.com": "Truth Social",
    "mastodon.social": "Mastodon",
}


# Returns the social platform name for a URL, or None for ordinary websites.
def detect_platform(url: str) -> str | None:
    """Match the URL's host (or any subdomain of it) against SOCIAL_PLATFORMS."""
    try:
        host = (urlsplit(url).hostname or "").lower().rstrip(".")
    except ValueError:
        return None
    for domain, platform_name in SOCIAL_PLATFORMS.items():
        if host == domain or host.endswith("." + domain):
            return platform_name
    return None
