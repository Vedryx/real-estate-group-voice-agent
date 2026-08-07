"""Primary agent: a single Agent holding all Canopy sales tools.

One specialist agent plus the human hand-off in escalation_agent.py is enough
for a single-project sales call. Each turn the instructions are rebuilt from
persona + Layer-0 working brief + current call state (see persona.py).
"""

from __future__ import annotations

from livekit.agents import Agent, llm

from agent.persona import OPENER, instructions_for_call, instructions_for_language
from agent.state import update_qualification_from_text
from tools.catalog import ALL_TOOLS


class CanopyAssistant(Agent):
    def __init__(self) -> None:
        super().__init__(instructions=instructions_for_language(None), tools=ALL_TOOLS)

    async def on_user_turn_completed(
        self, turn_ctx: llm.ChatContext, new_message: llm.ChatMessage
    ) -> None:
        # D4: update qualification from the caller's words outside the tool loop,
        # then rebuild instructions with the Layer-0 brief + refreshed call state.
        update_qualification_from_text(
            self.session.userdata, getattr(new_message, "text_content", "") or ""
        )
        await self.update_instructions(instructions_for_call(self.session.userdata))

    async def on_enter(self) -> None:
        # Load the working brief + state into the prompt for the turns that follow.
        await self.update_instructions(instructions_for_call(self.session.userdata))
        # Speak the opener FIRST (outbound call — agent must open, not wait).
        if self.session.tts is None:
            # S2S / RealtimeModel path: there is no TTS to feed say() (it raises
            # "RealtimeSession that supports say()"), so drive the greeting through
            # the model itself. It reproduces the fixed opener verbatim.
            self.session.generate_reply(
                instructions=f"Start the call now. Say exactly this and nothing else: {OPENER}"
            )
        else:
            # C4 (cascade): the opener is a FIXED line, so speak it directly via
            # say() instead of generate_reply() — drops the cold first-token LLM
            # latency from the greeting. allow_interruptions=False keeps SIP
            # call-setup noise from cancelling it before it plays.
            self.session.say(OPENER, allow_interruptions=False)
