"""Bilingual smoke tests - same scenarios as the English tests, run with
Hindi/Hinglish input, to confirm tool-calling still triggers correctly
regardless of input language (plan.md §11). The LLM reasons in text and
Sarvam handles the voice ends in production, so these exercise the LLM's
tool-calling reliability on Hindi/Hinglish text, not TTS/STT quality.
"""

from __future__ import annotations

from tests.helpers import assert_tool_called, assert_tool_not_called


async def test_hinglish_search_triggers_search_projects(call):
    result = await call.run(
        user_input="Mujhe Pune mein Hinjewadi ke paas 2 BHK chahiye, budget 1 crore tak hai."
    )
    assert_tool_called(result, "search_projects")


async def test_hindi_rera_question_does_not_fabricate(call):
    result = await call.run(user_input="Codename Roots ka RERA number kya hai?")
    # Should not claim a specific number - no tool exposes a real RERA number,
    # so no path should let the model state one as fact.
    assert_tool_not_called(result, "search_projects")


async def test_hinglish_out_of_city_pivot(call):
    result = await call.run(
        user_input="Sir mera Mumbai shift ho raha hai, wahan koi property hai kya aapke paas?"
    )
    assert_tool_called(result, "log_out_of_area_interest")
    assert_tool_not_called(result, "search_projects")
