"""Pure unit tests for the salon tool logic — no LLM, no session, no creds.

Mirrors tests/test_tools_unit_clinic.py's structure/fixtures for the salon
vertical.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from livekit.agents.llm import StopResponse, ToolError

from agent.salon import SalonKnowledge
from agent.state_salon import SalonCallUserdata
from tools import catalog_salon as catalog


@pytest.fixture
def ud() -> SalonCallUserdata:
    return SalonCallUserdata(knowledge=SalonKnowledge.load(), caller_phone="+919812345678")


@pytest.fixture
def ctx(ud):
    return SimpleNamespace(
        userdata=ud, session=SimpleNamespace(say=lambda *a, **k: None, tts=object())
    )


# --------------------------------------------------------- get_salon_info
async def test_info_departments(ctx):
    r = await catalog.get_salon_info(ctx, topic="departments")
    assert set(r["departments"]) == {"Hair", "Skin", "Nails", "Makeup"}


async def test_info_stylists_filters_and_sets_interest(ctx):
    r = await catalog.get_salon_info(ctx, topic="stylists", department="Nails")
    assert len(r["stylists"]) == 1
    assert r["stylists"][0]["id"] == "priyanka"
    assert ctx.userdata.department_interest == "Nails"  # department drives interest


async def test_info_fees_indicative(ctx):
    r = await catalog.get_salon_info(ctx, topic="fees", department="Skin")
    assert r["indicative_only"] is True
    assert r["fees"]
    assert "indicative" in r["disclaimer"].lower()


async def test_info_bridal_routes_to_consultation(ctx):
    r = await catalog.get_salon_info(ctx, topic="bridal")
    assert "consultation" in r["bridal_note"].lower()


# --------------------------------------------------------- request_appointment
async def test_request_appointment_requires_name_and_phone(ud):
    ctx = SimpleNamespace(
        userdata=ud, session=SimpleNamespace(say=lambda *a, **k: None, tts=object())
    )
    with pytest.raises(ToolError):
        await catalog.request_appointment(ctx, preferred_date="Saturday")


async def test_request_appointment_rejects_invalid_phone(ud):
    ctx = SimpleNamespace(
        userdata=ud, session=SimpleNamespace(say=lambda *a, **k: None, tts=object())
    )
    with pytest.raises(ToolError):
        await catalog.request_appointment(
            ctx, preferred_date="Saturday", name="Neha", phone="1234567890"
        )
    assert ctx.userdata.lead_logged is False


async def test_request_appointment_records_first_time_flag(ctx):
    with pytest.raises(StopResponse):
        await catalog.request_appointment(
            ctx,
            preferred_date="Saturday",
            department="Hair",
            service="hair color",
            first_time_client=True,
            name="Neha",
        )
    assert ctx.userdata.next_step == "appointment_requested"
    assert ctx.userdata.lead_logged is True
    assert ctx.userdata.department_interest == "Hair"
    assert ctx.userdata.first_time_chemical_service is True
    assert ctx.userdata.service_interest == "hair color"


async def test_request_appointment_s2s_returns_line_without_stopresponse(ud):
    ctx = SimpleNamespace(userdata=ud, session=SimpleNamespace(tts=None))
    result = await catalog.request_appointment(ctx, preferred_date="Sunday", name="Asha")
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
        await catalog.log_terminal_outcome(ctx, outcome="site_visit_requested")  # not a salon outcome


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
        "get_salon_info",
        "request_appointment",
        "request_callback",
        "escalate_to_human",
        "log_terminal_outcome",
        "end_call",
    }


async def test_lead_written_with_salon_project_tag_and_own_outcomes(ctx, monkeypatch):
    captured = {}
    real = catalog.ds.log_lead

    def cap(**kw):
        captured.update(kw)
        return real(**kw)

    monkeypatch.setattr(catalog.ds, "log_lead", cap)
    with pytest.raises(StopResponse):
        await catalog.request_callback(ctx, preferred_time="4pm", name="Meera")
    assert captured["project"] == "Aura Salon & Spa"
    assert captured["valid_outcomes"] == catalog.SALON_VALID_OUTCOMES
