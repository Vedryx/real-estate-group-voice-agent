"""Unit tests for the layered salon knowledge accessors (agent/salon.py).

Pure, deterministic, no credentials — always run. Mirrors
tests/test_clinic_knowledge.py's structure for the salon vertical.
"""

from __future__ import annotations

from agent.salon import SalonKnowledge, _normalize_department

K = SalonKnowledge.load()


# --------------------------------------------------------------------- Layer 0
def test_working_brief_loads_and_carries_guardrails() -> None:
    brief = K.working_brief()
    assert brief.strip()
    assert "Aura Salon" in brief
    assert "indicative" in brief.lower()


# --------------------------------------------------------------------- Layer 1
def test_core_identity() -> None:
    assert K.salon_name() == "Aura Salon & Spa"
    assert set(K.departments()) == {"Hair", "Skin", "Nails", "Makeup"}


def test_department_normalization_aliases() -> None:
    assert _normalize_department("hair") == "Hair"
    assert _normalize_department("bridal") == "Makeup"
    assert _normalize_department("facial") == "Skin"
    assert _normalize_department("massage") is None  # not a real department here


def test_stylists_for_filters_by_department() -> None:
    hair = K.stylists_for("Hair")
    nails = K.stylists_for("nails")
    assert len(hair) == 1 and hair[0]["id"] == "ritu"
    assert len(nails) == 1 and nails[0]["id"] == "priyanka"


def test_services_scoped_to_department() -> None:
    hair_services = K.services("Hair")
    assert "keratin / smoothening treatment" in hair_services
    assert "manicure" not in hair_services


def test_bridal_requires_consultation() -> None:
    assert "consultation" in K.bridal_note().lower()


# --------------------------------------------------------------------- Layer 2
def test_commercial_is_dummy() -> None:
    assert K.commercial_is_dummy() is True


def test_fees_scoped_to_department() -> None:
    hair_fees = K.fees("Hair")
    assert all(f["department"] == "Hair" for f in hair_fees)
    assert any(f["service"].startswith("Keratin") for f in hair_fees)
