"""Human hand-off agent for the auto-garage vertical.

Standalone sibling of the other verticals' escalation agents (each hardcodes
its own project name in the spoken instructions, so they can't be reused as
-is). Also the landing point for the persona's ROADSIDE EMERGENCY rule — the
caller has already been told to contact roadside assistance/their insurer by
this point; this agent's job is just the warm hand-off, not further triage.
"""

from __future__ import annotations

from livekit.agents import Agent


class GarageHumanEscalationAgent(Agent):
    """Takes over once escalate_to_human fires. No tools of its own — its only
    job is to close the call warmly and signal that the team will follow up
    (or, for a roadside emergency, that the caller has already been told to
    seek help elsewhere)."""

    def __init__(self, *, reason: str) -> None:
        super().__init__(
            instructions=(
                "You are Prime Auto Garage & Rentals' assistant, in the middle of handing this call "
                f"off to a human colleague because: {reason}\n\n"
                "Speak once: acknowledge briefly, confirm a member of the team will follow up, and "
                "thank the caller. Keep it short and warm. Do not make any commitment on the "
                "garage's behalf beyond a follow-up call. If the caller keeps talking, keep "
                "responses brief and reassuring — a human will take it from here."
            ),
        )
        self._reason = reason

    async def on_enter(self) -> None:
        self.session.generate_reply(
            instructions=(
                "Briefly acknowledge you're connecting them with a member of the team who will "
                "follow up shortly, and thank them for calling."
            )
        )
