"""Publication dates: accept only absolute, unambiguous dates. Anything else is None."""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone

_FORMATS = (
    "%Y-%m-%d",
    "%b %d, %Y",   # Jan 15, 2026
    "%B %d, %Y",   # January 15, 2026
    "%d %b %Y",    # 15 Jan 2026
    "%d %B %Y",    # 15 January 2026
)
_ISO_DATETIME = re.compile(
    r"^(\d{4}-\d{2}-\d{2})[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:\d{2})?$"
)
EARLIEST_PLAUSIBLE = date(1990, 1, 1)


def parse_reliable_date(value: object, *, today: date | None = None) -> date | None:
    """Return a date only for an absolute date string, else None.

    Rejected on purpose: relative text ("3 days ago"), year or month only,
    numeric forms like 03/04/2026 whose day/month order is ambiguous, and
    dates in the future or before 1990.
    """
    if not isinstance(value, str):
        return None
    text = " ".join(value.split()).rstrip(".")
    if not text or len(text) > 40:
        return None
    m = _ISO_DATETIME.match(text)
    if m:
        text = m.group(1)
    parsed: date | None = None
    for fmt in _FORMATS:
        try:
            parsed = datetime.strptime(text, fmt).date()
            break
        except ValueError:
            continue
    if parsed is None:
        return None
    today = today or datetime.now(timezone.utc).date()
    if parsed < EARLIEST_PLAUSIBLE or parsed > today + timedelta(days=1):
        return None
    return parsed
