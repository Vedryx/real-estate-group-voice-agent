"""Location edge cases (plan.md §9.1, §5.1)."""

from __future__ import annotations

from tests.conftest import judge_llm
from tests.helpers import assert_any_tool_called, assert_tool_called, assert_tool_not_called


async def test_out_of_city_no_invented_inventory(call):
    result = await call.run(
        user_input="Hi, I'm moving to Chennai for work, do you have any 2 BHK apartments there?"
    )
    assert_tool_not_called(result, "search_projects")
    assert_tool_called(result, "log_out_of_area_interest")
    await result.expect.contains_message(role="assistant").judge(
        judge_llm(),
        intent=(
            "tells the caller The Real Estate Group does not currently have listings in "
            "Chennai, without inventing or implying any Chennai inventory exists"
        ),
    )


async def test_locality_with_no_project_offers_alternative(call):
    result = await call.run(user_input="I'm looking for a 2 BHK in Pune, specifically in Baner.")
    assert_any_tool_called(result, ["search_projects", "get_nearby_projects_to_workplace"])
    await result.expect.contains_message(role="assistant").judge(
        judge_llm(),
        intent=(
            "does not claim The Real Estate Group has a live project directly in Baner (there is none "
            "in the data), and instead offers the nearest actual project or asks "
            "a clarifying question"
        ),
    )


async def test_future_relocation_still_qualifies(call):
    result = await call.run(
        user_input=(
            "I'm not moving for another year, but I'm curious - what 3 BHK options "
            "does The Real Estate Group have in Pune around Hinjewadi?"
        )
    )
    assert_tool_called(result, "search_projects")


async def test_ambiguous_lodha_project_asks_clarifying_question(call):
    result = await call.run(
        user_input="Can you tell me more about the Lodha project you mentioned?"
    )
    # No prior context narrowed this to one Lodha project (Panache/Magnus/Sylvan
    # are all separate listings) - the agent should ask which one rather than
    # guessing via get_project_details on an assumed id.
    assert_tool_not_called(result, "get_project_details")


async def test_multiple_family_members_can_log_separate_leads(call):
    await call.run(
        user_input=(
            "My parents want a 2 BHK in Pune near Ravet, budget around 80 lakh. "
            "Can you note their number as 9876543210?"
        )
    )
    result2 = await call.run(
        user_input=(
            "Separately, I personally want a 3 BHK in Hinjewadi, budget around 1.5 crore. "
            "My number is 9123456780, please log that as well and I'd like a callback."
        )
    )
    assert_tool_called(result2, "log_lead")
