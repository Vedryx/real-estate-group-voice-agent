"""Layered knowledge accessors for The Canopy voice agent.

Three read-only layers, loaded once at startup (see
documents/canopy-voice-agent-migration-plan.md §3):

  Layer 0 — working-memory brief (data/canopy_brief.md)
            High-level facts + FAQ pack, injected into the system prompt
            before the call so the common questions never hit a tool.
  Layer 1 — project facts (data/canopy.json)
            Verified brochure facts (configs, RERA-carpet areas, amenities,
            specs, RERA number, location). The retrieval layer.
  Layer 2 — commercial data (data/canopy_commercial.json)
            DUMMY indicative price + possession + payment skeleton. Every
            read carries the dummy/indicative flag so a caller can never be
            told a final figure.

This module only *reads* the three data files authored in Phase 0. Nothing
speaks these yet — the tools (Phase 2) and persona (Phase 4) consume it. It
intentionally does not touch the legacy multi-project ``data_store.DataStore``;
that is removed in Phase 1's consumer rewrite once state/tools/worker no longer
import it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
FACTS_PATH = DATA_DIR / "canopy.json"
COMMERCIAL_PATH = DATA_DIR / "canopy_commercial.json"
BRIEF_PATH = DATA_DIR / "canopy_brief.md"


def _normalize_config(config: str) -> str:
    """Fold '2 BHK' / '2bhk' / '2 bhk' / '2' to a canonical '2 BHK'."""
    digits = "".join(ch for ch in config if ch.isdigit())
    return f"{digits} BHK" if digits else config.strip().upper()


@dataclass(frozen=True)
class CanopyKnowledge:
    """In-memory snapshot of the three knowledge layers, loaded once."""

    facts: dict[str, Any]
    commercial: dict[str, Any]
    brief: str

    @classmethod
    def load(cls, data_dir: Path = DATA_DIR) -> "CanopyKnowledge":
        facts = json.loads((data_dir / "canopy.json").read_text(encoding="utf-8"))
        commercial = json.loads(
            (data_dir / "canopy_commercial.json").read_text(encoding="utf-8")
        )
        brief = (data_dir / "canopy_brief.md").read_text(encoding="utf-8")
        return cls(facts=facts, commercial=commercial, brief=brief)

    # ------------------------------------------------------------------ Layer 0
    def working_brief(self) -> str:
        """The pre-call working memory (high-level facts + FAQ pack)."""
        return self.brief

    # ------------------------------------------------------------------ Layer 1
    def project_name(self) -> str:
        return self.facts["project"]

    def developer(self) -> str:
        return self.facts["developer"]

    def township(self) -> str:
        return self.facts["township"]

    def configs(self) -> list[str]:
        return list(self.facts["configs"])

    def unit_types(self) -> list[dict[str, Any]]:
        return [dict(u) for u in self.facts["unit_types"]]

    def unit_types_for(self, config: str) -> list[dict[str, Any]]:
        target = _normalize_config(config)
        return [
            dict(u)
            for u in self.facts["unit_types"]
            if _normalize_config(u["config"]) == target
        ]

    def unit_type(self, unit_id: str) -> dict[str, Any] | None:
        for u in self.facts["unit_types"]:
            if u["id"] == unit_id:
                return dict(u)
        return None

    def config_summary(self, config: str | None = None) -> dict[str, Any]:
        summary = self.facts.get("config_summary", {})
        if config is None:
            return dict(summary)
        return dict(summary.get(_normalize_config(config), {}))

    def carpet_area_note(self) -> str:
        return self.facts["carpet_area_note"]

    def amenities_building(self) -> dict[str, Any]:
        return dict(self.facts["amenities_building"])

    def amenities_township(self) -> dict[str, Any]:
        """Township amenities — ALWAYS returned with the paid/under-construction caveat."""
        return dict(self.facts["amenities_township"])

    def township_utilities(self) -> list[str]:
        return list(self.facts["township_utilities"])

    def specifications(self) -> dict[str, Any]:
        return dict(self.facts["specifications"])

    def rera(self) -> dict[str, str]:
        return {
            "number": self.facts["rera_number"],
            "reference": self.facts["rera_reference"],
            "portal": self.facts["rera_portal"],
        }

    def location(self) -> dict[str, Any]:
        return {
            "address": self.facts["address"],
            "locality_hooks": list(self.facts["locality_hooks"]),
            "commute": dict(self.facts["commute"]),
            "township_size_acres": self.facts["township_size_acres"],
        }

    def contact(self) -> dict[str, str]:
        return dict(self.facts["contact"])

    def views(self) -> dict[str, Any]:
        return dict(self.facts["views"])

    def building(self) -> dict[str, Any]:
        return dict(self.facts["building"])

    def media_caveat(self) -> str:
        return self.facts["media_caveat"]

    def legal_caveat(self) -> str:
        return self.facts["legal_caveat"]

    # ------------------------------------------------------------------ Layer 2
    def commercial_is_dummy(self) -> bool:
        """True while canopy_commercial.json holds placeholder pricing."""
        return bool(self.commercial.get("is_dummy")) or (
            self.commercial.get("source") == "DUMMY_PLACEHOLDER"
        )

    def pricing(self, config: str | None = None) -> list[dict[str, Any]]:
        rows = [dict(p) for p in self.commercial.get("pricing", [])]
        if config is None:
            return rows
        target = _normalize_config(config)
        return [p for p in rows if _normalize_config(p["config"]) == target]

    def possession(self) -> dict[str, Any]:
        return dict(self.commercial.get("possession", {}))

    def booking_amount(self) -> str | None:
        return self.commercial.get("booking_amount")

    def payment_plan(self) -> str | None:
        return self.commercial.get("payment_plan")

    def commercial_disclaimer(self) -> str:
        return self.commercial.get("disclaimer", "")
