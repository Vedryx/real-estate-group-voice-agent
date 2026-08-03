"""Escalation / human hand-off edge cases (plan.md §9.7, §3.3)."""

from __future__ import annotations

from tests.conftest import judge_llm
from tests.helpers import assert_tool_called


async def test_explicit_human_request_escalates_immediately(call):
    result = await call.run(user_input="I don't want to talk to a bot, get me a human right now.")
    assert_tool_called(result, "escalate_to_human")
    result.expect.contains_agent_handoff()


async def test_angry_caller_deescalates_and_logs_complaint(call):
    result = await call.run(
        user_input=(
            "I'm really upset - your team promised me a callback last week about "
            "Lodha Panache and nobody ever called. This is unacceptable."
        )
    )
    assert_tool_called(result, "escalate_to_human")


async def test_does_not_argue_with_upset_caller(call):
    result = await call.run(
        user_input="This is the third time I've called and gotten nowhere, this is a joke."
    )
    await result.expect.contains_message(role="assistant").judge(
        judge_llm(),
        intent=(
            "de-escalates calmly and does not argue or get defensive with the "
            "frustrated caller"
        ),
    )
