"""Layered knowledge accessors for the clinic-appointment vertical.

Same three-layer shape as agent/canopy.py (Layer 0 working brief, Layer 1
facts, Layer 2 dummy commercial data), and same is_dummy convention, but for
an inbound appointment-booking persona instead of an outbound sales one. The
project itself ("Wellness Point Clinic") is fictional demo content — see
data/clinic.json's _comment.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

_DEPARTMENT_ALIASES = {
    "GENERAL": "General Medicine",
    "GENERAL MEDICINE": "General Medicine",
    "GENERAL PHYSICIAN": "General Medicine",
    "GP": "General Medicine",
    "DENTAL": "Dental",
    "DENTIST": "Dental",
    "TEETH": "Dental",
}


def _normalize_department(department: str) -> str | None:
    key = " ".join(department.strip().upper().split())
    return _DEPARTMENT_ALIASES.get(key)


def render_working_brief(facts: dict[str, Any], commercial: dict[str, Any]) -> str:
    """Generate the Layer-0 working brief from the canonical JSON — same
    one-source-of-truth approach as agent/canopy.py's render_working_brief."""
    doctors_line = "; ".join(
        f'{d["name"]} ({d["department"]}, {d["qualification"]})' for d in facts["doctors"]
    )
    fees_line = ", ".join(
        f'{f["service"]} {f["indicative_fee"]}' for f in commercial.get("fees", [])[:4]
    )
    lines = [
        f'# {facts["clinic_name"]} — quick facts (answer these directly, no tool needed)',
        f'- {facts["clinic_name"]}: {facts["clinic_type"]}, at {facts["address"]}.',
        f'- Doctors: {doctors_line}.',
        f'- Timings: {facts["timings"]["General Medicine"]} (General Medicine); '
        f'{facts["timings"]["Dental"]} (Dental). Closed {facts["timings"]["closed"]}.',
        f'- Fees (INDICATIVE — front desk confirms exact): {fees_line}.',
        f'- Appointment policy: {facts["appointment_policy"]}',
        f'- Emergency: {facts["emergency_note"]}',
        f'- Insurance: {facts["insurance_note"]}',
    ]
    return "\n".join(lines)


@dataclass(frozen=True)
class ClinicKnowledge:
    """In-memory snapshot of the three knowledge layers, loaded once."""

    facts: dict[str, Any]
    commercial: dict[str, Any]
    brief: str

    @classmethod
    def load(cls, data_dir: Path = DATA_DIR) -> "ClinicKnowledge":
        facts = json.loads((data_dir / "clinic.json").read_text(encoding="utf-8"))
        commercial = json.loads(
            (data_dir / "clinic_commercial.json").read_text(encoding="utf-8")
        )
        brief = render_working_brief(facts, commercial)
        return cls(facts=facts, commercial=commercial, brief=brief)

    # ------------------------------------------------------------------ Layer 0
    def working_brief(self) -> str:
        return self.brief

    # ------------------------------------------------------------------ Layer 1
    def clinic_name(self) -> str:
        return self.facts["clinic_name"]

    def clinic_type(self) -> str:
        return self.facts["clinic_type"]

    def departments(self) -> list[str]:
        return list(self.facts["departments"])

    def doctors(self) -> list[dict[str, Any]]:
        return [dict(d) for d in self.facts["doctors"]]

    def doctors_for(self, department: str) -> list[dict[str, Any]]:
        target = _normalize_department(department)
        return [dict(d) for d in self.facts["doctors"] if d["department"] == target]

    def services(self, department: str | None = None) -> dict[str, list[str]] | list[str]:
        services = self.facts["services"]
        if department is None:
            return {k: list(v) for k, v in services.items()}
        target = _normalize_department(department)
        return list(services.get(target, []))

    def timings(self, department: str | None = None) -> dict[str, Any] | str:
        timings = self.facts["timings"]
        if department is None:
            return dict(timings)
        target = _normalize_department(department)
        return timings.get(target, "not confirmed")

    def location(self) -> dict[str, str]:
        return {"address": self.facts["address"]}

    def contact(self) -> dict[str, str]:
        return dict(self.facts["contact"])

    def appointment_policy(self) -> str:
        return self.facts["appointment_policy"]

    def emergency_note(self) -> str:
        return self.facts["emergency_note"]

    def insurance_note(self) -> str:
        return self.facts["insurance_note"]

    def new_patient_note(self) -> str:
        return self.facts["new_patient_note"]

    def registration_number(self) -> str:
        return self.facts["registration_number"]

    def media_caveat(self) -> str:
        return self.facts["media_caveat"]

    def legal_caveat(self) -> str:
        return self.facts["legal_caveat"]

    # ------------------------------------------------------------------ Layer 2
    def commercial_is_dummy(self) -> bool:
        return bool(self.commercial.get("is_dummy")) or (
            self.commercial.get("source") == "DUMMY_PLACEHOLDER"
        )

    def fees(self, department: str | None = None) -> list[dict[str, Any]]:
        rows = [dict(f) for f in self.commercial.get("fees", [])]
        if department is None:
            return rows
        target = _normalize_department(department)
        return [f for f in rows if f["department"] == target]

    def fee_basis(self) -> str | None:
        return self.commercial.get("fee_basis")

    def payment_note(self) -> str | None:
        return self.commercial.get("payment_note")

    def commercial_disclaimer(self) -> str:
        return self.commercial.get("disclaimer", "")
