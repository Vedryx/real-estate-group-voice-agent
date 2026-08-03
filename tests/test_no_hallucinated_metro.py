"""Metro-proximity grounding (plan.md §9.5, §2.4 item 4).

Only Lodha Sylvan has a confirmed metro-proximity claim in the data. For
every other project, the agent must say "not confirmed" rather than assume
proximity or absence - this is called out in the plan as a common
hallucination risk.
"""

from __future__ import annotations

import pytest

from tests.conftest import judge_llm
from tests.helpers import assert_tool_called

NON_METRO_PROJECT_IDS = [
    "aikyam",
    "lodha-panache",
    "lodha-magnus",
    "raheja-vistas",
    "geo-aristo",
    "codename-roots",
    "skyi-manas-lake-city",
    "little-earth-kolte-patil",
]


async def test_sylvan_metro_proximity_confirmed(call):
    result = await call.run(user_input="Is Lodha Sylvan close to a metro station?")
    assert_tool_called(result, "check_metro_proximity")
    await result.expect.contains_message(role="assistant").judge(
        judge_llm(),
        intent=(
            "affirmatively confirms Lodha Sylvan is close to the metro station "
            "(a 5-minute walk), since that is the one project with confirmed "
            "metro proximity in the data"
        ),
    )


@pytest.mark.parametrize("project_id", NON_METRO_PROJECT_IDS)
async def test_non_sylvan_projects_never_claim_metro_proximity(call, project_id):
    result = await call.run(
        user_input=f"Is the {project_id.replace('-', ' ')} project near a metro station?"
    )
    await result.expect.contains_message(role="assistant").judge(
        judge_llm(),
        intent=(
            "does not affirmatively claim metro proximity for this project - "
            "either says it's not confirmed, or asks a clarifying question, but "
            "never states the project is near/far from a metro station as fact"
        ),
    )
