"""Read-only project/proximity data access and append-only lead logging.

Every fact the agent speaks (price, amenity, possession date, metro
proximity) must come from here, never from the LLM's own knowledge —
see plan.md §6 and §7 rule 1.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
LEADS_LOG_PATH = DATA_DIR / "leads.jsonl"

VALID_OUTCOMES = {
    "qualified_lead",
    "callback_requested",
    "site_visit_requested",
    "not_serviceable_area",
    "info_only_no_lead",
    "escalation_needed",
    "opted_out",
    "spam_or_abandoned",
}


@dataclass(frozen=True)
class DataStore:
    """In-memory snapshot of the seed data, loaded once at agent startup."""

    projects: list[dict[str, Any]]
    service_interest_areas: dict[str, Any]
    workplace_proximity: dict[str, Any]

    @classmethod
    def load(cls, data_dir: Path = DATA_DIR) -> "DataStore":
        projects = json.loads((data_dir / "projects.json").read_text())
        areas = json.loads((data_dir / "service_interest_areas.json").read_text())
        proximity = json.loads((data_dir / "workplace_proximity.json").read_text())
        return cls(projects=projects, service_interest_areas=areas, workplace_proximity=proximity)

    def get_project(self, project_id: str) -> dict[str, Any] | None:
        for project in self.projects:
            if project["id"] == project_id:
                return project
        return None

    def served_cities(self) -> set[str]:
        return {p["city"] for p in self.projects}

    def find_project_by_fuzzy_name(self, name: str) -> list[dict[str, Any]]:
        """Loose substring match on project name/developer, for ambiguous references."""
        needle = name.strip().lower()
        if not needle:
            return []
        return [
            p
            for p in self.projects
            if needle in p["name"].lower() or needle in p["developer"].lower()
        ]


def log_lead(
    *,
    caller_phone: str | None,
    outcome: str,
    name: str | None = None,
    preferred_language: str | None = None,
    requested_city: str | None = None,
    requested_locality: str | None = None,
    bhk_preference: str | None = None,
    budget_range_inr_lakh: tuple[float, float] | None = None,
    property_type_requested: str = "unspecified",
    projects_discussed: list[str] | None = None,
    workplace_area: str | None = None,
    notes: str = "",
    consent_to_be_contacted: bool = True,
    log_path: Path | None = None,
) -> dict[str, Any]:
    """Append one lead record to the local JSONL store (plan.md §4.5).

    Deliberately a single function so swapping in a CRM/webhook later is a
    one-function change (plan.md §12).
    """
    if outcome not in VALID_OUTCOMES:
        raise ValueError(f"invalid outcome {outcome!r}; must be one of {sorted(VALID_OUTCOMES)}")

    # Resolved at call time (not as a bound default) so tests can
    # monkeypatch module-level LEADS_LOG_PATH to isolate their writes.
    if log_path is None:
        log_path = LEADS_LOG_PATH

    record = {
        "lead_id": str(uuid.uuid4()),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "caller_phone": caller_phone,
        "name": name,
        "preferred_language": preferred_language,
        "requested_city": requested_city,
        "requested_locality": requested_locality,
        "bhk_preference": bhk_preference,
        "budget_range_inr_lakh": list(budget_range_inr_lakh) if budget_range_inr_lakh else None,
        "property_type_requested": property_type_requested,
        "projects_discussed": projects_discussed or [],
        "workplace_area": workplace_area,
        "outcome": outcome,
        "notes": notes,
        "consent_to_be_contacted": consent_to_be_contacted,
    }

    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")

    return record
