"""Unit tests for the layered garage knowledge accessors (agent/garage.py).

Pure, deterministic, no credentials — always run. Mirrors
tests/test_clinic_knowledge.py / tests/test_salon_knowledge.py's structure
for the auto-garage vertical.
"""

from __future__ import annotations

from agent.garage import GarageKnowledge, _normalize_department

K = GarageKnowledge.load()


# --------------------------------------------------------------------- Layer 0
def test_working_brief_loads_and_carries_guardrails() -> None:
    brief = K.working_brief()
    assert brief.strip()
    assert "Prime Auto Garage" in brief
    assert "indicative" in brief.lower() or "INDICATIVE" in brief


# --------------------------------------------------------------------- Layer 1
def test_core_identity() -> None:
    assert K.garage_name() == "Prime Auto Garage & Rentals"
    assert set(K.departments()) == {"Service", "Rental"}


def test_department_normalization_aliases() -> None:
    assert _normalize_department("repair") == "Service"
    assert _normalize_department("hire") == "Rental"
    assert _normalize_department("rent") == "Rental"
    assert _normalize_department("bodywork") is None  # not a real department here


def test_rental_fleet_lookup() -> None:
    suv = K.rental_fleet_for("SUV")
    assert suv is not None
    assert "Creta" in suv["examples"]
    assert K.rental_fleet_for("Convertible") is None


def test_roadside_and_estimate_notes_present() -> None:
    assert "roadside" in K.roadside_note().lower() or "towing" in K.roadside_note().lower()
    assert "inspect" in K.estimate_note().lower()


def test_rental_terms_never_collect_documents_over_phone() -> None:
    assert "never" in K.rental_terms()["documents_required"].lower()


# --------------------------------------------------------------------- Layer 2
def test_commercial_is_dummy() -> None:
    assert K.commercial_is_dummy() is True


def test_fees_scoped_to_department() -> None:
    rental_fees = K.fees("Rental")
    assert all(f["department"] == "Rental" for f in rental_fees)
    assert any(f["service"] == "SUV rental" for f in rental_fees)
