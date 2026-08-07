"""Primary agent: a single Agent holding all Canopy sales tools.

One specialist agent plus the human hand-off in escalation_agent.py is enough
for a single-project sales call. Each turn the instructions are rebuilt from
persona + Layer-0 working brief + current call state (see persona.py).
"""

from __future__ import annotations

from livekit.agents import Agent, llm

from agent.persona import instructions_for_call, instructions_for_language
from tools.catalog import ALL_TOOLS


class CanopyAssistant(Agent):
    def __init__(self) -> None:
        super().__init__(instructions=instructions_for_language(None), tools=ALL_TOOLS)

    async def on_user_turn_completed(
        self, turn_ctx: llm.ChatContext, new_message: llm.ChatMessage
    ) -> None:
        # Rebuild instructions with the Layer-0 brief + refreshed call state.
        await self.update_instructions(instructions_for_call(self.session.userdata))

    async def on_enter(self) -> None:
        # Load the working brief + state into the prompt before the greeting.
        await self.update_instructions(instructions_for_call(self.session.userdata))
        # allow_interruptions=False on the opener: a zero-word noise blip during
        # SIP call setup would otherwise cancel the greeting before it plays.
        # Normal turns after this still allow natural barge-in.
        self.session.generate_reply(allow_interruptions=False)
