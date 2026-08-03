"""Small assertion helpers not provided directly by RunAssert."""

from __future__ import annotations

from livekit.agents.llm import FunctionCall
from livekit.agents.voice.run_result import RunResult


def called_tools(result: RunResult) -> list[str]:
    return [ev.item.name for ev in result.events if isinstance(ev.item, FunctionCall)]


def assert_tool_not_called(result: RunResult, name: str) -> None:
    calls = called_tools(result)
    assert name not in calls, f"expected {name!r} not to be called, but it was (calls: {calls})"


def assert_tool_called(result: RunResult, name: str) -> None:
    calls = called_tools(result)
    assert name in calls, f"expected {name!r} to be called, but it wasn't (calls: {calls})"


def assert_any_tool_called(result: RunResult, names: list[str]) -> None:
    calls = called_tools(result)
    assert any(n in calls for n in names), (
        f"expected one of {names} to be called, but none were (calls: {calls})"
    )
