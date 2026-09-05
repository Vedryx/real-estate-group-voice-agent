"""Per-call session state (AgentSession userdata) for the salon vertical.

Mirrors agent/state_clinic.py's shape: an inbound caller phoning a salon is
already asking for an appointment, so there's no sales-style buying-signal
gate before offering one (contrast Canopy's cta_ready). Tracks what's been
captured so the agent doesn't re-ask, plus enough state to log an outcome.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from agent.salon import SalonKnowledge

SUPPORTED_LANGUAGE_CODES = frozenset({"hi-IN", "mr-IN", "en-IN"})

INTEREST_STATUSES = frozenset(
    {
        "unknown",
        "active",
        "casual",
        "not_interested",
        "wrong_person",
        "opted_out",
    }
)
DEPARTMENT_INTERESTS = frozenset({"Hair", "Skin", "Nails", "Makeup"})
NEXT_STEPS = frozenset(
    {"none", "appointment_requested", "callback_requested", "future_followup", "no_followup"}
)

_TERMINAL_INTEREST = {
    "not_interested": "Caller said they are no longer interested",
    "wrong_person": "The contacted person is not the intended customer",
    "opted_out": "Caller requested no further contact",
}


@dataclass
class SalonCallUserdata:
    knowledge: SalonKnowledge

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
    department_interest: str | None = None  # "Hair" / "Skin" / "Nails" / "Makeup"
    preferred_stylist: str | None = None
    service_interest: str | None = None  # free text, e.g. "haircut", "bridal makeup"
    first_time_chemical_service: bool = False  # drives the one-time patch-test mention

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
        never spoken aloud). No sales 'temperature' — a salon appointment
        request either happened or it didn't."""
        if self.interest_status in _TERMINAL_INTEREST:
            reason = _TERMINAL_INTEREST[self.interest_status]
            status = (
                "do_not_contact"
                if self.interest_status == "opted_out"
                else "wrong_or_invalid"
                if self.interest_status == "wrong_person"
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
