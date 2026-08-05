"""Pure unit tests for the deterministic tool logic - no LLM, no session,
no skip condition. These exercise the actual filtering/matching code paths
directly, since @function_tool leaves the wrapped function callable with
its original signature (FunctionTool.__call__ just delegates to it).

Complements the LLM-driven tests elsewhere in this directory, which check
that the *model* chooses the right tool/response; these check that the
tools themselves compute the right answer once called.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent.data_store import DataStore
from agent.state import CallUserdata
from tools import catalog


@pytest.fixture
def ctx():
    userdata = CallUserdata(data_store=DataStore.load(), caller_phone="+919812345678")
    return SimpleNamespace(userdata=userdata)


async def test_search_projects_filters_by_city_bhk_budget(ctx):
    result = await catalog.search_projects(ctx, city="Pune", bhk="2BHK", budget_max_lakh=90)
    assert result["served"] is True
    ids = {m["id"] for m in result["matches"]}
    assert "aikyam" in ids  # 2BHK options at 78/82/88L fit under 90L
    assert "lodha-panache" not in ids  # cheapest 2BHK there is 130L


async def test_search_projects_filters_by_developer(ctx):
    result = await catalog.search_projects(
        ctx,
        city="Pune",
        bhk="2BHK",
        budget_max_lakh=72,
        developer="Kolte Patil",
    )

    assert [match["id"] for match in result["matches"]] == [
        "little-earth-kolte-patil"
    ]
    assert ctx.userdata.developer_preference == "Kolte Patil"


async def test_search_projects_preserves_constraints_until_explicitly_relaxed(ctx):
    first = await catalog.search_projects(
        ctx,
        city="Pune",
        locality="Hinjewadi",
        bhk="2BHK",
        budget_max_lakh=72,
        developer="Kolte Patil",
    )
    assert first["matches"] == []

    still_constrained = await catalog.search_projects(ctx, city="Pune")
    assert still_constrained["matches"] == []

    broadened = await catalog.search_projects(
        ctx, city="Pune", relax_locality=True
    )
    assert [match["id"] for match in broadened["matches"]] == [
        "little-earth-kolte-patil"
    ]


async def test_search_summary_uses_requested_bhk_price(ctx):
    result = await catalog.search_projects(
        ctx, city="Pune", bhk="2BHK", developer="Lodha"
    )
    panache = next(match for match in result["matches"] if match["id"] == "lodha-panache")
    assert panache["starting_price_inr_lakh"] == 130
    assert panache["starting_price_display"] == "1 crore 30 lakh"


async def test_search_projects_unserved_city(ctx):
    result = await catalog.search_projects(ctx, city="Chennai")
    assert result["served"] is False
    assert result["matches"] == []


async def test_search_projects_commercial_returns_no_matches(ctx):
    result = await catalog.search_projects(ctx, city="Pune", property_type="commercial")
    assert result["matches"] == []
    assert "no confirmed standalone commercial" in result["message"].lower()


async def test_get_pricing_flags_raheja_vistas_unverified(ctx):
    result = await catalog.get_pricing(ctx, project_id="raheja-vistas")
    assert "message" in result  # unverified hedge present


async def test_get_pricing_no_hedge_for_clean_project(ctx):
    result = await catalog.get_pricing(ctx, project_id="lodha-sylvan")
    assert "message" not in result


async def test_check_metro_proximity_confirmed_only_for_sylvan(ctx):
    sylvan = await catalog.check_metro_proximity(ctx, project_id="lodha-sylvan")
    assert sylvan["confirmed"] is True

    aikyam = await catalog.check_metro_proximity(ctx, project_id="aikyam")
    assert aikyam["confirmed"] is False


async def test_get_amenities_full_list_vs_highlights_only(ctx):
    full = await catalog.get_amenities(ctx, project_id="codename-roots")
    assert full["confirmed_full_list"] is True

    highlights_only = await catalog.get_amenities(ctx, project_id="aikyam")
    assert highlights_only["confirmed_full_list"] is False


async def test_get_nearby_projects_to_workplace_hinjewadi(ctx):
    result = await catalog.get_nearby_projects_to_workplace(ctx, workplace_area="Hinjewadi")
    assert result["matched_hub"] is True
    ids = {m["id"] for m in result["matches"]}
    assert {"lodha-panache", "lodha-magnus", "lodha-sylvan"} <= ids


async def test_get_nearby_projects_to_workplace_no_nearby_listings(ctx):
    result = await catalog.get_nearby_projects_to_workplace(ctx, workplace_area="Kharadi")
    assert result["matched_hub"] is True
    assert result["matches"] == []


async def test_get_nearby_projects_to_workplace_unknown_area(ctx):
    result = await catalog.get_nearby_projects_to_workplace(ctx, workplace_area="Timbuktu")
    assert result["matched_hub"] is False


async def test_log_lead_and_log_out_of_area_interest_roundtrip(ctx, tmp_path, monkeypatch):
    from agent import data_store as ds

    monkeypatch.setattr(ds, "LEADS_LOG_PATH", tmp_path / "leads.jsonl")

    result = await catalog.log_lead(ctx, outcome="callback_requested", name="Test User")
    assert result["logged"] is True
    assert (tmp_path / "leads.jsonl").exists()

    lines = (tmp_path / "leads.jsonl").read_text().strip().splitlines()
    assert len(lines) == 1

    result2 = await catalog.log_out_of_area_interest(ctx, requested_city_or_locality="Chennai")
    assert result2["logged"] is True
    lines = (tmp_path / "leads.jsonl").read_text().strip().splitlines()
    assert len(lines) == 2


async def test_log_lead_rejects_invalid_outcome(ctx):
    from livekit.agents.llm import ToolError

    with pytest.raises(ToolError):
        await catalog.log_lead(ctx, outcome="not_a_real_outcome")


async def test_record_qualification_and_site_visit_persist_hot_lead(
    ctx, tmp_path, monkeypatch
):
    from agent import data_store as ds
    import json

    monkeypatch.setattr(ds, "LEADS_LOG_PATH", tmp_path / "leads.jsonl")
    await catalog.record_lead_qualification(
        ctx,
        interest_status="active",
        purchase_timeline="within_3_months",
        purchase_purpose="self_use",
    )
    await catalog.search_projects(
        ctx, city="Pune", bhk="2BHK", budget_max_lakh=90
    )
    result = await catalog.schedule_site_visit(
        ctx,
        project_id="aikyam",
        preferred_date="Saturday afternoon",
        name="Test User",
        phone="+919812345678",
    )

    assert result["status"] == "requested_pending_human_confirmation"
    assert result["qualification"]["lead_temperature"] == "hot"
    record = json.loads((tmp_path / "leads.jsonl").read_text().strip())
    assert record["qualification_status"] == "qualified"
    assert record["lead_temperature"] == "hot"
    assert record["next_step"] == "site_visit_requested"


async def test_opt_out_overrides_qualification_and_disables_contact(
    ctx, tmp_path, monkeypatch
):
    from agent import data_store as ds
    import json

    monkeypatch.setattr(ds, "LEADS_LOG_PATH", tmp_path / "leads.jsonl")
    await catalog.log_lead(
        ctx,
        outcome="opted_out",
        consent_to_be_contacted=False,
        notes="Caller requested no further contact",
    )

    record = json.loads((tmp_path / "leads.jsonl").read_text().strip())
    assert record["interest_status"] == "opted_out"
    assert record["qualification_status"] == "do_not_contact"
    assert record["lead_temperature"] == "no_opportunity"
    assert record["consent_to_be_contacted"] is False
