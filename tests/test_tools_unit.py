"""Pure unit tests for the Canopy tool logic — no LLM, no session, no creds.

@function_tool leaves the wrapped coroutine callable with its original
signature, so each tool is driven directly with a lightweight stub context.
These check the tools compute the right answer once called; the LLM-driven
tests (test_behavior_canopy.py) check the model *chooses* the right tool.

The autouse isolate_leads_log fixture (conftest.py) redirects lead writes to
a throwaway file, so the log_* tools are safe to exercise here.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from livekit.agents.llm import ToolError

from livekit.agents.llm import StopResponse

from agent.canopy import CanopyKnowledge
from agent.state import CallUserdata
from tools import catalog


@pytest.fixture
def ud() -> CallUserdata:
    return CallUserdata(knowledge=CanopyKnowledge.load(), caller_phone="+919812345678")


@pytest.fixture
def ctx(ud):
    # session.say is used for deterministic confirmations (C1) and the slow-op
    # filler (C2); a no-op stub is enough for these logic tests.
    return SimpleNamespace(userdata=ud, session=SimpleNamespace(say=lambda *a, **k: None))


# ------------------------------------------------------------- Layer 1 facts
async def test_get_configurations(ctx):
    result = await catalog.get_configurations(ctx)
    assert result["configs"] == ["2 BHK", "3 BHK"]
    assert "2 BHK" in result["summary"] and "3 BHK" in result["summary"]


async def test_get_unit_details_speaks_rera_carpet_not_carpet_plus_balcony(ctx):
    result = await catalog.get_unit_details(ctx, config="3BHK")
    assert result["found"] is True
    assert result["config"] == "3 BHK"
    assert len(result["layouts"]) == 4
    # Sizes returned are RERA carpet; the carpet+balcony field is NOT exposed here.
    for layout in result["layouts"]:
        assert "carpet_sqft" in layout
        assert "carpet_plus_balconies_sqft" not in layout
    # Filtering to 3 BHK records the caller's config interest.
    assert ctx.userdata.config_interest == "3 BHK"


async def test_get_unit_details_unknown_config(ctx):
    result = await catalog.get_unit_details(ctx, config="4 BHK")
    assert result["found"] is False


async def test_get_amenities_township_forces_paid_caveat(ctx):
    result = await catalog.get_amenities(ctx, scope="township")
    assert result["must_state_caveat"] is True
    assert "paid" in result["caveat"].lower()


async def test_get_amenities_building_no_caveat(ctx):
    result = await catalog.get_amenities(ctx, scope="building")
    assert result["must_state_caveat"] is False
    assert "kaylasha_activity_centre" in result["amenities"]


async def test_get_rera_returns_real_number(ctx):
    result = await catalog.get_rera(ctx)
    assert result["number"] == "P52100079518"


async def test_get_location_is_approximate(ctx):
    result = await catalog.get_location(ctx)
    assert "Bhugaon" in result["address"]
    assert "approximate" in result["note"].lower()


# ------------------------------------------------------------- Layer 2 (DUMMY)
async def test_get_pricing_is_flagged_indicative(ctx):
    result = await catalog.get_pricing(ctx, config="2 BHK")
    assert result["indicative_only"] is True
    assert result["pricing"]  # non-empty
    assert "indicative" in result["disclaimer"].lower()
    assert ctx.userdata.config_interest == "2 BHK"


async def test_get_possession_is_flagged_indicative(ctx):
    result = await catalog.get_possession(ctx)
    assert result["indicative_only"] is True
    assert result["possession"].get("target")


async def test_commercial_detail_unavailable_never_answers(ctx):
    result = await catalog.commercial_detail_unavailable(ctx, topic="stamp_duty")
    assert result["available"] is False
    assert result["topic"] == "stamp_duty"


# ------------------------------------------------------------- qualification
async def test_record_qualification_sets_state(ctx):
    result = await catalog.record_lead_qualification(
        ctx,
        interest_status="active",
        config_interest="3bhk",
        purchase_timeline="within_3_months",
        purchase_purpose="self_use",
    )
    assert result["recorded"] is True
    assert ctx.userdata.interest_status == "active"
    assert ctx.userdata.config_interest == "3 BHK"
    assert ctx.userdata.purchase_timeline_asked is True


async def test_record_qualification_rejects_invalid_interest(ctx):
    with pytest.raises(ToolError):
        await catalog.record_lead_qualification(ctx, interest_status="maybe")


# ------------------------------------------------------------- CTAs / logging
async def test_schedule_site_visit_requires_name_and_phone(ud):
    # phone present (caller_phone) but no name -> ToolError
    ctx = SimpleNamespace(userdata=ud)
    with pytest.raises(ToolError):
        await catalog.schedule_site_visit(ctx, preferred_date="Saturday")


async def test_schedule_site_visit_logs_request(ctx):
    # success speaks a deterministic confirmation and raises StopResponse (C1)
    with pytest.raises(StopResponse):
        await catalog.schedule_site_visit(ctx, preferred_date="this Saturday", name="Asha")
    assert ctx.userdata.next_step == "site_visit_requested"
    assert ctx.userdata.lead_logged is True


async def test_log_callback_logs_and_sets_next_step(ctx):
    with pytest.raises(StopResponse):
        await catalog.log_callback(ctx, preferred_time="kal shaam", name="Ravi")
    assert ctx.userdata.next_step == "callback_requested"


async def test_log_lead_rejects_invalid_outcome(ctx):
    with pytest.raises(ToolError):
        await catalog.log_lead(ctx, outcome="not_serviceable_area")


async def test_log_lead_terminal_sets_state(ctx):
    result = await catalog.log_lead(
        ctx, outcome="opted_out", consent_to_be_contacted=False
    )
    assert result["logged"] is True
    assert ctx.userdata.interest_status == "opted_out"
    assert ctx.userdata.conversation_stage == "closing"


async def test_cta_ready_needs_two_buying_signals():
    k = CanopyKnowledge.load()
    ud = CallUserdata(knowledge=k)
    assert ud.cta_ready is False  # nothing yet
    ud.config_interest = "3 BHK"  # 1 signal
    assert ud.cta_ready is False
    ud.purchase_timeline_asked = True  # 2 signals
    assert ud.cta_ready is True
    ud.site_visit_declined = True  # a decline overrides
    assert ud.cta_ready is False


async def test_schedule_site_visit_records_cta_offer(ctx):
    with pytest.raises(StopResponse):
        await catalog.schedule_site_visit(ctx, preferred_date="Saturday", name="Asha")
    assert ctx.userdata.site_visit_offered is True
    assert ctx.userdata.cta_offer_count == 1


async def test_terminal_outcome_marks_site_visit_declined(ctx):
    await catalog.log_lead(ctx, outcome="not_interested", consent_to_be_contacted=True)
    assert ctx.userdata.site_visit_declined is True
    assert ctx.userdata.cta_ready is False


async def test_derive_final_outcome_from_state():
    """The closing outcome is computed from state, never taken from the LLM."""
    k = CanopyKnowledge.load()

    site = CallUserdata(knowledge=k, caller_phone="+1")
    site.next_step = "site_visit_requested"
    assert catalog._derive_final_outcome(site) == "site_visit_requested"

    cb = CallUserdata(knowledge=k, caller_phone="+1")
    cb.next_step = "callback_requested"
    assert catalog._derive_final_outcome(cb) == "callback_requested"

    dnd = CallUserdata(knowledge=k, caller_phone="+1")
    dnd.interest_status = "opted_out"
    assert catalog._derive_final_outcome(dnd) == "opted_out"

    browsing = CallUserdata(knowledge=k, caller_phone="+1")
    browsing.interest_status = "active"
    assert catalog._derive_final_outcome(browsing) == "info_only_no_lead"

    cold = CallUserdata(knowledge=k, caller_phone="+1")
    assert catalog._derive_final_outcome(cold) == "spam_or_abandoned"


async def test_end_call_takes_no_outcome_arg(ctx):
    """end_call must not accept an LLM-supplied outcome (it once invented a compound one)."""
    import inspect

    sig = inspect.signature(catalog.end_call.__wrapped__ if hasattr(catalog.end_call, "__wrapped__") else catalog.end_call)
    assert "outcome" not in sig.parameters


async def test_end_call_derives_and_logs(ctx):
    ctx.userdata.next_step = "callback_requested"
    ctx.speech_handle = SimpleNamespace(add_done_callback=lambda cb: None)
    ctx.session = SimpleNamespace(shutdown=lambda **kw: None)
    msg = await catalog.end_call(ctx)
    assert "goodbye" in msg.lower()
    assert ctx.userdata.lead_logged is True


async def test_callback_persist_failure_returns_logged_false(ctx, monkeypatch):
    """If the write fails, the tool must report logged=False and instruct not to confirm."""
    def boom(**kwargs):
        raise TypeError("Object of type MagicMock is not JSON serializable")

    monkeypatch.setattr(catalog.ds, "log_lead", boom)
    result = await catalog.log_callback(ctx, preferred_time="evening", name="Ravi")
    assert result["logged"] is False
    assert "do not tell the caller" in result["instruction"].lower()
    assert ctx.userdata.lead_logged is False


async def test_callback_logged_only_once(ctx, monkeypatch):
    """A callback must not be written twice (log_callback then log_lead at close)."""
    outcomes = []
    real = catalog.ds.log_lead

    def counting(**kw):
        outcomes.append(kw["outcome"])
        return real(**kw)

    monkeypatch.setattr(catalog.ds, "log_lead", counting)
    with pytest.raises(StopResponse):
        await catalog.log_callback(ctx, preferred_time="aaj 4 baje")
    # model redundantly logs the same outcome again at close
    r2 = await catalog.log_lead(ctx, outcome="callback_requested")
    assert r2["logged"] is True
    assert outcomes.count("callback_requested") == 1  # written once, not twice


async def test_end_call_does_not_duplicate_logged_outcome(ctx, monkeypatch):
    outcomes = []
    real = catalog.ds.log_lead
    monkeypatch.setattr(
        catalog.ds, "log_lead", lambda **kw: (outcomes.append(kw["outcome"]), real(**kw))[1]
    )
    with pytest.raises(StopResponse):
        await catalog.log_callback(ctx, preferred_time="4pm")
    ctx.speech_handle = SimpleNamespace(add_done_callback=lambda cb: None)
    ctx.session = SimpleNamespace(shutdown=lambda **kw: None)
    await catalog.end_call(ctx)
    assert outcomes.count("callback_requested") == 1


def test_cta_confirmation_is_deterministic_and_localized():
    hi = catalog._cta_confirmation("visit", "Asha", "kal 4 baje", "hi-IN")
    assert "Asha ji" in hi and "kal 4 baje" in hi and "save" in hi.lower()
    en = catalog._cta_confirmation("callback", None, "evening", "en-IN")
    assert "evening" in en and "team will call" in en.lower()
    mr = catalog._cta_confirmation("visit", "Raj", "udya", "mr-IN")
    assert "Raj ji" in mr and "udya" in mr


async def test_no_multi_project_tools_remain():
    """The multi-project tools must be gone from the catalog surface."""
    names = {t.info.name for t in catalog.ALL_TOOLS}
    for removed in (
        "search_projects",
        "get_project_details",
        "get_nearby_projects_to_workplace",
        "log_out_of_area_interest",
        "check_metro_proximity",
    ):
        assert removed not in names
    # And the single-project tool set is present.
    for present in ("get_configurations", "get_pricing", "schedule_site_visit", "log_callback"):
        assert present in names
