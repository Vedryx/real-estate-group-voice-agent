"""Per-call session state (AgentSession userdata) for the clinic vertical.

Simpler than agent/state.py's CallUserdata: an inbound caller phoning a
clinic is already asking for an appointment, so there's no sales-style
buying-signal gate before offering one (contrast Canopy's cta_ready, built
for an outbound sales call where offering too early was the bug). This
module just tracks what's been captured so the agent doesn't re-ask, plus
enough state to log a sensible outcome.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from agent.clinic import ClinicKnowledge

SUPPORTED_LANGUAGE_CODES = frozenset({"hi-IN", "mr-IN", "en-IN"})

INTEREST_STATUSES = frozenset(
    {
        "unknown",
        "active",
        "casual",
        "not_interested",
        "wrong_person",
        "opted_out",
        "emergency",
    }
)
PATIENT_TYPES = frozenset({"unknown", "new", "returning"})
DEPARTMENT_INTERESTS = frozenset({"General Medicine", "Dental"})
NEXT_STEPS = frozenset(
    {"none", "appointment_requested", "callback_requested", "future_followup", "no_followup"}
)

_TERMINAL_INTEREST = {
    "not_interested": "Caller said they are no longer interested",
    "wrong_person": "The contacted person is not the intended patient",
    "opted_out": "Caller requested no further contact",
    "emergency": "Caller described an emergency — routed to emergency services, not scheduled",
}


@dataclass
class ClinicCallUserdata:
    knowledge: ClinicKnowledge

    caller_phone: str | None = None
    caller_name: str | None = None
    preferred_language: str | None = None

    # Telephony/campaign metadata, parsed in worker.py — mostly unused for a
    # plain inbound call but kept for parity with the Canopy dispatcher path
    # (_extract_outbound_lead_context always supplies these keys).
    source_channel: str | None = None
    source_campaign: str | None = None
    source_project: str | None = None
    source_enquiry_id: str | None = None
    consent_reference: str | None = None

    # What the caller has told us.
    interest_status: str = "unknown"
    department_interest: str | None = None  # "General Medicine" / "Dental"
    preferred_doctor: str | None = None
    reason_for_visit: str | None = None  # free text, never a diagnosis
    patient_type: str = "unknown"  # "new" / "returning"

    next_step: str = "none"
    closing_attempted: bool = False

    appointment_offered: bool = False
    appointment_declined: bool = False
    callback_offered: bool = False
    cta_offer_count: int = 0

    lead_logged: bool = False
    write_ack_spoken: bool = False
    logged_outcomes: set[str] = field(default_factory=set)

    @property
    def conversation_stage(self) -> str:
        if self.interest_status in _TERMINAL_INTEREST or self.closing_attempted:
            return "closing"
        if self.interest_status == "unknown":
            return "opening"
        return "engaged"

    def qualification_snapshot(self) -> dict[str, object]:
        """Classify the call from explicit, auditable signals (internal tags,
        never spoken aloud). Deliberately no sales 'temperature' — a clinic
        appointment request either happened or it didn't."""
        if self.interest_status in _TERMINAL_INTEREST:
            reason = _TERMINAL_INTEREST[self.interest_status]
            status = (
                "do_not_contact"
                if self.interest_status == "opted_out"
                else "wrong_or_invalid"
                if self.interest_status == "wrong_person"
                else "emergency_redirected"
                if self.interest_status == "emergency"
                else "not_interested"
            )
            return {"appointment_status": status, "reasons": [reason]}

        if self.next_step == "appointment_requested":
            return {"appointment_status": "appointment_requested", "reasons": ["Caller requested an appointment"]}
        if self.next_step == "callback_requested":
            return {"appointment_status": "callback_requested", "reasons": ["Caller requested a callback"]}
        if self.interest_status == "unknown":
            return {"appointment_status": "incomplete", "reasons": ["Interest not yet confirmed"]}
        return {"appointment_status": "info_only", "reasons": ["Caller asked questions, no appointment booked"]}
