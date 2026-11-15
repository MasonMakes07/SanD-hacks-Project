"""Input sanitization: cleans text, validates URLs, rejects code-like input."""

import re
import unicodedata
from urllib.parse import urlsplit

from app.models import InputRejectedError

CONTROL_CHARACTERS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
HORIZONTAL_WHITESPACE = re.compile(r"[ \t ]+")
EXTRA_NEWLINES = re.compile(r"\n{3,}")

CODE_PATTERNS = re.compile(
    "|".join(
        [
            r"<\s*/?\s*(script|iframe|object|embed|style|svg|img|form|input)\b",
            r"<\?php",
            r"\bjavascript\s*:",
            r"\bdata\s*:\s*text/html",
            r"\bon(error|load|click|mouseover)\s*=",
            r"```",
            r"\$\(\s*[\w'\"]",
            r"\b(eval|exec)\s*\(",
            r"\bfunction\s*\w*\s*\([^)]*\)\s*\{",
            r"=>\s*\{",
            r"\b(import|from)\s+[\w.]+\s+import\b",
            r"^\s*(def|class)\s+\w+\s*[(:]",
            r"\b(select|delete)\b[^.]{0,80}\bfrom\b[^.]{0,80}(\bwhere\b|;)",
            r"\b(drop|truncate)\s+table\b",
            r"\binsert\s+into\b",
            r"\bunion\s+(all\s+)?select\b",
            r"'\s*or\s+'?1'?\s*=\s*'?1",
            r";\s*--",
            r"\{\{.*?\}\}",
            r"\b(rm\s+-rf|sudo\s+\w+|chmod\s+\d{3})",
        ]
    ),
    re.IGNORECASE | re.MULTILINE,
)
# <, >, $ and | are left out: social posts use them (">>>", "<3", "$TSLA", "a | b").
# Markup and shell payloads that need them are caught by CODE_PATTERNS instead.
CODE_SYMBOLS = frozenset("{};=`\\")
CODE_SYMBOL_RATIO_LIMIT = 0.04
MIN_LENGTH_FOR_RATIO_CHECK = 40


# Normalizes unicode, strips control characters and collapses whitespace.
def clean_text(raw_text: str) -> str:
    """Return a normalized copy of the text that is safe to process."""
    normalized_text = unicodedata.normalize("NFKC", raw_text)
    normalized_text = normalized_text.replace("\r\n", "\n").replace("\r", "\n")
    normalized_text = CONTROL_CHARACTERS.sub("", normalized_text)
    normalized_text = HORIZONTAL_WHITESPACE.sub(" ", normalized_text)
    normalized_text = EXTRA_NEWLINES.sub("\n\n", normalized_text)
    return normalized_text.strip()


# Detects text that looks like source code, markup or an injection payload.
def looks_like_code(text: str) -> bool:
    """Return True when text matches a code pattern or is symbol-dense."""
    if CODE_PATTERNS.search(text):
        return True
    if len(text) < MIN_LENGTH_FOR_RATIO_CHECK:
        return False
    symbol_count = sum(1 for character in text if character in CODE_SYMBOLS)
    return symbol_count / len(text) > CODE_SYMBOL_RATIO_LIMIT


# Validates that a page URL is a plain http(s) URL with a host.
def validate_page_url(raw_url: str) -> str:
    """Return the URL unchanged if it is a safe http(s) URL, else raise."""
    url = raw_url.strip()
    if any(character.isspace() for character in url) or CONTROL_CHARACTERS.search(
        url
    ):
        raise InputRejectedError("URL contains whitespace or control characters.")
    try:
        parts = urlsplit(url)
        hostname = parts.hostname
    except ValueError as error:
        raise InputRejectedError("URL could not be parsed.") from error
    if parts.scheme not in {"http", "https"}:
        raise InputRejectedError("URL must use http or https.")
    if not hostname:
        raise InputRejectedError("URL must include a host.")
    if parts.username or parts.password:
        raise InputRejectedError("URL must not contain credentials.")
    return url


# Cleans the claim text and rejects empty or code-like input.
def validate_claim_text(raw_text: str) -> str:
    """Return cleaned text, raising InputRejectedError if it is unusable."""
    text = clean_text(raw_text)
    if not text:
        raise InputRejectedError("Text is empty after cleaning.")
    if looks_like_code(text):
        raise InputRejectedError("Text looks like code and was rejected.")
    return text
