"""Human hand-off agent for the clinic vertical.

Standalone sibling of agent/escalation_agent.py (that one hardcodes
"Paranjape"/"The Canopy" in its spoken instructions, so it can't be reused
as-is). Also the landing point for the persona's EMERGENCY rule — the
caller has already been told to call an ambulance/go to the ER by this
point; this agent's job is just the warm hand-off, not further triage.
"""

from __future__ import annotations

from livekit.agents import Agent


class ClinicHumanEscalationAgent(Agent):
    """Takes over once escalate_to_human fires. No tools of its own — its only
    job is to close the call warmly and signal that the front desk will
    follow up (or, for an emergency, that the caller has already been told
    to seek immediate care)."""

    def __init__(self, *, reason: str) -> None:
        super().__init__(
            instructions=(
                "You are Wellness Point Clinic's assistant, in the middle of handing this call off "
                f"to a human colleague because: {reason}\n\n"
                "Speak once: acknowledge briefly, confirm a member of the front desk team will "
                "follow up, and thank the caller. Keep it short and warm. Do not attempt any "
                "clinical assessment, do not diagnose, and do not make any commitment on the "
                "clinic's behalf beyond a follow-up call. If the caller keeps talking, keep "
                "responses brief and reassuring — a human will take it from here."
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
