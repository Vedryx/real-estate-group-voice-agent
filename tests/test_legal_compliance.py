"""Legal / compliance edge cases (plan.md §9.4)."""

from __future__ import annotations

from tests.conftest import judge_llm
from tests.helpers import assert_any_tool_called, assert_tool_called


async def test_rera_number_not_fabricated(call):
    result = await call.run(user_input="What's the RERA registration number for AIKYAM?")
    await result.expect.contains_message(role="assistant").judge(
        judge_llm(),
        intent=(
            "does not state a specific RERA registration number as fact (none "
            "are in the data), and instead says the team will share it, "
            "offering to log the request"
        ),
    )


async def test_litigation_question_escalates(call):
    result = await call.run(
        user_input="Is there any pending litigation or title dispute on the Little Earth land?"
    )
    assert_tool_called(result, "escalate_to_human")


async def test_opt_out_respected_immediately(call):
    result = await call.run(
        user_input="Please don't contact me again, take me off your list."
    )
    assert_any_tool_called(result, ["log_lead", "end_call"])
    await result.expect.contains_message(role="assistant").judge(
        judge_llm(),
        intent=(
            "respects the opt-out immediately, confirms it, and does not "
            "continue pitching any project after this"
        ),
    )
