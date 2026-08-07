"""Lightweight hand-off agent for human escalation.

A separate module (not part of assistant.py or tools/catalog.py) so both can
import it without a circular dependency: the escalate_to_human tool returns an
instance of this to trigger the LiveKit multi-agent handoff.
"""

from __future__ import annotations

from livekit.agents import Agent


class HumanEscalationAgent(Agent):
    """Takes over once escalate_to_human fires. No tools of its own — its only
    job is to close the call warmly and signal that a human will follow up. It
    never invents a live-transfer capability that isn't wired up (SIP REFER warm
    transfer is a later configuration item; this hand-off is a scripted, logged
    callback, not a live transfer).
    """

    def __init__(self, *, reason: str) -> None:
        super().__init__(
            instructions=(
                "You are Paranjape's assistant for The Canopy, in the middle of handing this "
                "call off to a human colleague because: "
                f"{reason}\n\n"
                "Speak once: acknowledge briefly, confirm a member of the Paranjape team will "
                "follow up, and thank the caller. Keep it short and warm. Do not try to resolve "
                "the underlying issue yourself, negotiate price, or make any commitment on "
                "Paranjape's behalf. If the caller keeps talking, keep responses brief and "
                "reassuring — a human will take it from here."
            ),
        )
        self._reason = reason

    async def on_enter(self) -> None:
        self.session.generate_reply(
            instructions=(
                "Briefly acknowledge you're connecting them with a member of the Paranjape team "
                "who will follow up shortly, and thank them for calling."
            )
        )
