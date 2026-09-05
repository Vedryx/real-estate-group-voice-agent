"""Primary agent for the auto-garage vertical: a single Agent holding all
Prime Auto Garage & Rentals booking tools.

Standalone sibling of agent/assistant_clinic.py / agent/assistant_salon.py —
identical mechanics (s2s gets full instructions at construction and never
re-injects them per turn; cascade rebuilds instructions each turn), pointed
at agent/persona_garage.py and tools/catalog_garage.py instead.
"""

from __future__ import annotations

from livekit.agents import Agent, llm

from agent.persona_garage import OPENER, instructions_for_call, instructions_for_language
from tools.catalog_garage import ALL_TOOLS


class GarageAssistant(Agent):
    def __init__(self, instructions: str | None = None) -> None:
        super().__init__(
            instructions=instructions or instructions_for_language(None),
            tools=ALL_TOOLS,
        )

    def _is_s2s(self) -> bool:
        return getattr(self.session, "tts", None) is None

    async def on_user_turn_completed(
        self, turn_ctx: llm.ChatContext, new_message: llm.ChatMessage
    ) -> None:
        if not self._is_s2s():
            await self.update_instructions(instructions_for_call(self.session.userdata))

    async def on_enter(self) -> None:
        if self._is_s2s():
            handle = self.session.generate_reply(
                instructions=f"Start the call now. Say exactly this and nothing else: {OPENER}"
            )
            try:
                await handle
            except Exception:  # noqa: BLE001 - fall back silently, see CanopyAssistant
                pass
            return
        await self.update_instructions(instructions_for_call(self.session.userdata))
        self.session.say(OPENER, allow_interruptions=False)
