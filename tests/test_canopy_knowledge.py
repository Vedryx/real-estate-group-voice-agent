"""Unit tests for the layered Canopy knowledge accessors (agent/canopy.py).

Pure, deterministic, no credentials — always run. Guards the facts that the
sales agent will speak, plus the two things easiest to get wrong: the
carpet-vs-carpet+balcony trap, and the dummy-commercial flag.
"""

from __future__ import annotations

from agent.canopy import CanopyKnowledge, _normalize_config

K = CanopyKnowledge.load()


# --------------------------------------------------------------------- Layer 0
def test_working_brief_loads_and_carries_guardrails() -> None:
    brief = K.working_brief()
    assert brief.strip()
    # The brief must reinforce the single-project + no-invention guardrails.
    assert "Canopy" in brief
    assert "indicative" in brief.lower()


# --------------------------------------------------------------------- Layer 1
def test_core_identity() -> None:
    assert K.project_name() == "The Canopy"
    assert K.developer() == "Paranjape"
    assert K.township() == "Forest Trails"


def test_configs_are_two_and_three_bhk_only() -> None:
    assert K.configs() == ["2 BHK", "3 BHK"]


def test_unit_types_for_filters_by_config() -> None:
    two = K.unit_types_for("2 BHK")
    three = K.unit_types_for("3BHK")  # loose spelling must still match
    assert len(two) == 1
    assert len(three) == 4
    assert {u["id"] for u in three} == {"3bhk-t3", "3bhk-t2a", "3bhk-t2b", "3bhk-t1"}


def test_headline_size_is_rera_carpet_not_carpet_plus_balcony() -> None:
    """The carpet trap: the spoken size must be RERA carpet, never carpet+balconies."""
    u = K.unit_type("2bhk-t1")
    assert u is not None
    assert u["carpet_sqft"] == 886.30
    # The combined figure exists but must be a *different*, larger field.
    assert u["carpet_plus_balconies_sqft"] == 960.02
    assert u["carpet_sqft"] < u["carpet_plus_balconies_sqft"]


def test_three_bhk_carpet_band() -> None:
    carpets = sorted(u["carpet_sqft"] for u in K.unit_types_for("3 BHK"))
    assert carpets[0] == 1229.46
    assert carpets[-1] == 1275.64


def test_rera_number_is_the_real_one() -> None:
    rera = K.rera()
    assert rera["number"] == "P52100079518"
    assert "maharera" in rera["portal"]


def test_township_amenities_always_carry_paid_caveat() -> None:
    township = K.amenities_township()
    assert township["list"]
    caveat = township["caveat"].lower()
    assert "paid" in caveat
    assert "extra cost" in caveat or "additional charges" in caveat


def test_specifications_present() -> None:
    specs = K.specifications()
    for key in ("flooring", "kitchen", "doors", "electrical", "plumbing"):
        assert key in specs and specs[key]


def test_media_and_legal_caveats_present() -> None:
    assert "artistic impression" in K.media_caveat().lower()
    assert K.legal_caveat().strip()


# --------------------------------------------------------------------- Layer 2
def test_commercial_is_flagged_dummy() -> None:
    assert K.commercial_is_dummy() is True


def test_pricing_present_for_both_configs() -> None:
    assert len(K.pricing("2 BHK")) == 1
    assert len(K.pricing("3 BHK")) == 1
    assert K.pricing() == K.pricing(None)
    for row in K.pricing():
        assert "indicative_all_in" in row


def test_nearby_places_present_and_flagged_approximate() -> None:
    nearby = K.nearby()
    assert len(nearby.get("places", [])) >= 5
    assert "approximate" in nearby["note"].lower()
    # Bavdhan should be the closest anchor from the brochure claim.
    names = [p["place"] for p in nearby["places"]]
    assert any("Bavdhan" in n for n in names)


def test_price_basis_says_not_all_inclusive() -> None:
    basis = K.price_basis()
    assert basis
    low = basis.lower()
    assert "stamp duty" in low and "not" in low  # base price, extras separate


def test_possession_present() -> None:
    poss = K.possession()
    assert poss.get("status")
    assert poss.get("target")


def test_commercial_disclaimer_says_indicative() -> None:
    assert "indicative" in K.commercial_disclaimer().lower()


# ----------------------------------------------------------------- normalizer
def test_normalize_config_variants() -> None:
    for variant in ("2 BHK", "2bhk", "2 bhk", "2", "  2Bhk "):
        assert _normalize_config(variant) == "2 BHK"
