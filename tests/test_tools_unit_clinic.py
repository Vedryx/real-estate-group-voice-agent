"""Pure unit tests for the clinic tool logic — no LLM, no session, no creds.

Mirrors tests/test_tools_unit.py's structure/fixtures for the clinic vertical.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from livekit.agents.llm import StopResponse, ToolError

from agent.clinic import ClinicKnowledge
from agent.state_clinic import ClinicCallUserdata
from tools import catalog_clinic as catalog


@pytest.fixture
def ud() -> ClinicCallUserdata:
    return ClinicCallUserdata(knowledge=ClinicKnowledge.load(), caller_phone="+919812345678")


@pytest.fixture
def ctx(ud):
    return SimpleNamespace(
        userdata=ud, session=SimpleNamespace(say=lambda *a, **k: None, tts=object())
    )


# --------------------------------------------------------- get_clinic_info
async def test_info_departments(ctx):
    r = await catalog.get_clinic_info(ctx, topic="departments")
    assert set(r["departments"]) == {"General Medicine", "Dental"}


async def test_info_doctors_filters_and_sets_interest(ctx):
    r = await catalog.get_clinic_info(ctx, topic="doctors", department="Dental")
    assert len(r["doctors"]) == 1
    assert r["doctors"][0]["id"] == "dr-khan"
    assert ctx.userdata.department_interest == "Dental"  # department drives interest


async def test_info_fees_indicative(ctx):
    r = await catalog.get_clinic_info(ctx, topic="fees", department="General Medicine")
    assert r["indicative_only"] is True
    assert r["fees"]
    assert "indicative" in r["disclaimer"].lower()


async def test_info_emergency(ctx):
    r = await catalog.get_clinic_info(ctx, topic="emergency")
    assert "ambulance" in r["emergency_note"].lower()


# --------------------------------------------------------- request_appointment
async def test_request_appointment_requires_name_and_phone(ud):
    ctx = SimpleNamespace(
        userdata=ud, session=SimpleNamespace(say=lambda *a, **k: None, tts=object())
    )
    with pytest.raises(ToolError):
        await catalog.request_appointment(ctx, preferred_date="tomorrow")


async def test_request_appointment_rejects_invalid_phone(ud):
    ctx = SimpleNamespace(
        userdata=ud, session=SimpleNamespace(say=lambda *a, **k: None, tts=object())
    )
    with pytest.raises(ToolError):
        await catalog.request_appointment(
            ctx, preferred_date="tomorrow", name="Rohit", phone="1234567890"
        )
    assert ctx.userdata.lead_logged is False


async def test_request_appointment_records_and_confirms(ctx):
    with pytest.raises(StopResponse):
        await catalog.request_appointment(
            ctx, preferred_date="tomorrow morning", department="Dental", name="Rohit"
        )
    assert ctx.userdata.next_step == "appointment_requested"
    assert ctx.userdata.lead_logged is True
    assert ctx.userdata.department_interest == "Dental"
    assert ctx.userdata.cta_offer_count == 1


async def test_request_appointment_s2s_returns_line_without_stopresponse(ud):
    ctx = SimpleNamespace(userdata=ud, session=SimpleNamespace(tts=None))
    result = await catalog.request_appointment(ctx, preferred_date="Saturday", name="Asha")
    assert result["logged"] is True
    assert result["say_to_caller"]
    assert ud.lead_logged is True


# --------------------------------------------------------- request_callback
async def test_request_callback_records_and_confirms(ctx):
    with pytest.raises(StopResponse):
        await catalog.request_callback(ctx, preferred_time="evening", name="Ravi")
    assert ctx.userdata.next_step == "callback_requested"
    assert ctx.userdata.callback_offered is True


async def test_request_callback_rejects_invalid_phone(ctx):
    with pytest.raises(ToolError):
        await catalog.request_callback(
            ctx, preferred_time="evening", name="Ravi", phone="98765"
        )


# --------------------------------------------------------- log_terminal_outcome
async def test_log_terminal_outcome_rejects_invalid(ctx):
    with pytest.raises(ToolError):
        await catalog.log_terminal_outcome(ctx, outcome="site_visit_requested")  # real-estate outcome, not clinic


async def test_log_terminal_opted_out_sets_state(ctx):
    r = await catalog.log_terminal_outcome(ctx, outcome="opted_out", consent_to_be_contacted=False)
    assert r["logged"] is True
    assert ctx.userdata.interest_status == "opted_out"
    assert ctx.userdata.appointment_declined is True
    assert ctx.userdata.conversation_stage == "closing"


# --------------------------------------------------------- end_call
async def test_end_call_derives_and_logs(ctx):
    ctx.userdata.next_step = "callback_requested"
    ctx.speech_handle = SimpleNamespace(add_done_callback=lambda cb: None)
    ctx.session = SimpleNamespace(shutdown=lambda **kw: None)
    msg = await catalog.end_call(ctx)
    assert "goodbye" in msg.lower()
    assert ctx.userdata.lead_logged is True


# --------------------------------------------------------- misc
def test_tool_surface_is_the_consolidated_six():
    names = {t.info.name for t in catalog.ALL_TOOLS}
    assert names == {
        "get_clinic_info",
        "request_appointment",
        "request_callback",
        "escalate_to_human",
        "log_terminal_outcome",
        "end_call",
    }


async def test_lead_written_with_clinic_project_tag_and_own_outcomes(ctx, monkeypatch):
    captured = {}
    real = catalog.ds.log_lead

    def cap(**kw):
        captured.update(kw)
        return real(**kw)

    monkeypatch.setattr(catalog.ds, "log_lead", cap)
    with pytest.raises(StopResponse):
        await catalog.request_callback(ctx, preferred_time="4pm", name="Meera")
    assert captured["project"] == "Wellness Point Clinic"
    assert captured["valid_outcomes"] == catalog.CLINIC_VALID_OUTCOMES
