"""Human hand-off agent for the salon vertical.

Standalone sibling of agent/escalation_agent.py / agent/escalation_agent_
clinic.py (those hardcode their own project name in the spoken instructions,
so they can't be reused as-is).
"""

from __future__ import annotations

from livekit.agents import Agent


class SalonHumanEscalationAgent(Agent):
    """Takes over once escalate_to_human fires. No tools of its own — its only
    job is to close the call warmly and signal that the front desk will
    follow up."""

    def __init__(self, *, reason: str) -> None:
        super().__init__(
            instructions=(
                "You are Aura Salon & Spa's assistant, in the middle of handing this call off to a "
                f"human colleague because: {reason}\n\n"
                "Speak once: acknowledge briefly, confirm a member of the front desk team will "
                "follow up, and thank the caller. Keep it short and warm. Do not make any "
                "commitment on the salon's behalf beyond a follow-up call. If the caller keeps "
                "talking, keep responses brief and reassuring — a human will take it from here."
            ),
        )
        self._reason = reason

    async def on_enter(self) -> None:
        self.session.generate_reply(
            instructions=(
                "Briefly acknowledge you're connecting them with a member of the front desk team "
                "who will follow up shortly, and thank them for calling."
            )
        )
