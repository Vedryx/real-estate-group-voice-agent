"""Indian mobile phone validation, shared across verticals.

Factored out of tools/catalog.py once a second vertical (tools/catalog_clinic.py)
needed the same check — one source of truth for the format rule rather than
copy-pasted regexes drifting apart.
"""

from __future__ import annotations

import re

# Indian mobile numbers (DoT/TRAI National Numbering Plan): exactly 10 digits,
# first digit 6-9. No valid mobile number starts 0-5, so "1234567890" or a
# short/long digit run are both rejected, not just re-formatted.
_INDIAN_MOBILE_RE = re.compile(r"^[6-9]\d{9}$")


def normalize_indian_phone(phone: str) -> str | None:
    """Validate + normalize a caller-stated number to '+91XXXXXXXXXX'.

    Strips spaces/hyphens/parens and an optional '+91' / '91' / trunk '0'
    prefix before checking the underlying 10 digits. Returns None (never
    guesses or silently truncates) when the result isn't a valid Indian
    mobile number — the caller should be asked to repeat it instead.
    """
    digits = re.sub(r"\D", "", phone)
    if digits.startswith("91") and len(digits) == 12:
        digits = digits[2:]
    elif digits.startswith("0") and len(digits) == 11:
        digits = digits[1:]
    if not _INDIAN_MOBILE_RE.match(digits):
        return None
    return f"+91{digits}"
