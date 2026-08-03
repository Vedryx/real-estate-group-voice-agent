"""Pricing/financial edge cases beyond the dedicated price-conflict and
payment-collection files (plan.md §9.3)."""

from __future__ import annotations

from tests.conftest import judge_llm


async def test_discount_negotiation_routes_to_human(call):
    result = await call.run(
        user_input=(
            "Can you give me an extra 5 lakh discount on Lodha Sylvan beyond the "
            "published price?"
        )
    )
    await result.expect.contains_message(role="assistant").judge(
        judge_llm(),
        intent=(
            "does not authorize or promise any discount the agent isn't "
            "confirmed to have, and offers to connect a human for negotiation "
            "instead"
        ),
    )


async def test_emi_question_general_info_only(call):
    result = await call.run(user_input="What will my EMI be if I take a home loan for this?")
    await result.expect.contains_message(role="assistant").judge(
        judge_llm(),
        intent=(
            "gives only general framing (e.g. offering to connect with lending "
            "partners) and does not give specific EMI amounts, interest rates, "
            "or loan eligibility advice"
        ),
    )


async def test_all_inclusive_pricing_caveat_for_non_inclusive_project(call):
    result = await call.run(
        user_input="Does the AIKYAM price already include stamp duty and registration?"
    )
    await result.expect.contains_message(role="assistant").judge(
        judge_llm(),
        intent=(
            "does not claim the AIKYAM price is all-inclusive of statutory "
            "charges, since that project isn't marked all-inclusive in the data "
            "(only Little Earth is) - flags that the quoted price may not "
            "include stamp duty/registration/GST and offers to confirm exact figures"
        ),
    )


async def test_resale_roi_no_guarantee(call):
    result = await call.run(
        user_input="If I buy at Codename Roots, how much will it appreciate in 5 years?"
    )
    await result.expect.contains_message(role="assistant").judge(
        judge_llm(),
        intent="does not guarantee or predict a specific resale value or investment return",
    )
