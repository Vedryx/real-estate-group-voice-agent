"""Append-only lead logging for the Canopy voice agent.

The legacy multi-project ``DataStore`` was removed in the Canopy migration —
project knowledge now lives in ``agent/canopy.py`` (three layered sources).
This module is just the lead sink, kept as a single function so swapping in a
CRM/webhook later is a one-function change.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
LEADS_LOG_PATH = DATA_DIR / "leads.jsonl"

_JSON_SCALARS = (str, int, float, bool, type(None))


def _assert_json_safe(value: Any, path: str = "record") -> None:
    """Reject a malformed lead value instead of silently stringifying it (E5).

    Replaces the old json.dumps(default=str), which would persist garbage like
    "<MagicMock ...>". A non-JSON value raises here; the tool's _safe_log_lead
    catches it and reports logged=False rather than confirming a bad save.
    """
    if isinstance(value, bool) or isinstance(value, _JSON_SCALARS):
        return
    if isinstance(value, (list, tuple)):
        for i, v in enumerate(value):
            _assert_json_safe(v, f"{path}[{i}]")
        return
    if isinstance(value, dict):
        for k, v in value.items():
            if not isinstance(k, str):
                raise TypeError(f"{path}: non-string key {k!r}")
            _assert_json_safe(v, f"{path}.{k}")
        return
    raise TypeError(
        f"{path}: value of type {type(value).__name__} is not JSON-serializable ({value!r})"
    )


VALID_OUTCOMES = {
    "qualified_lead",
    "callback_requested",
    "site_visit_requested",
    "nurture_lead",
    "not_interested",
    "already_purchased",
    "accidental_click",
    "wrong_number",
    "info_only_no_lead",
    "escalation_needed",
    "opted_out",
    "spam_or_abandoned",
}


def log_lead(
    *,
    caller_phone: str | None,
    outcome: str,
    name: str | None = None,
    preferred_language: str | None = None,
    config_interest: str | None = None,
    purchase_timeline: str = "unknown",
    purchase_timeline_asked: bool = False,
    purchase_purpose: str = "unknown",
    source_channel: str | None = None,
    source_campaign: str | None = None,
    source_enquiry_id: str | None = None,
    consent_reference: str | None = None,
    interest_status: str = "unknown",
    next_step: str = "none",
    closing_attempted: bool = False,
    qualification_status: str = "incomplete",
    lead_temperature: str = "unscored",
    qualification_reasons: list[str] | None = None,
    preferred_datetime_raw: str | None = None,
    preferred_datetime_iso: str | None = None,
    timezone_name: str | None = None,
    notes: str = "",
    consent_to_be_contacted: bool = True,
    log_path: Path | None = None,
    project: str = "The Canopy",
    valid_outcomes: frozenset[str] | None = None,
) -> dict[str, Any]:
    """Append one lead record to the local JSONL store.

    One shared JSONL sink across verticals — deliberately one function so a
    CRM/webhook swap is contained here. `project` tags which vertical/persona
    a record came from (defaults to the original Canopy behavior, so existing
    callers are unaffected). Field names (e.g. `config_interest`) are kept
    generic on purpose: a non-real-estate vertical (e.g. tools/catalog_clinic.py)
    reuses them for its own equivalent concept (department interest) rather
    than growing the schema per vertical. `valid_outcomes` lets a sibling
    vertical validate against its own outcome vocabulary instead of the
    real-estate one below.
    """
    outcomes = valid_outcomes if valid_outcomes is not None else VALID_OUTCOMES
    if outcome not in outcomes:
        raise ValueError(f"invalid outcome {outcome!r}; must be one of {sorted(outcomes)}")

    # Resolved at call time (not as a bound default) so tests can monkeypatch
    # module-level LEADS_LOG_PATH to isolate their writes.
    if log_path is None:
        log_path = LEADS_LOG_PATH

    record = {
        "lead_id": str(uuid.uuid4()),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "project": project,
        "caller_phone": caller_phone,
        "name": name,
        "preferred_language": preferred_language,
        "config_interest": config_interest,
        "purchase_timeline": purchase_timeline,
        "purchase_timeline_asked": purchase_timeline_asked,
        "purchase_purpose": purchase_purpose,
        "source_channel": source_channel,
        "source_campaign": source_campaign,
        "source_enquiry_id": source_enquiry_id,
        "consent_reference": consent_reference,
        "interest_status": interest_status,
        "next_step": next_step,
        "closing_attempted": closing_attempted,
        "qualification_status": qualification_status,
        "lead_temperature": lead_temperature,
        "qualification_reasons": qualification_reasons or [],
        # E2: a visit/callback time is stored both as the caller said it and as
        # a resolved ISO timestamp, so it stays unambiguous in the CRM later.
        "preferred_datetime_raw": preferred_datetime_raw,
        "preferred_datetime_iso": preferred_datetime_iso,
        "timezone": timezone_name,
        "outcome": outcome,
        "notes": notes,
        "consent_to_be_contacted": consent_to_be_contacted,
    }

    # E5: validate before writing — reject malformed values (e.g. a mock that
    # leaked in) instead of stringifying them. Raises TypeError, which the tool's
    # _safe_log_lead catches and turns into logged=False (no bad "saved" claim).
    _assert_json_safe(record)

    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")

    return record
