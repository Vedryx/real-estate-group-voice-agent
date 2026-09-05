"""Per-call session state (AgentSession userdata) for the auto-garage
vertical.

Mirrors agent/state_clinic.py / agent/state_salon.py's shape: an inbound
caller phoning the garage is already asking for a service booking or a
rental, so there's no sales-style buying-signal gate before offering one
(contrast Canopy's cta_ready). Tracks what's been captured across BOTH
departments (Service and Rental use different fields) so the agent doesn't
re-ask, plus enough state to log a sensible outcome.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from agent.garage import GarageKnowledge

SUPPORTED_LANGUAGE_CODES = frozenset({"hi-IN", "mr-IN", "en-IN"})

INTEREST_STATUSES = frozenset(
    {
        "unknown",
        "active",
        "casual",
        "not_interested",
        "wrong_person",
        "opted_out",
        "roadside_emergency",
    }
)
DEPARTMENT_INTERESTS = frozenset({"Service", "Rental"})
NEXT_STEPS = frozenset(
    {"none", "appointment_requested", "callback_requested", "future_followup", "no_followup"}
)

_TERMINAL_INTEREST = {
    "not_interested": "Caller said they are no longer interested",
    "wrong_person": "The contacted person is not the intended customer",
    "opted_out": "Caller requested no further contact",
    "roadside_emergency": "Caller described an active breakdown/accident — routed to roadside assistance, not scheduled",
}


@dataclass
class GarageCallUserdata:
    knowledge: GarageKnowledge

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
    department_interest: str | None = None  # "Service" / "Rental"

    # Service-specific.
    car_make_model: str | None = None
    service_type: str | None = None

    # Rental-specific.
    car_category: str | None = None  # "Hatchback" / "Sedan" / "SUV"
    rental_duration: str | None = None
    self_drive: bool | None = None

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
        never spoken aloud). No sales 'temperature' — a service/rental
        request either happened or it didn't."""
        if self.interest_status in _TERMINAL_INTEREST:
            reason = _TERMINAL_INTEREST[self.interest_status]
            status = (
                "do_not_contact"
                if self.interest_status == "opted_out"
                else "wrong_or_invalid"
                if self.interest_status == "wrong_person"
                else "roadside_emergency_redirected"
                if self.interest_status == "roadside_emergency"
                else "not_interested"
            )
            return {"appointment_status": status, "reasons": [reason]}

        if self.next_step == "appointment_requested":
            return {"appointment_status": "appointment_requested", "reasons": ["Caller requested a service/rental booking"]}
        if self.next_step == "callback_requested":
            return {"appointment_status": "callback_requested", "reasons": ["Caller requested a callback"]}
        if self.interest_status == "unknown":
            return {"appointment_status": "incomplete", "reasons": ["Interest not yet confirmed"]}
        return {"appointment_status": "info_only", "reasons": ["Caller asked questions, no booking made"]}
