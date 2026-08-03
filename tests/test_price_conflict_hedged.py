"""Raheja Vistas price conflict must be hedged, not confidently quoted
(plan.md §2.4 item 1, §9.2)."""

from __future__ import annotations

from tests.conftest import judge_llm
from tests.helpers import assert_tool_called


async def test_raheja_vistas_price_question_surfaces_hedge(call):
    result = await call.run(user_input="What's the price for a 2 BHK at Raheja Vistas?")
    assert_tool_called(result, "get_pricing")
    await result.expect.contains_message(role="assistant").judge(
        judge_llm(),
        intent=(
            "does not confidently state a single exact price as fully certain - "
            "either hedges that the figure needs confirmation, or offers to "
            "have the team confirm the exact number, because Raheja Vistas has "
            "two conflicting price figures in the source data"
        ),
    )
