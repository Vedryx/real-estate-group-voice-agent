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
    notes: str = "",
    consent_to_be_contacted: bool = True,
    log_path: Path | None = None,
) -> dict[str, Any]:
    """Append one lead record to the local JSONL store.

    Single-project (The Canopy) schema — no city/BHK/developer/inventory
    fields. Deliberately one function so a CRM/webhook swap is contained here.
    """
    if outcome not in VALID_OUTCOMES:
        raise ValueError(
            f"invalid outcome {outcome!r}; must be one of {sorted(VALID_OUTCOMES)}"
        )

    # Resolved at call time (not as a bound default) so tests can monkeypatch
    # module-level LEADS_LOG_PATH to isolate their writes.
    if log_path is None:
        log_path = LEADS_LOG_PATH

    record = {
        "lead_id": str(uuid.uuid4()),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "project": "The Canopy",
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
        "outcome": outcome,
        "notes": notes,
        "consent_to_be_contacted": consent_to_be_contacted,
    }

    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")

    return record
