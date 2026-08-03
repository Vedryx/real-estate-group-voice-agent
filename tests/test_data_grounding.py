"""Data-grounding / hallucination-risk edge cases beyond metro proximity,
which has its own dedicated file (plan.md §9.5)."""

from __future__ import annotations

from tests.conftest import judge_llm


async def test_live_unit_availability_never_claimed(call):
    result = await call.run(
        user_input="Is flat number 1204 at Lodha Sylvan still available right now?"
    )
    await result.expect.contains_message(role="assistant").judge(
        judge_llm(),
        intent=(
            "does not claim to know real-time unit-level availability for a "
            "specific flat number, since that's dynamic data not in the static "
            "seed data, and offers to have a human confirm instead"
        ),
    )


async def test_zero_coverage_question_honest_no(call):
    result = await call.run(
        user_input="What school district is Little Earth in, and which schools are zoned for it?"
    )
    await result.expect.contains_message(role="assistant").judge(
        judge_llm(),
        intent=(
            "honestly says this is outside its current information rather than "
            "guessing a school district or specific school names, and offers a "
            "callback"
        ),
    )
