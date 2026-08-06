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

from agent.canopy import CanopyKnowledge
from agent.state import CallUserdata
from tools import catalog


@pytest.fixture
def ud() -> CallUserdata:
    return CallUserdata(knowledge=CanopyKnowledge.load(), caller_phone="+919812345678")


@pytest.fixture
def ctx(ud):
    return SimpleNamespace(userdata=ud)


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
    result = await catalog.schedule_site_visit(
        ctx, preferred_date="this Saturday", name="Asha"
    )
    assert result["logged"] is True
    assert result["status"] == "requested_pending_team_confirmation"
    assert ctx.userdata.next_step == "site_visit_requested"
    assert ctx.userdata.lead_logged is True


async def test_log_callback_logs_and_sets_next_step(ctx):
    result = await catalog.log_callback(ctx, name="Ravi", notes="prefers evening")
    assert result["logged"] is True
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
