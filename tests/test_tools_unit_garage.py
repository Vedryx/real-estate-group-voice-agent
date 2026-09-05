"""Pure unit tests for the auto-garage tool logic — no LLM, no session, no
creds.

Mirrors tests/test_tools_unit_clinic.py / tests/test_tools_unit_salon.py's
structure/fixtures, plus coverage for the two distinct booking tools
(service vs. rental) this vertical has.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from livekit.agents.llm import StopResponse, ToolError

from agent.garage import GarageKnowledge
from agent.state_garage import GarageCallUserdata
from tools import catalog_garage as catalog


@pytest.fixture
def ud() -> GarageCallUserdata:
    return GarageCallUserdata(knowledge=GarageKnowledge.load(), caller_phone="+919812345678")


@pytest.fixture
def ctx(ud):
    return SimpleNamespace(
        userdata=ud, session=SimpleNamespace(say=lambda *a, **k: None, tts=object())
    )


# --------------------------------------------------------- get_garage_info
async def test_info_departments(ctx):
    r = await catalog.get_garage_info(ctx, topic="departments")
    assert set(r["departments"]) == {"Service", "Rental"}


async def test_info_rental_fleet(ctx):
    r = await catalog.get_garage_info(ctx, topic="rental_fleet")
    categories = {f["category"] for f in r["rental_fleet"]}
    assert categories == {"Hatchback", "Sedan", "SUV"}


async def test_info_fees_scoped_and_sets_interest(ctx):
    r = await catalog.get_garage_info(ctx, topic="fees", department="Service")
    assert r["indicative_only"] is True
    assert all(f["department"] == "Service" for f in r["fees"])
    assert ctx.userdata.department_interest == "Service"


async def test_info_rental_terms_no_documents_over_phone(ctx):
    r = await catalog.get_garage_info(ctx, topic="rental_terms")
    assert "never" in r["rental_terms"]["documents_required"].lower()


# --------------------------------------------------------- request_service_appointment
async def test_request_service_requires_name_and_phone(ud):
    ctx = SimpleNamespace(
        userdata=ud, session=SimpleNamespace(say=lambda *a, **k: None, tts=object())
    )
    with pytest.raises(ToolError):
        await catalog.request_service_appointment(ctx, preferred_date="tomorrow")


async def test_request_service_rejects_invalid_phone(ud):
    ctx = SimpleNamespace(
        userdata=ud, session=SimpleNamespace(say=lambda *a, **k: None, tts=object())
    )
    with pytest.raises(ToolError):
        await catalog.request_service_appointment(
            ctx, preferred_date="tomorrow", name="Rohit", phone="1234567890"
        )
    assert ctx.userdata.lead_logged is False


async def test_request_service_records_and_confirms(ctx):
    with pytest.raises(StopResponse):
        await catalog.request_service_appointment(
            ctx, preferred_date="tomorrow", car_make_model="Honda City", service_type="oil change", name="Rohit"
        )
    assert ctx.userdata.next_step == "appointment_requested"
    assert ctx.userdata.lead_logged is True
    assert ctx.userdata.department_interest == "Service"
    assert ctx.userdata.car_make_model == "Honda City"
    assert ctx.userdata.service_type == "oil change"


# --------------------------------------------------------- request_car_rental
async def test_request_rental_requires_name_and_phone(ud):
    ctx = SimpleNamespace(
        userdata=ud, session=SimpleNamespace(say=lambda *a, **k: None, tts=object())
    )
    with pytest.raises(ToolError):
        await catalog.request_car_rental(ctx, preferred_start_date="this weekend")


async def test_request_rental_rejects_invalid_phone(ud):
    ctx = SimpleNamespace(
        userdata=ud, session=SimpleNamespace(say=lambda *a, **k: None, tts=object())
    )
    with pytest.raises(ToolError):
        await catalog.request_car_rental(
            ctx, preferred_start_date="this weekend", name="Neha", phone="1234567890"
        )
    assert ctx.userdata.lead_logged is False


async def test_request_rental_records_category_and_confirms(ctx):
    with pytest.raises(StopResponse):
        await catalog.request_car_rental(
            ctx,
            preferred_start_date="this weekend",
            car_category="suv",
            rental_duration="3 days",
            self_drive=True,
            name="Neha",
        )
    assert ctx.userdata.next_step == "appointment_requested"
    assert ctx.userdata.lead_logged is True
    assert ctx.userdata.department_interest == "Rental"
    assert ctx.userdata.car_category == "SUV"
    assert ctx.userdata.rental_duration == "3 days"
    assert ctx.userdata.self_drive is True


async def test_request_rental_s2s_returns_line_without_stopresponse(ud):
    ctx = SimpleNamespace(userdata=ud, session=SimpleNamespace(tts=None))
    result = await catalog.request_car_rental(ctx, preferred_start_date="Monday", name="Asha")
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
        await catalog.log_terminal_outcome(ctx, outcome="site_visit_requested")  # not a garage outcome


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
def test_tool_surface_is_the_consolidated_seven():
    names = {t.info.name for t in catalog.ALL_TOOLS}
    assert names == {
        "get_garage_info",
        "request_service_appointment",
        "request_car_rental",
        "request_callback",
        "escalate_to_human",
        "log_terminal_outcome",
        "end_call",
    }


async def test_lead_written_with_garage_project_tag_and_own_outcomes(ctx, monkeypatch):
    captured = {}
    real = catalog.ds.log_lead

    def cap(**kw):
        captured.update(kw)
        return real(**kw)

    monkeypatch.setattr(catalog.ds, "log_lead", cap)
    with pytest.raises(StopResponse):
        await catalog.request_callback(ctx, preferred_time="4pm", name="Meera")
    assert captured["project"] == "Prime Auto Garage & Rentals"
    assert captured["valid_outcomes"] == catalog.GARAGE_VALID_OUTCOMES
