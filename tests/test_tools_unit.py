"""Pure unit tests for the Canopy tool logic — no LLM, no session, no creds.

@function_tool leaves the wrapped coroutine callable with its original
signature, so each tool is driven directly with a lightweight stub context.
After D4 the surface is six tools: get_detailed_project_info, request_site_visit,
request_callback, escalate_to_human, log_terminal_outcome, end_call.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from livekit.agents.llm import StopResponse, ToolError

from agent.canopy import CanopyKnowledge
from agent.state import CallUserdata, update_qualification_from_text
from tools import catalog


@pytest.fixture
def ud() -> CallUserdata:
    return CallUserdata(knowledge=CanopyKnowledge.load(), caller_phone="+919812345678")


@pytest.fixture
def ctx(ud):
    # session.say is used for deterministic confirmations (C1) and the slow-op
    # filler (C2); a no-op stub is enough for these logic tests. tts is a truthy
    # sentinel so _say_line takes the cascade say()+StopResponse path (tts=None
    # would select the S2S branch, which returns instead of raising).
    return SimpleNamespace(
        userdata=ud, session=SimpleNamespace(say=lambda *a, **k: None, tts=object())
    )


# --------------------------------------------------- get_detailed_project_info
async def test_info_configurations(ctx):
    r = await catalog.get_detailed_project_info(ctx, topic="configurations")
    assert r["configs"] == ["2 BHK", "3 BHK"]
    assert "2 BHK" in r["summary"] and "3 BHK" in r["summary"]


async def test_info_unit_details_rera_carpet_not_carpet_plus_balcony(ctx):
    r = await catalog.get_detailed_project_info(ctx, topic="unit_details", config="3BHK")
    assert r["found"] is True and r["config"] == "3 BHK"
    assert len(r["layouts"]) == 4
    for layout in r["layouts"]:
        assert "carpet_sqft" in layout
        assert "carpet_plus_balconies_sqft" not in layout
    assert ctx.userdata.config_interest == "3 BHK"  # config drives interest


async def test_info_unit_details_unknown_config(ctx):
    r = await catalog.get_detailed_project_info(ctx, topic="unit_details", config="4 BHK")
    assert r["found"] is False


async def test_info_amenities_township_forces_paid_caveat(ctx):
    r = await catalog.get_detailed_project_info(ctx, topic="amenities_township")
    assert r["must_state_caveat"] is True
    assert "paid" in r["caveat"].lower()


async def test_info_amenities_building(ctx):
    r = await catalog.get_detailed_project_info(ctx, topic="amenities_building")
    assert r["must_state_caveat"] is False
    assert "kaylasha_activity_centre" in r["amenities"]


async def test_info_rera_real_number(ctx):
    r = await catalog.get_detailed_project_info(ctx, topic="rera")
    assert r["number"] == "P52100079518"


async def test_info_location_is_approximate(ctx):
    r = await catalog.get_detailed_project_info(ctx, topic="location")
    assert "Bhugaon" in r["address"]
    assert "approximate" in r["note"].lower()


async def test_info_pricing_indicative(ctx):
    r = await catalog.get_detailed_project_info(ctx, topic="pricing", config="2 BHK")
    assert r["indicative_only"] is True
    assert r["pricing"]
    assert "indicative" in r["disclaimer"].lower()
    assert ctx.userdata.config_interest == "2 BHK"


async def test_info_possession_indicative(ctx):
    r = await catalog.get_detailed_project_info(ctx, topic="possession")
    assert r["indicative_only"] is True
    assert r["possession"].get("target")


# --------------------------------------------------- qualification heuristic (D4)
def test_qualification_from_text_sets_config_and_interest():
    k = CanopyKnowledge.load()
    ud = CallUserdata(knowledge=k)
    update_qualification_from_text(ud, "haan main teen BHK dekh raha tha investment ke liye")
    assert ud.config_interest == "3 BHK"
    assert ud.purchase_purpose == "investment"
    assert ud.interest_status == "active"


def test_cta_ready_needs_two_buying_signals():
    k = CanopyKnowledge.load()
    ud = CallUserdata(knowledge=k)
    assert ud.cta_ready is False
    ud.config_interest = "3 BHK"  # 1 signal
    assert ud.cta_ready is False
    ud.purchase_timeline_asked = True  # 2 signals
    assert ud.cta_ready is True
    ud.site_visit_declined = True
    assert ud.cta_ready is False


# --------------------------------------------------- CTAs / terminal
async def test_request_site_visit_requires_name_and_phone(ud):
    ctx = SimpleNamespace(
        userdata=ud, session=SimpleNamespace(say=lambda *a, **k: None, tts=object())
    )
    with pytest.raises(ToolError):
        await catalog.request_site_visit(ctx, preferred_date="Saturday")


async def test_request_site_visit_records_and_confirms(ctx):
    with pytest.raises(StopResponse):
        await catalog.request_site_visit(ctx, preferred_date="this Saturday", name="Asha")
    assert ctx.userdata.next_step == "site_visit_requested"
    assert ctx.userdata.lead_logged is True
    assert ctx.userdata.site_visit_offered is True
    assert ctx.userdata.cta_offer_count == 1


async def test_request_site_visit_s2s_returns_line_without_stopresponse(ud):
    # S2S: no TTS -> _say_line can't say(); the tool must NOT raise StopResponse
    # and instead return the confirmation for the realtime model to speak.
    ud.caller_phone = "+919812345678"
    ctx = SimpleNamespace(userdata=ud, session=SimpleNamespace(tts=None))
    result = await catalog.request_site_visit(ctx, preferred_date="kal 4 baje", name="Asha")
    assert result["logged"] is True
    assert result["say_to_caller"]  # localized confirmation line for the model
    assert ud.lead_logged is True
    assert ud.write_ack_spoken is False  # write-ack no-ops in S2S


async def test_request_callback_records_and_confirms(ctx):
    with pytest.raises(StopResponse):
        await catalog.request_callback(ctx, preferred_time="kal shaam", name="Ravi")
    assert ctx.userdata.next_step == "callback_requested"
    assert ctx.userdata.callback_offered is True


async def test_request_callback_stores_raw_and_resolved_datetime(ctx, monkeypatch):
    captured = {}
    real = catalog.ds.log_lead

    def cap(**kw):
        captured.update(kw)
        return real(**kw)

    monkeypatch.setattr(catalog.ds, "log_lead", cap)
    with pytest.raises(StopResponse):
        await catalog.request_callback(ctx, preferred_time="kal 4 baje")
    assert captured["preferred_datetime_raw"] == "kal 4 baje"
    assert captured["timezone_name"] == "Asia/Kolkata"
    iso = captured["preferred_datetime_iso"]
    assert iso and iso.endswith("+05:30") and "T16:00:00" in iso  # tomorrow 4pm


async def test_callback_persist_failure_returns_logged_false(ctx, monkeypatch):
    def boom(**kwargs):
        raise TypeError("Object of type MagicMock is not JSON serializable")

    monkeypatch.setattr(catalog.ds, "log_lead", boom)
    result = await catalog.request_callback(ctx, preferred_time="evening", name="Ravi")
    assert result["logged"] is False
    assert "do not tell the caller" in result["instruction"].lower()
    assert ctx.userdata.lead_logged is False


async def test_callback_logged_only_once(ctx, monkeypatch):
    outcomes = []
    real = catalog.ds.log_lead

    def counting(**kw):
        outcomes.append(kw["outcome"])
        return real(**kw)

    monkeypatch.setattr(catalog.ds, "log_lead", counting)
    with pytest.raises(StopResponse):
        await catalog.request_callback(ctx, preferred_time="aaj 4 baje")
    # model redundantly logs the same outcome again at close
    r2 = await catalog.log_terminal_outcome(ctx, outcome="callback_requested")
    assert r2["logged"] is True
    assert outcomes.count("callback_requested") == 1


async def test_log_terminal_outcome_rejects_invalid(ctx):
    with pytest.raises(ToolError):
        await catalog.log_terminal_outcome(ctx, outcome="not_serviceable_area")


async def test_log_terminal_opted_out_sets_state(ctx):
    r = await catalog.log_terminal_outcome(ctx, outcome="opted_out", consent_to_be_contacted=False)
    assert r["logged"] is True
    assert ctx.userdata.interest_status == "opted_out"
    assert ctx.userdata.site_visit_declined is True
    assert ctx.userdata.conversation_stage == "closing"


# --------------------------------------------------- end_call
def test_derive_final_outcome_from_state():
    k = CanopyKnowledge.load()

    site = CallUserdata(knowledge=k, caller_phone="+1")
    site.next_step = "site_visit_requested"
    assert catalog._derive_final_outcome(site) == "site_visit_requested"

    dnd = CallUserdata(knowledge=k, caller_phone="+1")
    dnd.interest_status = "opted_out"
    assert catalog._derive_final_outcome(dnd) == "opted_out"

    browsing = CallUserdata(knowledge=k, caller_phone="+1")
    browsing.interest_status = "active"
    assert catalog._derive_final_outcome(browsing) == "info_only_no_lead"

    cold = CallUserdata(knowledge=k, caller_phone="+1")
    assert catalog._derive_final_outcome(cold) == "spam_or_abandoned"


async def test_end_call_takes_no_outcome_arg(ctx):
    import inspect

    fn = catalog.end_call.__wrapped__ if hasattr(catalog.end_call, "__wrapped__") else catalog.end_call
    assert "outcome" not in inspect.signature(fn).parameters


async def test_end_call_derives_and_logs(ctx):
    ctx.userdata.next_step = "callback_requested"
    ctx.speech_handle = SimpleNamespace(add_done_callback=lambda cb: None)
    ctx.session = SimpleNamespace(shutdown=lambda **kw: None)
    msg = await catalog.end_call(ctx)
    assert "goodbye" in msg.lower()
    assert ctx.userdata.lead_logged is True


async def test_end_call_does_not_duplicate_logged_outcome(ctx, monkeypatch):
    outcomes = []
    real = catalog.ds.log_lead
    monkeypatch.setattr(
        catalog.ds, "log_lead", lambda **kw: (outcomes.append(kw["outcome"]), real(**kw))[1]
    )
    with pytest.raises(StopResponse):
        await catalog.request_callback(ctx, preferred_time="4pm")
    ctx.speech_handle = SimpleNamespace(add_done_callback=lambda cb: None)
    ctx.session = SimpleNamespace(shutdown=lambda **kw: None)
    await catalog.end_call(ctx)
    assert outcomes.count("callback_requested") == 1


# --------------------------------------------------- misc
def test_cta_confirmation_is_deterministic_and_localized():
    hi = catalog._cta_confirmation("visit", "Asha", "kal 4 baje", "hi-IN")
    assert "Asha ji" in hi and "kal 4 baje" in hi and "save" in hi.lower()
    en = catalog._cta_confirmation("callback", None, "evening", "en-IN")
    assert "evening" in en and "team will call" in en.lower()


def test_tool_surface_is_the_consolidated_six():
    names = {t.info.name for t in catalog.ALL_TOOLS}
    assert names == {
        "get_detailed_project_info",
        "request_site_visit",
        "request_callback",
        "escalate_to_human",
        "log_terminal_outcome",
        "end_call",
    }
