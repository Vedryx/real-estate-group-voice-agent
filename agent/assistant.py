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
    def __init__(self, instructions: str | None = None) -> None:
        # In s2s the full persona+brief instructions are passed at construction
        # (see worker), because calling update_instructions() on the Gemini
        # realtime session BEFORE it is active triggers a reconnect that makes
        # the opener's generate_reply() time out. Cascade builds them per turn.
        super().__init__(
            instructions=instructions or instructions_for_language(None),
            tools=ALL_TOOLS,
        )

    def _is_s2s(self) -> bool:
        # No TTS on the session == Gemini RealtimeModel (speech-to-speech).
        return getattr(self.session, "tts", None) is None

    async def on_user_turn_completed(
        self, turn_ctx: llm.ChatContext, new_message: llm.ChatMessage
    ) -> None:
        # D4: update qualification from the caller's words outside the tool loop,
        # then rebuild instructions with the Layer-0 brief + refreshed call state.
        update_qualification_from_text(
            self.session.userdata, getattr(new_message, "text_content", "") or ""
        )
        # S2S: do NOT re-inject instructions per turn. The full persona+brief is
        # already set at construction, and re-sending ~1800 tokens every turn
        # blows the native-audio model's small context window (a call died with
        # 1007 "context exhausted"). Cascade rebuilds cheaply, so keep it there.
        if not self._is_s2s():
            await self.update_instructions(instructions_for_call(self.session.userdata))

    async def on_enter(self) -> None:
        if self._is_s2s():
            # Instructions were set at construction. Do NOT update_instructions
            # here (pre-active-session update => reconnect => greeting timeout).
            # Drive the opener through the model; it reproduces the fixed line.
            handle = self.session.generate_reply(
                instructions=f"Start the call now. Say exactly this and nothing else: {OPENER}"
            )
            try:
                await handle
            except Exception:  # noqa: BLE001 - if the greeting races connect setup,
                # fall back silently; the model still greets on the first user turn.
                pass
            return
        # Cascade: load brief + state, then speak the fixed opener via say() —
        # drops the cold first-token LLM latency from the greeting (C4).
        await self.update_instructions(instructions_for_call(self.session.userdata))
        self.session.say(OPENER, allow_interruptions=False)
