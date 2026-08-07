"""Resolve a caller's spoken relative date/time to an unambiguous ISO string.

The caller says "kal 4 baje" / "aaj shaam" / "Saturday" — fine for a human, but
ambiguous in the CRM once the call date passes (E2). We store BOTH the raw text
and a best-effort resolved ISO timestamp in Asia/Kolkata. Best-effort: if the
phrase can't be parsed, iso is None and the raw text is always kept.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))
IST_NAME = "Asia/Kolkata"

_WEEKDAYS = {
    "monday": 0, "somvar": 0, "somwar": 0,
    "tuesday": 1, "mangalvar": 1, "mangalwar": 1,
    "wednesday": 2, "budhvar": 2, "budhwar": 2,
    "thursday": 3, "guruvar": 3, "guruwar": 3, "brihaspativar": 3,
    "friday": 4, "shukravar": 4, "shukrawar": 4,
    "saturday": 5, "shanivar": 5, "shaniwar": 5,
    "sunday": 6, "ravivar": 6, "raviwar": 6, "itwar": 6,
}


def resolve_datetime(raw: str, now: datetime | None = None) -> str | None:
    """Best-effort resolve raw Hinglish/English date+time to an IST ISO string.

    Returns None when the day can't be determined (the caller-facing tool always
    keeps the raw text alongside). `now` is injectable for testing.
    """
    if not raw or not raw.strip():
        return None
    now = now or datetime.now(IST)
    t = raw.lower()

    # ---- day
    day: datetime | None = None
    if any(k in t for k in ("parso", "day after")):
        day = now + timedelta(days=2)
    elif any(k in t for k in ("kal", "tomorrow")):
        day = now + timedelta(days=1)
    elif any(k in t for k in ("aaj", "today")):
        day = now
    else:
        for name, wd in _WEEKDAYS.items():
            if name in t:
                delta = (wd - now.weekday()) % 7 or 7  # next occurrence, not today
                day = now + timedelta(days=delta)
                break
    if day is None:
        return None

    # ---- part of day (fallback when no explicit hour)
    part = None
    if any(k in t for k in ("subah", "morning")):
        part = "morning"
    elif any(k in t for k in ("dopahar", "afternoon", "noon")):
        part = "afternoon"
    elif any(k in t for k in ("shaam", "sham", "evening")):
        part = "evening"
    elif any(k in t for k in ("raat", "night")):
        part = "night"

    # ---- explicit hour, e.g. "4 baje", "4pm", "6:30"
    hour: int | None = None
    minute = 0
    m = re.search(r"\b(\d{1,2})(?::(\d{2}))?\s*(baje|bje|am|pm|a\.m\.|p\.m\.)?", t)
    if m:
        hour = int(m.group(1))
        minute = int(m.group(2) or 0)
        is_pm = ("pm" in t) or ("p.m" in t) or part in ("afternoon", "evening", "night")
        is_am = ("am" in t) or ("a.m" in t) or part == "morning"
        if is_pm and hour < 12:
            hour += 12
        elif is_am and hour == 12:
            hour = 0
        elif not is_pm and not is_am and 1 <= hour <= 7:
            hour += 12  # a bare "4 baje" for a site visit is almost always afternoon
    elif part:
        hour = {"morning": 10, "afternoon": 14, "evening": 18, "night": 20}[part]

    if hour is None:
        resolved = day.replace(hour=11, minute=0, second=0, microsecond=0)
    else:
        resolved = day.replace(hour=hour % 24, minute=minute, second=0, microsecond=0)
    return resolved.astimezone(IST).isoformat()
