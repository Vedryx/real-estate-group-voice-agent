"""Function-tool catalog for the salon-appointment voice agent (Aura Salon &
Spa).

Standalone sibling of tools/catalog_clinic.py — same discipline (every fact
the agent speaks traces to a tool result or the Layer-0 brief; prices are
DUMMY placeholders always flagged indicative), adapted for salon-specific
concerns: a first-time-client flag on request_appointment (drives the
persona's one-time patch-test mention) and bridal makeup being routed to a
consultation rather than a confirmed booking.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Literal

from livekit.agents import Agent, RunContext, function_tool
from livekit.agents.llm import StopResponse, ToolError
from livekit.agents.voice.speech_handle import SpeechHandle

from agent import data_store as ds
from agent.escalation_agent_salon import SalonHumanEscalationAgent
from agent.phone import normalize_indian_phone
from agent.scheduling import IST_NAME, resolve_datetime
from agent.state_salon import SalonCallUserdata

logger = logging.getLogger("Aura Salon & Spa")

SALON_PROJECT_NAME = "Aura Salon & Spa"

SALON_VALID_OUTCOMES = frozenset(
    {
        "appointment_requested",
        "callback_requested",
        "info_only_no_appointment",
        "wrong_number",
        "escalation_needed",
        "opted_out",
        "spam_or_abandoned",
    }
)

_PERSIST_FAILED = (
    "Saving failed. Do NOT tell the caller it was saved. Apologise briefly, say you'll "
    "personally make sure the front desk has their details, and offer to have someone follow up."
)

Department = Literal["Hair", "Skin", "Nails", "Makeup"]
Outcome = Literal[
    "appointment_requested",
    "callback_requested",
    "info_only_no_appointment",
    "wrong_number",
    "escalation_needed",
    "opted_out",
    "spam_or_abandoned",
]

_DEPARTMENT_ALIASES = {
    "HAIR": "Hair",
    "HAIRDRESSING": "Hair",
    "SKIN": "Skin",
    "FACIAL": "Skin",
    "FACE": "Skin",
    "NAILS": "Nails",
    "NAIL": "Nails",
    "MANICURE": "Nails",
    "PEDICURE": "Nails",
    "MAKEUP": "Makeup",
    "MAKE UP": "Makeup",
    "BRIDAL": "Makeup",
}


def _normalize_department(department: str | None) -> str | None:
    if not department:
        return None
    return _DEPARTMENT_ALIASES.get(" ".join(department.strip().upper().split()))


def _write_ack_line() -> str:
    return "One second, let me note that down..."


def _say_line(session, text: str) -> bool:
    if getattr(session, "tts", None) is None:
        return False
    session.say(text)
    return True


def _speak_write_ack(context: RunContext[SalonCallUserdata], ud: SalonCallUserdata) -> None:
    if ud.write_ack_spoken:
        return
    if _say_line(context.session, _write_ack_line()):
        ud.write_ack_spoken = True


async def _log_with_filler(
    context: RunContext[SalonCallUserdata], ud: SalonCallUserdata, **kwargs: Any
) -> dict[str, Any] | None:
    _speak_write_ack(context, ud)
    return await asyncio.to_thread(_safe_log_lead, ud, **kwargs)


def _cta_confirmation(kind: str, name: str | None, when: str) -> str:
    who = f" {name}" if name else ""
    if kind == "appointment":
        return (
            f"Done{who}. Your appointment request for {when} is noted; the front desk will "
            "confirm the exact slot."
        )
    return f"Done{who}. Your callback request for {when} is noted; the front desk will call you back."


def _safe_log_lead(ud: SalonCallUserdata, **kwargs: Any) -> dict[str, Any] | None:
    outcome = kwargs.get("outcome")
    if outcome in ud.logged_outcomes:
        return {"_duplicate": True, "lead_id": None}
    try:
        record = ds.log_lead(project=SALON_PROJECT_NAME, valid_outcomes=SALON_VALID_OUTCOMES, **kwargs)
    except Exception:  # noqa: BLE001 - never let a write error break the call
        logger.exception("log_lead failed for outcome=%s", outcome)
        return None
    ud.logged_outcomes.add(outcome)
    return record


SalonTopic = Literal[
    "overview",
    "departments",
    "stylists",
    "services",
    "timings",
    "location",
    "fees",
    "patch_test",
    "results",
    "bridal",
    "membership",
]


@function_tool
async def get_salon_info(
    context: RunContext[SalonCallUserdata],
    topic: SalonTopic,
    department: Department | None = None,
) -> dict[str, Any]:
    """Look up a detail about Aura Salon & Spa that ISN'T already in your call brief.

    The brief already covers the basics (stylists, departments, timings, a few common prices) — answer
    those directly. Use this only to go deeper: a specific department's full service list, a specific
    service's exact price, or the patch-test/results/bridal/membership policy verbatim.

    Args:
        topic: what to look up — one of overview, departments, stylists, services, timings, location,
            fees, patch_test, results, bridal, membership.
        department: "Hair", "Skin", "Nails" or "Makeup", for stylists/services/fees.
    """
    k = context.userdata.knowledge
    normalized = _normalize_department(department)
    if normalized:
        context.userdata.department_interest = normalized

    if topic == "departments":
        return {"departments": k.departments()}

    if topic == "stylists":
        stylists = k.stylists_for(department) if department else k.stylists()
        return {"stylists": stylists}

    if topic == "services":
        return {"department": normalized, "services": k.services(department)}

    if topic == "timings":
        return {"timings": k.timings(), "policy": k.appointment_policy()}

    if topic == "location":
        return {"address": k.location()["address"], "contact": k.contact()}

    if topic == "fees":
        return {
            "fees": k.fees(department),
            "indicative_only": True,
            "fee_basis": k.fee_basis(),
            "disclaimer": k.commercial_disclaimer(),
        }

    if topic == "patch_test":
        return {"patch_test_note": k.patch_test_note()}

    if topic == "results":
        return {"results_disclaimer": k.results_disclaimer()}

    if topic == "bridal":
        return {"bridal_note": k.bridal_note()}

    if topic == "membership":
        return {"membership_note": k.membership_note()}

    # overview / fallback
    return {
        "salon_name": k.salon_name(),
        "salon_type": k.salon_type(),
        "departments": k.departments(),
        "address": k.location()["address"],
    }


# --------------------------------------------------------------------- lead capture
def _current_lead_kwargs(ud: SalonCallUserdata, notes: str) -> dict[str, Any]:
    qualification = ud.qualification_snapshot()
    return {
        "caller_phone": ud.caller_phone,
        "name": ud.caller_name,
        "preferred_language": ud.preferred_language,
        "config_interest": ud.department_interest,  # generic field, reused: department not BHK
        "source_channel": ud.source_channel,
        "source_campaign": ud.source_campaign,
        "source_enquiry_id": ud.source_enquiry_id,
        "consent_reference": ud.consent_reference,
        "interest_status": ud.interest_status,
        "next_step": ud.next_step,
        "closing_attempted": ud.closing_attempted,
        "qualification_status": qualification["appointment_status"],
        "lead_temperature": "n/a",  # no sales-temperature concept for a salon
        "qualification_reasons": qualification["reasons"],
        "notes": notes,
    }


_OUTCOME_TO_INTEREST = {
    "wrong_number": "wrong_person",
    "opted_out": "opted_out",
}


def _apply_outcome_to_state(ud: SalonCallUserdata, outcome: str) -> None:
    if outcome in _OUTCOME_TO_INTEREST:
        ud.interest_status = _OUTCOME_TO_INTEREST[outcome]
        ud.next_step = "no_followup"
        ud.appointment_declined = True
    elif outcome == "appointment_requested":
        ud.next_step = "appointment_requested"
    elif outcome == "callback_requested":
        ud.next_step = "callback_requested"
    elif outcome in {"info_only_no_appointment", "spam_or_abandoned"}:
        ud.next_step = "no_followup"
    ud.closing_attempted = True


def _derive_final_outcome(ud: SalonCallUserdata) -> str:
    reverse = {v: k for k, v in _OUTCOME_TO_INTEREST.items()}
    if ud.interest_status in reverse:
        return reverse[ud.interest_status]
    if ud.next_step == "appointment_requested":
        return "appointment_requested"
    if ud.next_step == "callback_requested":
        return "callback_requested"
    if ud.interest_status in {"active", "casual"}:
        return "info_only_no_appointment"
    return "spam_or_abandoned"


def _validate_phone_or_raise(resolved_phone: str | None) -> str | None:
    if not resolved_phone:
        return None
    normalized = normalize_indian_phone(resolved_phone)
    if normalized is None:
        raise ToolError(
            f"{resolved_phone!r} is not a valid Indian mobile number (10 digits, starting 6-9). "
            "Ask the caller to repeat their number, then call this again."
        )
    return normalized


@function_tool
async def request_appointment(
    context: RunContext[SalonCallUserdata],
    preferred_date: str,
    department: Department | None = None,
    service: str | None = None,
    first_time_client: bool | None = None,
    name: str | None = None,
    phone: str | None = None,
) -> dict[str, Any]:
    """Capture an appointment REQUEST (primary next step). No live calendar — logs intent for the front desk to confirm the slot. For bridal makeup this books a CONSULTATION, not a final booking.

    Args:
        preferred_date: The caller's preferred day/time in their own words, e.g. "Saturday afternoon".
        department: "Hair", "Skin", "Nails" or "Makeup", if known.
        service: The specific service requested in the caller's words, e.g. "haircut", "hair color", "bridal makeup consultation".
        first_time_client: True if the caller says this is their first time for a color/keratin/henna service (drives the patch-test note) — leave unset if not mentioned.
        name: Caller's name if stated anywhere in the call.
        phone: Best callback number if stated (falls back to the known caller number).
    """
    ud = context.userdata
    if name:
        ud.caller_name = name
    normalized_dept = _normalize_department(department)
    if normalized_dept:
        ud.department_interest = normalized_dept
    if service:
        ud.service_interest = service
    if first_time_client is not None:
        ud.first_time_chemical_service = first_time_client
    resolved_name = name or ud.caller_name
    resolved_phone = phone or ud.caller_phone
    if not resolved_name:
        raise ToolError("A caller name is required before saving an appointment request.")
    if not resolved_phone:
        raise ToolError("A callback number is required before saving an appointment request.")
    resolved_phone = _validate_phone_or_raise(resolved_phone)

    ud.next_step = "appointment_requested"
    ud.closing_attempted = True
    ud.appointment_offered = True
    ud.cta_offer_count += 1
    note = f"Appointment requested, preferred: {preferred_date}"
    if normalized_dept:
        note = f"{note} ({normalized_dept}"
        note += f" - {service})" if service else ")"
    kwargs = _current_lead_kwargs(ud, notes=note)
    kwargs["name"] = resolved_name
    kwargs["caller_phone"] = resolved_phone
    kwargs["preferred_datetime_raw"] = preferred_date
    kwargs["preferred_datetime_iso"] = resolve_datetime(preferred_date)
    kwargs["timezone_name"] = IST_NAME
    record = await _log_with_filler(
        context, ud, outcome="appointment_requested", consent_to_be_contacted=True, **kwargs
    )
    if record is None:
        return {"logged": False, "instruction": _PERSIST_FAILED}
    ud.lead_logged = True
    confirmation = _cta_confirmation("appointment", resolved_name, preferred_date)
    if _say_line(context.session, confirmation):
        raise StopResponse()
    return {"logged": True, "say_to_caller": confirmation}


@function_tool
async def request_callback(
    context: RunContext[SalonCallUserdata],
    preferred_time: str,
    name: str | None = None,
    phone: str | None = None,
    notes: str = "",
) -> dict[str, Any]:
    """Capture a CALLBACK request (fallback when an appointment isn't set). Ask the caller what time suits them BEFORE calling this — don't call it with a guessed time.

    Args:
        preferred_time: The caller's preferred callback time in their own words.
        name: Caller's name if stated anywhere in the call.
        phone: Best callback number if stated (falls back to the known caller number).
        notes: Anything else the front desk should know before calling back.
    """
    ud = context.userdata
    if name:
        ud.caller_name = name
    resolved_phone = _validate_phone_or_raise(phone or ud.caller_phone)
    ud.next_step = "callback_requested"
    ud.closing_attempted = True
    ud.callback_offered = True
    ud.cta_offer_count += 1
    note = f"Callback requested, preferred time: {preferred_time}"
    if notes:
        note = f"{note}. {notes}"
    kwargs = _current_lead_kwargs(ud, notes=note)
    kwargs["name"] = name or ud.caller_name
    kwargs["caller_phone"] = resolved_phone
    kwargs["preferred_datetime_raw"] = preferred_time
    kwargs["preferred_datetime_iso"] = resolve_datetime(preferred_time)
    kwargs["timezone_name"] = IST_NAME
    record = await _log_with_filler(
        context, ud, outcome="callback_requested", consent_to_be_contacted=True, **kwargs
    )
    if record is None:
        return {"logged": False, "instruction": _PERSIST_FAILED}
    ud.lead_logged = True
    confirmation = _cta_confirmation("callback", name or ud.caller_name, preferred_time)
    if _say_line(context.session, confirmation):
        raise StopResponse()
    return {"logged": True, "say_to_caller": confirmation}


@function_tool
async def log_terminal_outcome(
    context: RunContext[SalonCallUserdata],
    outcome: Outcome,
    name: str | None = None,
    phone: str | None = None,
    notes: str = "",
    consent_to_be_contacted: bool = True,
) -> dict[str, Any]:
    """Persist a terminal / catch-all outcome (info_only_no_appointment, wrong_number, opted_out, etc.). Use request_appointment / request_callback for those specific next steps instead.

    Args:
        outcome: One of the valid outcomes (appointment_requested, callback_requested, info_only_no_appointment, wrong_number, escalation_needed, opted_out, spam_or_abandoned).
        name: Caller's name if stated anywhere in the call.
        phone: Best callback number if stated.
        notes: Free-text notes.
        consent_to_be_contacted: False if the caller asked not to be contacted.
    """
    if outcome not in SALON_VALID_OUTCOMES:
        raise ToolError(f"invalid outcome {outcome!r}; must be one of {sorted(SALON_VALID_OUTCOMES)}")
    ud = context.userdata
    if name:
        ud.caller_name = name
    _apply_outcome_to_state(ud, outcome)
    kwargs = _current_lead_kwargs(ud, notes)
    kwargs["name"] = name or ud.caller_name
    kwargs["caller_phone"] = phone or ud.caller_phone
    record = _safe_log_lead(
        ud, outcome=outcome, consent_to_be_contacted=consent_to_be_contacted, **kwargs
    )
    if record is None:
        return {"logged": False, "instruction": _PERSIST_FAILED}
    ud.lead_logged = True
    return {"logged": True, "lead_id": record.get("lead_id"), "qualification": ud.qualification_snapshot()}


@function_tool
async def escalate_to_human(context: RunContext[SalonCallUserdata], reason: str) -> Agent:
    """Hand off when the caller asks for a human, is upset, or the topic is outside your authority (a complaint, a service issue). Don't argue or try to resolve it first.

    Args:
        reason: Brief reason, e.g. "caller has a complaint" or "caller asked for a human".
    """
    ud = context.userdata
    _safe_log_lead(
        ud,
        outcome="escalation_needed",
        consent_to_be_contacted=True,
        **_current_lead_kwargs(ud, notes=reason),
    )
    ud.lead_logged = True
    context.disallow_interruptions()
    return SalonHumanEscalationAgent(reason=reason)


@function_tool
async def end_call(context: RunContext[SalonCallUserdata]) -> str:
    """Gracefully end the call. The final outcome is derived deterministically from what was recorded during the call — you do NOT pass it. Must be the last tool call in a turn; only a brief goodbye after.
    """
    ud = context.userdata
    outcome = _derive_final_outcome(ud)
    _apply_outcome_to_state(ud, outcome)
    if not ud.lead_logged:
        _safe_log_lead(
            ud,
            outcome=outcome,
            consent_to_be_contacted=outcome != "opted_out",
            **_current_lead_kwargs(ud, notes="auto-logged at end_call"),
        )
        ud.lead_logged = True

    def _on_speech_done(_: SpeechHandle) -> None:
        context.session.shutdown()

    context.speech_handle.add_done_callback(_on_speech_done)
    return "Call is wrapping up. Say a brief, warm goodbye now — nothing else."


ALL_TOOLS = [
    get_salon_info,
    request_appointment,
    request_callback,
    escalate_to_human,
    log_terminal_outcome,
    end_call,
]
