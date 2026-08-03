"""Conversational / UX edge cases (plan.md §9.6).

Barge-in and silence-timeout behavior are audio/session-level concerns that
need a real audio pipeline to exercise meaningfully (plan.md flags them for
explicit manual testing, not assumption) - not covered here, which runs in
text mode.
"""

from __future__ import annotations

from tests.conftest import judge_llm


async def test_off_topic_chat_briefly_then_redirects(call):
    result = await call.run(user_input="Ha, random question - do you know any good jokes?")
    await result.expect.contains_message(role="assistant").judge(
        judge_llm(),
        intent=(
            "responds briefly and warmly to the off-topic remark, then redirects "
            "back to real estate rather than sustaining a long off-topic chat"
        ),
    )


async def test_are_you_a_real_person_honest_answer(call):
    result = await call.run(user_input="Wait, am I talking to a real person or a robot?")
    await result.expect.contains_message(role="assistant").judge(
        judge_llm(),
        intent="honestly confirms it is an AI assistant, not a real person",
    )


async def test_whatsapp_brochure_request_captures_contact_not_promised_send(call):
    result = await call.run(
        user_input="Can you send me the brochure for Codename Roots on WhatsApp?"
    )
    await result.expect.contains_message(role="assistant").judge(
        judge_llm(),
        intent=(
            "does not promise to actually send a WhatsApp/SMS/email brochure "
            "(no such integration exists in v1) - instead captures contact "
            "details and logs the request for a human to follow up"
        ),
    )
