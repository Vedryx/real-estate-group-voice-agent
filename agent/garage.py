"""Layered knowledge accessors for the auto-garage vertical (car service +
car rental).

Same three-layer shape as agent/canopy.py, agent/clinic.py and agent/salon.py
(Layer 0 working brief, Layer 1 facts, Layer 2 dummy commercial data), for an
inbound receptionist persona covering two departments: Service (repair/
maintenance) and Rental (self-drive/chauffeur car hire). The project itself
("Prime Auto Garage & Rentals") is fictional demo content — see
data/garage.json's _comment.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

_DEPARTMENT_ALIASES = {
    "SERVICE": "Service",
    "REPAIR": "Service",
    "MAINTENANCE": "Service",
    "SERVICING": "Service",
    "RENTAL": "Rental",
    "RENT": "Rental",
    "HIRE": "Rental",
}


def _normalize_department(department: str) -> str | None:
    key = " ".join(department.strip().upper().split())
    return _DEPARTMENT_ALIASES.get(key)


def render_working_brief(facts: dict[str, Any], commercial: dict[str, Any]) -> str:
    """Generate the Layer-0 working brief from the canonical JSON — same
    one-source-of-truth approach as agent/canopy.py's render_working_brief."""
    advisors_line = "; ".join(
        f'{a["name"]} ({a["role"]})' for a in facts["service_advisors"]
    )
    fleet_line = "; ".join(
        f'{f["category"]} (e.g. {", ".join(f["examples"])})' for f in facts["rental_fleet"]
    )
    fees_line = ", ".join(
        f'{f["service"]} {f["indicative_fee"]}' for f in commercial.get("fees", [])[:4]
    )
    lines = [
        f'# {facts["garage_name"]} — quick facts (answer these directly, no tool needed)',
        f'- {facts["garage_name"]}: {facts["garage_type"]}, at {facts["address"]}.',
        f'- Brands serviced: {", ".join(facts["brands_serviced"])}.',
        f'- Service team: {advisors_line}.',
        f'- Rental fleet: {fleet_line}.',
        f'- Timings: Service {facts["timings"]["service"]}; Rental pickup/drop {facts["timings"]["rental_pickup_drop"]}. '
        f'Closed {facts["timings"]["closed"]}.',
        f'- Prices (INDICATIVE — team confirms exact): {fees_line}.',
        f'- Rental documents: {facts["rental_terms"]["documents_required"]}',
        f'- Roadside/emergency: {facts["roadside_note"]}',
        f'- Repair estimates: {facts["estimate_note"]}',
    ]
    return "\n".join(lines)


@dataclass(frozen=True)
class GarageKnowledge:
    """In-memory snapshot of the three knowledge layers, loaded once."""

    facts: dict[str, Any]
    commercial: dict[str, Any]
    brief: str

    @classmethod
    def load(cls, data_dir: Path = DATA_DIR) -> "GarageKnowledge":
        facts = json.loads((data_dir / "garage.json").read_text(encoding="utf-8"))
        commercial = json.loads(
            (data_dir / "garage_commercial.json").read_text(encoding="utf-8")
        )
        brief = render_working_brief(facts, commercial)
        return cls(facts=facts, commercial=commercial, brief=brief)

    # ------------------------------------------------------------------ Layer 0
    def working_brief(self) -> str:
        return self.brief

    # ------------------------------------------------------------------ Layer 1
    def garage_name(self) -> str:
        return self.facts["garage_name"]

    def garage_type(self) -> str:
        return self.facts["garage_type"]

    def departments(self) -> list[str]:
        return list(self.facts["departments"])

    def brands_serviced(self) -> list[str]:
        return list(self.facts["brands_serviced"])

    def service_advisors(self) -> list[dict[str, Any]]:
        return [dict(a) for a in self.facts["service_advisors"]]

    def service_types(self) -> list[str]:
        return list(self.facts["service_types"])

    def rental_fleet(self) -> list[dict[str, Any]]:
        return [dict(f) for f in self.facts["rental_fleet"]]

    def rental_fleet_for(self, category: str) -> dict[str, Any] | None:
        for f in self.facts["rental_fleet"]:
            if f["category"].lower() == category.strip().lower():
                return dict(f)
        return None

    def rental_terms(self) -> dict[str, Any]:
        return dict(self.facts["rental_terms"])

    def timings(self) -> dict[str, Any]:
        return dict(self.facts["timings"])

    def location(self) -> dict[str, str]:
        return {"address": self.facts["address"]}

    def contact(self) -> dict[str, str]:
        return dict(self.facts["contact"])

    def roadside_note(self) -> str:
        return self.facts["roadside_note"]

    def estimate_note(self) -> str:
        return self.facts["estimate_note"]

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

    def deposit_note(self) -> str | None:
        return self.commercial.get("deposit_note")

    def payment_note(self) -> str | None:
        return self.commercial.get("payment_note")

    def commercial_disclaimer(self) -> str:
        return self.commercial.get("disclaimer", "")
