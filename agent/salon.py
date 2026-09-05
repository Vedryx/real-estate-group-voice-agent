"""Layered knowledge accessors for the salon-appointment vertical.

Same three-layer shape as agent/canopy.py and agent/clinic.py (Layer 0
working brief, Layer 1 facts, Layer 2 dummy commercial data), for an inbound
appointment-booking receptionist persona. The project itself ("Aura Salon &
Spa") is fictional demo content — see data/salon.json's _comment.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

_DEPARTMENT_ALIASES = {
    "HAIR": "Hair",
    "HAIRDRESSING": "Hair",
    "SKIN": "Skin",
    "FACIAL": "Skin",
    "FACE": "Skin",
    "NAILS": "Nails",
    "NAIL": "Nails",
    "MANICURE": "Nails",
    "PEDICURE": "Nails",
    "MAKEUP": "Makeup",
    "MAKE UP": "Makeup",
    "BRIDAL": "Makeup",
}


def _normalize_department(department: str) -> str | None:
    key = " ".join(department.strip().upper().split())
    return _DEPARTMENT_ALIASES.get(key)


def render_working_brief(facts: dict[str, Any], commercial: dict[str, Any]) -> str:
    """Generate the Layer-0 working brief from the canonical JSON — same
    one-source-of-truth approach as agent/canopy.py's render_working_brief."""
    stylists_line = "; ".join(
        f'{s["name"]} ({s["department"]})' for s in facts["stylists"]
    )
    fees_line = ", ".join(
        f'{f["service"]} {f["indicative_fee"]}' for f in commercial.get("fees", [])[:4]
    )
    lines = [
        f'# {facts["salon_name"]} — quick facts (answer these directly, no tool needed)',
        f'- {facts["salon_name"]}: {facts["salon_type"]}, at {facts["address"]}.',
        f'- Stylists: {stylists_line}.',
        f'- Timings: {facts["timings"]["general"]}. Closed {facts["timings"]["closed"]}.',
        f'- Fees (INDICATIVE — front desk confirms exact): {fees_line}.',
        f'- Appointment policy: {facts["appointment_policy"]}',
        f'- Patch test: {facts["patch_test_note"]}',
        f'- Bridal: {facts["bridal_note"]}',
    ]
    return "\n".join(lines)


@dataclass(frozen=True)
class SalonKnowledge:
    """In-memory snapshot of the three knowledge layers, loaded once."""

    facts: dict[str, Any]
    commercial: dict[str, Any]
    brief: str

    @classmethod
    def load(cls, data_dir: Path = DATA_DIR) -> "SalonKnowledge":
        facts = json.loads((data_dir / "salon.json").read_text(encoding="utf-8"))
        commercial = json.loads(
            (data_dir / "salon_commercial.json").read_text(encoding="utf-8")
        )
        brief = render_working_brief(facts, commercial)
        return cls(facts=facts, commercial=commercial, brief=brief)

    # ------------------------------------------------------------------ Layer 0
    def working_brief(self) -> str:
        return self.brief

    # ------------------------------------------------------------------ Layer 1
    def salon_name(self) -> str:
        return self.facts["salon_name"]

    def salon_type(self) -> str:
        return self.facts["salon_type"]

    def departments(self) -> list[str]:
        return list(self.facts["departments"])

    def stylists(self) -> list[dict[str, Any]]:
        return [dict(s) for s in self.facts["stylists"]]

    def stylists_for(self, department: str) -> list[dict[str, Any]]:
        target = _normalize_department(department)
        return [dict(s) for s in self.facts["stylists"] if s["department"] == target]

    def services(self, department: str | None = None) -> dict[str, list[str]] | list[str]:
        services = self.facts["services"]
        if department is None:
            return {k: list(v) for k, v in services.items()}
        target = _normalize_department(department)
        return list(services.get(target, []))

    def timings(self) -> dict[str, Any]:
        return dict(self.facts["timings"])

    def location(self) -> dict[str, str]:
        return {"address": self.facts["address"]}

    def contact(self) -> dict[str, str]:
        return dict(self.facts["contact"])

    def appointment_policy(self) -> str:
        return self.facts["appointment_policy"]

    def patch_test_note(self) -> str:
        return self.facts["patch_test_note"]

    def results_disclaimer(self) -> str:
        return self.facts["results_disclaimer"]

    def bridal_note(self) -> str:
        return self.facts["bridal_note"]

    def membership_note(self) -> str:
        return self.facts["membership_note"]

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
