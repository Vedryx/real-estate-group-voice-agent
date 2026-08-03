"""Per-call session state (AgentSession userdata).

Tracks what's already been collected from the caller so the agent
doesn't re-ask (plan.md §8), and carries the read-only DataStore.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from agent.data_store import DataStore


@dataclass
class CallUserdata:
    data_store: DataStore

    caller_phone: str | None = None
    caller_name: str | None = None
    preferred_language: str | None = None

    requested_city: str | None = None
    requested_locality: str | None = None
    bhk_preference: str | None = None
    budget_min_lakh: float | None = None
    budget_max_lakh: float | None = None
    property_type_requested: str = "unspecified"
    workplace_area: str | None = None

    projects_discussed: list[str] = field(default_factory=list)
    lead_logged: bool = False

    def note_project_discussed(self, project_id: str) -> None:
        if project_id not in self.projects_discussed:
            self.projects_discussed.append(project_id)
