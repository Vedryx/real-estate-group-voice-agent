"""Primary agent: a single Agent holding all real-estate tools (plan.md
§3.3). The conversation isn't complex enough to need more than one
specialist agent plus the escalation hand-off in escalation_agent.py.
"""

from __future__ import annotations

from livekit.agents import Agent

from agent.persona import INSTRUCTIONS
from tools.catalog import ALL_TOOLS


class RealEstateGroupAssistant(Agent):
    def __init__(self) -> None:
        super().__init__(instructions=INSTRUCTIONS, tools=ALL_TOOLS)

    async def on_enter(self) -> None:
        # allow_interruptions=False: without this, a 2026-08-03 observability
        # export showed the opening greeting getting cancelled ~1.7s in on
        # its very first LLM request, before ever playing - the default
        # interruption.min_words=0 means even a zero-word noise blip during
        # SIP call setup (comfort noise, connect artifacts) is enough to
        # cancel it, leaving the call silent until the caller spoke first
        # and only then triggered a response. A short opening greeting is a
        # reasonable thing to make non-interruptible; normal conversation
        # turns afterward still allow natural barge-in.
        self.session.generate_reply(allow_interruptions=False)
