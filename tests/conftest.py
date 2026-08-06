"""Shared fixtures for the pytest-based agent behavior tests (plan.md §11).

These tests exercise real LLM tool-calling decisions - LiveKit's testing
harness (AgentSession.run + result.expect...) is designed to validate that
the model actually chooses the right tool/response given a live LLM, not a
scripted mock. The LLM runs through LiveKit Cloud's inference gateway
(livekit.agents.inference.LLM), authenticated with LIVEKIT_API_KEY/SECRET -
the same credentials the worker itself needs, no separate provider key.
Tests are skipped automatically when those aren't set, so a bare `pytest`
run stays green without credentials.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from livekit.agents import AgentSession, inference

from agent import data_store as ds
from agent.assistant import CanopyAssistant
from agent.canopy import CanopyKnowledge
from agent.state import CallUserdata

TEST_LLM_MODEL = os.getenv("TEST_LLM_MODEL", "openai/gpt-4.1-mini")


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if os.getenv("LIVEKIT_API_KEY") and os.getenv("LIVEKIT_API_SECRET"):
        return
    skip = pytest.mark.skip(
        reason=(
            "requires LIVEKIT_API_KEY/LIVEKIT_API_SECRET - these tests exercise "
            "live LLM tool-calling decisions via LiveKit's inference gateway, "
            "not a mock (plan.md §11)"
        )
    )
    for item in items:
        # Only skip tests that actually drive a live LLM session (the `call`
        # fixture) - pure tool-logic unit tests don't need credentials.
        if "call" in getattr(item, "fixturenames", ()):
            item.add_marker(skip)


def judge_llm() -> inference.LLM:
    """LLM instance used for RunAssert.judge(...) semantic checks."""
    return inference.LLM(model=TEST_LLM_MODEL)


@pytest.fixture(autouse=True)
def isolate_leads_log(tmp_path, monkeypatch):
    """Every test writes leads to a throwaway file, never the real leads.jsonl."""
    monkeypatch.setattr(ds, "LEADS_LOG_PATH", tmp_path / "leads.jsonl")


@pytest_asyncio.fixture
async def call() -> AsyncIterator[AgentSession[CallUserdata]]:
    """A fresh, started assistant session with a clean CallUserdata per test."""
    userdata = CallUserdata(
        knowledge=CanopyKnowledge.load(), caller_phone="+919812345678"
    )
    session = AgentSession[CallUserdata](
        userdata=userdata,
        llm=inference.LLM(model=TEST_LLM_MODEL),
    )
    await session.start(agent=CanopyAssistant())
    try:
        yield session
    finally:
        await session.aclose()
