from __future__ import annotations

from agent.data_store import DataStore
from agent.state import CallUserdata


def _active_matching_lead() -> CallUserdata:
    return CallUserdata(
        data_store=DataStore.load(),
        interest_status="active",
        requested_city="Pune",
        bhk_preference="2BHK",
        budget_max_lakh=90,
        purchase_timeline_asked=True,
        inventory_fit="exact_match",
    )


def test_unknown_interest_is_unscored_and_starts_at_interest_check():
    lead = CallUserdata(data_store=DataStore.load())

    assert lead.conversation_stage == "confirm_interest"
    assert lead.qualification_snapshot()["lead_temperature"] == "unscored"


def test_active_exact_match_is_warm_until_near_term_cta_is_accepted():
    lead = _active_matching_lead()

    snapshot = lead.qualification_snapshot()
    assert snapshot["qualification_status"] == "qualified"
    assert snapshot["lead_temperature"] == "warm"
    assert lead.conversation_stage == "present_matches_and_offer_next_step"


def test_near_term_exact_match_with_accepted_cta_is_hot():
    lead = _active_matching_lead()
    lead.purchase_timeline = "within_3_months"
    lead.next_step = "site_visit_requested"
    lead.closing_attempted = True

    snapshot = lead.qualification_snapshot()
    assert snapshot["qualification_status"] == "qualified"
    assert snapshot["lead_temperature"] == "hot"
    assert lead.conversation_stage == "close"


def test_casual_and_terminal_answers_override_scoring():
    casual = _active_matching_lead()
    casual.interest_status = "casual"
    assert casual.qualification_snapshot()["qualification_status"] == "casual_enquiry"
    assert casual.qualification_snapshot()["lead_temperature"] == "nurture"

    opted_out = _active_matching_lead()
    opted_out.interest_status = "opted_out"
    assert opted_out.qualification_snapshot()["qualification_status"] == "do_not_contact"
    assert opted_out.qualification_snapshot()["lead_temperature"] == "no_opportunity"
