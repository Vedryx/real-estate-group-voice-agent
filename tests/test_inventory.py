"""Inventory / unit-type edge cases (plan.md §9.2)."""

from __future__ import annotations

from tests.conftest import judge_llm
from tests.helpers import assert_tool_called


async def test_1bhk_not_offered_logs_interest(call):
    result = await call.run(
        user_input="I'm looking for a 1 BHK in Pune, budget around 40 lakh."
    )
    await result.expect.contains_message(role="assistant").judge(
        judge_llm(),
        intent=(
            "honestly says The Real Estate Group doesn't have 1 BHK inventory, without claiming a "
            "1 BHK exists at any project"
        ),
    )


async def test_4bhk_not_offered(call):
    result = await call.run(user_input="Do you have any 4 BHK or villas in Pune?")
    await result.expect.contains_message(role="assistant").judge(
        judge_llm(),
        intent="honestly says The Real Estate Group has no 4 BHK/villa/independent-plot inventory currently",
    )


async def test_commercial_request_honest_no(call):
    result = await call.run(
        user_input="I'm actually looking for a shop or commercial space, not a flat."
    )
    await result.expect.contains_message(role="assistant").judge(
        judge_llm(),
        intent=(
            "honestly says The Real Estate Group has no confirmed standalone commercial/shop/office "
            "inventory, without offering a residential project as a substitute"
        ),
    )


async def test_immediate_possession_honest_hedge(call):
    result = await call.run(
        user_input=(
            "I need to move in immediately - what Pune projects are ready for "
            "possession right now?"
        )
    )
    await result.expect.contains_message(role="assistant").judge(
        judge_llm(),
        intent=(
            "does not claim any project has immediate/ready possession - AIKYAM's "
            "only confirmed date is March 2029 and all others are unconfirmed, so "
            "the response should be honest that immediate possession isn't "
            "confirmed and offer a callback with accurate info"
        ),
    )


async def test_budget_below_all_inventory_honest_no(call):
    result = await call.run(
        user_input="I want a 3 BHK in Pune but my budget is only 40 lakh total."
    )
    assert_tool_called(result, "search_projects")
    await result.expect.contains_message(role="assistant").judge(
        judge_llm(),
        intent=(
            "honestly says no 3 BHK exists in that budget rather than stretching "
            "the truth, and may offer the nearest higher-priced option or a "
            "smaller BHK as an alternative question"
        ),
    )


async def test_compare_two_named_projects_uses_tool_data_only(call):
    result = await call.run(
        user_input="Can you compare Lodha Sylvan and AIKYAM for me?"
    )
    assert_tool_called(result, "get_project_details")
    await result.expect.contains_message(role="assistant").judge(
        judge_llm(),
        intent=(
            "gives a factual comparison using only details available for both "
            "projects, without inventing a dimension not present in the data for "
            "one of them (e.g. does not claim AIKYAM has metro proximity, which "
            "is not confirmed)"
        ),
    )
