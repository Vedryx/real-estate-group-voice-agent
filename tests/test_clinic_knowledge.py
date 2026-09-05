"""Unit tests for the layered clinic knowledge accessors (agent/clinic.py).

Pure, deterministic, no credentials — always run. Mirrors
tests/test_canopy_knowledge.py's structure for the clinic vertical.
"""

from __future__ import annotations

from agent.clinic import ClinicKnowledge, _normalize_department

K = ClinicKnowledge.load()


# --------------------------------------------------------------------- Layer 0
def test_working_brief_loads_and_carries_guardrails() -> None:
    brief = K.working_brief()
    assert brief.strip()
    assert "Wellness Point" in brief
    assert "indicative" in brief.lower()


# --------------------------------------------------------------------- Layer 1
def test_core_identity() -> None:
    assert K.clinic_name() == "Wellness Point Clinic"
    assert "General Medicine" in K.departments()
    assert "Dental" in K.departments()


def test_department_normalization_aliases() -> None:
    assert _normalize_department("dental") == "Dental"
    assert _normalize_department("GP") == "General Medicine"
    assert _normalize_department("general physician") == "General Medicine"
    assert _normalize_department("orthopedics") is None  # not a real department here


def test_doctors_for_filters_by_department() -> None:
    dental = K.doctors_for("Dental")
    general = K.doctors_for("general medicine")
    assert len(dental) == 1 and dental[0]["id"] == "dr-khan"
    assert len(general) == 1 and general[0]["id"] == "dr-mehta"


def test_services_scoped_to_department() -> None:
    dental_services = K.services("Dental")
    assert "tooth extraction" in dental_services
    assert "vaccination (select vaccines)" not in dental_services


def test_emergency_and_insurance_notes_present() -> None:
    assert "ambulance" in K.emergency_note().lower()
    assert "cashless" in K.insurance_note().lower()


# --------------------------------------------------------------------- Layer 2
def test_commercial_is_dummy() -> None:
    assert K.commercial_is_dummy() is True


def test_fees_scoped_to_department() -> None:
    dental_fees = K.fees("Dental")
    assert all(f["department"] == "Dental" for f in dental_fees)
    assert any(f["service"] == "Root canal treatment" for f in dental_fees)
