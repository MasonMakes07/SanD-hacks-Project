"""Tests for social platform detection and how social posts flow through the API."""

import json

import httpx
import pytest

from app.analysis import _strip_social_noise, run_investigation
from app.config import get_settings
from app.models import AnalysisInput, AnalyzeRequest
from app.platforms import detect_platform
from app.Translator import to_analysis_input
from tests.conftest import gemini_body, make_http_client


# Checks known social hosts, including subdomains, map to platform names.
@pytest.mark.parametrize(
    "url, platform_name",
    [
        ("https://x.com/user/status/123", "X (Twitter)"),
        ("https://mobile.twitter.com/user/status/1", "X (Twitter)"),
        ("https://old.reddit.com/r/news/comments/abc/", "Reddit"),
        ("https://www.facebook.com/post/1", "Facebook"),
        ("https://www.threads.net/@user/post/1", "Threads"),
        ("https://bsky.app/profile/a/post/b", "Bluesky"),
        ("https://www.tiktok.com/@a/video/1", "TikTok"),
    ],
)
def test_detects_social_platforms(url, platform_name):
    """Social URLs return their platform name."""
    assert detect_platform(url) == platform_name


# Checks news sites and look-alike domains are not treated as social.
@pytest.mark.parametrize(
    "url",
    [
        "https://www.nytimes.com/2026/11/14/news.html",
        "https://notx.com/status/1",
        "https://x.com.evil.example/status/1",
        "https://reddit.com.example.org/",
        "https://[::1",
    ],
)
def test_non_social_urls_return_none(url):
    """Ordinary and spoofed hosts return None."""
    assert detect_platform(url) is None


# Checks the Translator attaches the platform to sanitized input.
def test_translator_sets_platform():
    """A post URL produces AnalysisInput.platform; a news URL leaves it None."""
    post = to_analysis_input(
        AnalyzeRequest(
            text="Water boils at 90C at sea level.",
            url="https://x.com/u/status/9",
            mode="manual",
        )
    )
    article = to_analysis_input(
        AnalyzeRequest(
            text="Water boils at 90C at sea level.",
            url="https://news.example/a",
            mode="manual",
        )
    )
    assert post.platform == "X (Twitter)" and article.platform is None


# Checks links, mentions, hashtags, RT and thread counters are stripped.
def test_strip_social_noise():
    """Decoration is removed and the factual sentence is kept."""
    raw_post = (
        "RT @someone: Michigan poll worker found a foreign wifi signal in a "
        "tabulator! #StopTheSteal @gop https://t.co/abc 1/7"
    )
    assert _strip_social_noise(raw_post) == (
        "Michigan poll worker found a foreign wifi signal in a tabulator!"
    )


# Runs one investigation where Gemini extraction is down, capturing prompts.
async def run_with_capture(url, text, extraction_fails):
    """Return the result and the list of Gemini prompt texts sent."""
    prompts = []

    # Records Gemini prompts; fails extraction when asked to.
    def handler(request: httpx.Request) -> httpx.Response:
        """Return canned upstream responses."""
        if request.url.host == "factchecktools.googleapis.com":
            return httpx.Response(200, json={})
        prompt_text = json.loads(request.content)["contents"][0]["parts"][0]["text"]
        prompts.append(prompt_text)
        if extraction_fails:
            return httpx.Response(503)
        return httpx.Response(
            200, json=gemini_body({"checkable": True, "claim": "A claim."})
        )

    analysis_input = to_analysis_input(
        AnalyzeRequest(text=text, url=url, mode="manual")
    )
    async with make_http_client(handler) as client:
        result = await run_investigation(client, get_settings(), analysis_input)
    return result, prompts


# Checks Gemini is told when the text is a social media post.
async def test_social_post_prompt_names_platform():
    """Post URLs add a <source> tag after the page text; news URLs do not."""
    _, post_prompts = await run_with_capture(
        "https://x.com/u/status/1", "The moon is made of cheese.", False
    )
    _, news_prompts = await run_with_capture(
        "https://news.example/a", "The moon is made of cheese.", False
    )
    assert post_prompts[0].startswith("<page_text>")
    expected_tag = "<source>A social media post on X (Twitter)</source>"
    assert post_prompts[0].endswith(expected_tag)
    assert "<source>" not in news_prompts[0]


# Checks the fallback claim for a post has its decoration removed.
async def test_social_fallback_claim_is_cleaned():
    """With Gemini down, the post minus hashtags and links becomes the claim."""
    result, _ = await run_with_capture(
        "https://x.com/u/status/1",
        "Obama was born in Kenya. #Birther #Truth >>> https://t.co/x",
        True,
    )
    assert result.claim == "Obama was born in Kenya. >>>"


# Checks non-social text keeps hashtags in the fallback claim.
async def test_news_fallback_claim_is_unchanged():
    """Ordinary pages are not stripped of # or @ text."""
    analysis_input = AnalysisInput(
        text="The #1 seed lost to @home rivals.",
        url="https://n.example/",
        mode="manual",
    )
    assert analysis_input.platform is None
    result, _ = await run_with_capture(
        "https://n.example/", "The #1 seed lost to @home rivals.", True
    )
    assert result.claim == "The #1 seed lost to @home rivals."
