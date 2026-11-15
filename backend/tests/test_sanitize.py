"""Tests for input cleaning, code detection and URL validation."""

import pytest

from app.models import InputRejectedError
from app.sanitize import (
    clean_text,
    looks_like_code,
    validate_claim_text,
    validate_page_url,
)

NORMAL_TEXTS = [
    "The unemployment rate fell to 3.9% in October, the Labor Department said.",
    'Senator Smith said, "We passed the bill 52-48 on Tuesday."',
    "Prices rose $4.50 per gallon; analysts expect a drop by 2027.",
    "Q&A: What the new law means for renters in San Diego",
    "Officials selected 12 sites from the 40 proposed, where testing begins.",
    "He said the plan would cut emissions by 50%—a claim experts dispute.",
    "The vote was 7 > 3 in favor, according to minutes.",
]

SOCIAL_POSTS = [
    "BREAKING: poll worker found a FOREIGN wifi signal in a tabulator!!! 🚨 #Vote",
    "$TSLA just announced they're recalling 2M cars -> stock down 12% today 📉",
    "My grandma is 101 and says the secret is whiskey every day <3 <3",
    "Taylor Swift & Travis Kelce pledged $250 MILLION?!?! 😱 @taylorswift13",
    r"lol no. water boils at 100C at sea level. source: any textbook ¯\_(ツ)_/¯",
    "Gas was $1.85 when he left office. Now it's $5+. Let that sink in. 🧵 1/7",
    "RT @user: 5G towers cause COVID — proof in thread 👇 => https://t.co/x",
    "Obama was born in Kenya. #Birther #Truth >>> mainstream media lies",
    "Breaking | Senate passes the bill | 52-48 | more soon >>>",
]

CODE_TEXTS = [
    "<script>alert(1)</script>",
    "Click <img src=x onerror=alert(1)>",
    "javascript:alert(document.cookie)",
    "'; DROP TABLE users; --",
    "admin' OR '1'='1",
    "SELECT * FROM users WHERE id = 1",
    "1 UNION SELECT password FROM accounts",
    "INSERT INTO claims VALUES (1)",
    "```python\nprint('hi')\n```",
    "function steal() { fetch('/x') }",
    "const f = () => { return 1 }",
    "eval(atob('ZXZpbA=='))",
    "from os import system",
    "def run():\n    pass",
    "{{7*7}} template injection",
    "$('body').html()",
    "rm -rf / --no-preserve-root",
    "if (you == believe) { share(); } // the vaccine has microchips",
    "<?php system($_GET['c']); ?>",
    "a{b}c;d<e>f=g$h`i|j\\k{l}m;n<o>p=q$r`s|t\\u{v}w;x<y>z",
]


# Checks that ordinary news sentences are never flagged as code.
@pytest.mark.parametrize("text", NORMAL_TEXTS)
def test_normal_text_is_not_code(text):
    """Normal article prose must pass the code filter."""
    assert not looks_like_code(text)
    assert validate_claim_text(text)


# Checks that real-world social media posts are never flagged as code.
@pytest.mark.parametrize("text", SOCIAL_POSTS)
def test_social_posts_are_not_code(text):
    """Emoji, $tickers, <3, >>>, ->, | and hashtags must pass the code filter."""
    assert not looks_like_code(text)
    assert validate_claim_text(text)


# Checks that code, markup and injection payloads are rejected.
@pytest.mark.parametrize("text", CODE_TEXTS)
def test_code_like_text_is_rejected(text):
    """Code-like input must raise InputRejectedError."""
    assert looks_like_code(text)
    with pytest.raises(InputRejectedError):
        validate_claim_text(text)


# Checks control characters and whitespace are normalized.
def test_clean_text_normalizes_whitespace_and_controls():
    """Control chars are removed and whitespace collapsed."""
    raw_text = "  Hello\x00\x07   world\t\t!\r\n\r\n\r\n\r\nNext  "
    assert clean_text(raw_text) == "Hello world !\n\nNext"


# Checks empty-after-cleaning text is rejected.
def test_whitespace_only_text_is_rejected():
    """Text that is only whitespace/control characters is rejected."""
    with pytest.raises(InputRejectedError):
        validate_claim_text(" \x00\t\n ")


# Checks valid http(s) URLs pass unchanged.
@pytest.mark.parametrize(
    "url",
    [
        "https://www.example.com/news/article?id=5#top",
        "http://localhost:8000/page",
        "https://sub.domain.co.uk/a/b/c",
    ],
)
def test_valid_urls_pass(url):
    """Well-formed http(s) URLs are returned as-is."""
    assert validate_page_url(url) == url


# Checks dangerous or malformed URLs are rejected.
@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "file:///etc/passwd",
        "chrome://settings",
        "ftp://example.com/x",
        "https://",
        "https://user:pass@example.com/",
        "https://exa mple.com/",
        "https://example.com/\x00",
        "not a url",
        "https://[::1",
    ],
)
def test_bad_urls_are_rejected(url):
    """Non-http(s), hostless, credentialed or malformed URLs raise."""
    with pytest.raises(InputRejectedError):
        validate_page_url(url)
