"""Function-tool catalog for The Canopy outbound sales agent.

Single project. Three knowledge layers back these tools (agent/canopy.py):
  Layer 1 (project facts) -> get_configurations / get_unit_details /
      get_amenities / get_location / get_specifications / get_rera
  Layer 2 (DUMMY commercial) -> get_pricing / get_possession  (always indicative)
  no source -> commercial_detail_unavailable  (honest gap -> callback)

Every project fact the agent speaks must come from a tool result (or the
Layer-0 brief in the prompt), never from the model's own knowledge. Price and
possession are DUMMY placeholder values and are always returned flagged
indicative — never a firm figure.
"""

from __future__ import annotations

import logging
from typing import Any, Literal

from livekit.agents import Agent, RunContext, function_tool
from livekit.agents.llm import ToolError
from livekit.agents.voice.speech_handle import SpeechHandle

from agent import data_store as ds
from agent.data_store import VALID_OUTCOMES
from agent.escalation_agent import HumanEscalationAgent
from agent.state import (
    CONFIG_INTERESTS,
    INTEREST_STATUSES,
    PURCHASE_PURPOSES,
    PURCHASE_TIMELINES,
    CallUserdata,
)

logger = logging.getLogger("The Canopy")

# Failure instruction shared by every persistence tool. The agent MUST NOT tell
# the caller an action succeeded unless the tool returned logged=True.
_PERSIST_FAILED = (
    "Saving failed. Do NOT tell the caller it was saved or sent. Apologise "
    "briefly, say you'll personally make sure the team has their details, and "
    "offer to have someone follow up."
)

# Literal aliases so the tool SCHEMA constrains the model — this stops the LLM
# inventing values like a compound outcome "callback_requested, not_opposable".
Config = Literal["2 BHK", "3 BHK"]
InterestStatus = Literal[
    "active",
    "casual",
    "not_interested",
    "already_purchased",
    "accidental_click",
    "wrong_person",
    "opted_out",
]
Timeline = Literal["unknown", "within_3_months", "3_to_6_months", "over_6_months"]
Purpose = Literal["unknown", "self_use", "investment"]
Outcome = Literal[
    "qualified_lead",
    "callback_requested",
    "site_visit_requested",
    "nurture_lead",
    "not_interested",
    "already_purchased",
    "accidental_click",
    "wrong_number",
    "info_only_no_lead",
    "escalation_needed",
    "opted_out",
    "spam_or_abandoned",
]


def _safe_log_lead(**kwargs: Any) -> dict[str, Any] | None:
    """Persist a lead, converting any failure into a signal instead of a crash.

    Returns the record on success, or None on failure (logged for ops). The
    caller decides what to tell the customer — never confirm on None.
    """
    try:
        return ds.log_lead(**kwargs)
    except Exception:  # noqa: BLE001 - never let a write error break the call
        logger.exception("log_lead failed for outcome=%s", kwargs.get("outcome"))
        return None

# Money topics with no source in any layer — never guess these.
UNAVAILABLE_COMMERCIAL_TOPICS = {
    "floor_rise",
    "view_premium",
    "plc",
    "gst",
    "stamp_duty",
    "registration",
    "maintenance",
    "parking_charge",
    "parking_allocation",
    "current_availability",
    "launch_offer",
    "exact_distance",
}


def _normalize_config(config: str | None) -> str | None:
    if not config:
        return None
    digits = "".join(ch for ch in config if ch.isdigit())
    return f"{digits} BHK" if digits else None


# --------------------------------------------------------------------- Layer 1
@function_tool
async def get_configurations(context: RunContext[CallUserdata]) -> dict[str, Any]:
    """List the home types available at The Canopy (2 BHK and 3 BHK) with carpet-size bands.

    Present it conversationally — do not read out every internal layout unless
    the caller asks for a specific one.
    """
    k = context.userdata.knowledge
    return {
        "configs": k.configs(),
        "summary": {c: k.config_summary(c) for c in k.configs()},
        "note": "Speak the carpet band naturally; drill into a specific layout only if asked.",
    }


@function_tool
async def get_unit_details(
    context: RunContext[CallUserdata], config: Config
) -> dict[str, Any]:
    """Get carpet-area details for a config — call this ONLY when the caller asks for specific sizes/layouts, not to open a pitch.

    Speak the RERA carpet area as the size. Never quote the larger
    "carpet + balconies" figure as carpet. Do not read out every layout — a
    caller who just mentioned "3 BHK" wants warmth and a nudge to visit, not a
    spec dump (see the returned note).

    Args:
        config: "2 BHK" or "3 BHK".
    """
    k = context.userdata.knowledge
    normalized = _normalize_config(config)
    units = k.unit_types_for(config)
    if not units:
        return {
            "found": False,
            "message": f"No layout matches {config!r}. The Canopy has only 2 BHK and 3 BHK.",
        }
    if normalized in CONFIG_INTERESTS:
        context.userdata.config_interest = normalized
    carpets = sorted(u["carpet_sqft"] for u in units)
    return {
        "found": True,
        "config": normalized,
        "layout_count": len(units),
        "carpet_sqft_range": [carpets[0], carpets[-1]],
        "carpet_area_note": k.carpet_area_note(),
        "layouts": [
            {
                "label": u["label"],
                "carpet_sqft": u["carpet_sqft"],
                "balcony_sqft": u["balcony_sqft"],
                "dry_balcony_sqft": u["dry_balcony_sqft"],
            }
            for u in units
        ],
        "note": (
            "Summarise warmly (e.g. 'we have a few 3 BHK layouts') and suggest seeing them in "
            "person — do NOT list every layout or its numbers unless the caller specifically "
            "asks for the sizes/options."
        ),
    }


@function_tool
async def get_amenities(
    context: RunContext[CallUserdata], scope: Literal["building", "township"]
) -> dict[str, Any]:
    """Get amenities for The Canopy.

    Args:
        scope: "building" for in-tower amenities (pool, gym, business lounge),
            or "township" for Forest Trails amenities. Township results are
            paid / partly under construction — say so honestly.
    """
    k = context.userdata.knowledge
    if scope == "township":
        township = k.amenities_township()
        return {
            "scope": "township",
            "amenities": township["list"],
            "caveat": township["caveat"],
            "must_state_caveat": True,
        }
    building = k.amenities_building()
    return {"scope": "building", "amenities": building, "must_state_caveat": False}


@function_tool
async def get_location(context: RunContext[CallUserdata]) -> dict[str, Any]:
    """Where The Canopy is and how it's placed (Bhugaon, Forest Trails, approx commute)."""
    k = context.userdata.knowledge
    loc = k.location()
    return {
        "address": loc["address"],
        "township": k.township(),
        "township_size_acres": loc["township_size_acres"],
        "commute": loc["commute"]["phrasing"],
        "positioning": k.building()["positioning"],
        "note": "Any commute time is approximate; no exact distances are confirmed.",
    }


@function_tool
async def get_specifications(context: RunContext[CallUserdata]) -> dict[str, Any]:
    """Get the apartment specifications (flooring, kitchen, doors, electrical, plumbing).

    Brands are indicative ("or equivalent") — never promise a specific final brand.
    """
    return {
        "specifications": context.userdata.knowledge.specifications(),
        "note": "Brands are 'or equivalent' — present as indicative, not guaranteed.",
    }


@function_tool
async def get_rera(context: RunContext[CallUserdata]) -> dict[str, Any]:
    """Get The Canopy's MahaRERA registration number and portal (share it if the caller asks)."""
    return context.userdata.knowledge.rera()


# --------------------------------------------------------------------- Layer 2 (DUMMY)
@function_tool
async def get_pricing(
    context: RunContext[CallUserdata], config: Config | None = None
) -> dict[str, Any]:
    """Get the INDICATIVE price band for The Canopy. Always present as indicative, never final.

    Args:
        config: "2 BHK" or "3 BHK" to narrow; omit for both.
    """
    k = context.userdata.knowledge
    normalized = _normalize_config(config)
    if normalized in CONFIG_INTERESTS:
        context.userdata.config_interest = normalized
    return {
        "pricing": k.pricing(config),
        "indicative_only": True,
        "disclaimer": k.commercial_disclaimer(),
        "instruction": (
            "State this as indicative / starting-from and say the team confirms the exact figure "
            "for the specific floor, view and unit. Never present it as a final price."
        ),
    }


@function_tool
async def get_possession(context: RunContext[CallUserdata]) -> dict[str, Any]:
    """Get the INDICATIVE possession timeline for The Canopy. Present as a target, team confirms."""
    k = context.userdata.knowledge
    return {
        "possession": k.possession(),
        "indicative_only": True,
        "instruction": "Give it as a target timeline the team will confirm — not a promise.",
    }


@function_tool
async def commercial_detail_unavailable(
    context: RunContext[CallUserdata], topic: str
) -> dict[str, Any]:
    """Use for a money/logistics detail you have NO source for (floor-rise, GST, stamp duty, maintenance, parking charges/allocation, exact availability, launch offers, exact distances).

    Do not guess these. Return an honest "the team will share exact figures" and offer a callback.

    Args:
        topic: Short slug of what was asked, e.g. "stamp_duty" or "maintenance".
    """
    return {
        "available": False,
        "topic": topic,
        "message": (
            "Be honest that you don't have that exact figure on this call, and offer to have the "
            "team share it — a callback or during a site visit. Do not invent a number."
        ),
    }


# --------------------------------------------------------------------- lead capture
def _current_lead_kwargs(ud: CallUserdata, notes: str) -> dict[str, Any]:
    qualification = ud.qualification_snapshot()
    return {
        "caller_phone": ud.caller_phone,
        "name": ud.caller_name,
        "preferred_language": ud.preferred_language,
        "config_interest": ud.config_interest,
        "purchase_timeline": ud.purchase_timeline,
        "purchase_timeline_asked": ud.purchase_timeline_asked,
        "purchase_purpose": ud.purchase_purpose,
        "source_channel": ud.source_channel,
        "source_campaign": ud.source_campaign,
        "source_enquiry_id": ud.source_enquiry_id,
        "consent_reference": ud.consent_reference,
        "interest_status": ud.interest_status,
        "next_step": ud.next_step,
        "closing_attempted": ud.closing_attempted,
        "qualification_status": qualification["qualification_status"],
        "lead_temperature": qualification["lead_temperature"],
        "qualification_reasons": qualification["qualification_reasons"],
        "notes": notes,
    }


_OUTCOME_TO_INTEREST = {
    "not_interested": "not_interested",
    "already_purchased": "already_purchased",
    "accidental_click": "accidental_click",
    "wrong_number": "wrong_person",
    "opted_out": "opted_out",
}


def _apply_outcome_to_state(ud: CallUserdata, outcome: str) -> None:
    if outcome in _OUTCOME_TO_INTEREST:
        ud.interest_status = _OUTCOME_TO_INTEREST[outcome]
        ud.next_step = "no_followup"
    elif outcome == "site_visit_requested":
        ud.next_step = "site_visit_requested"
    elif outcome == "callback_requested":
        ud.next_step = "callback_requested"
    elif outcome == "nurture_lead":
        ud.next_step = "future_followup"
    elif outcome in {"info_only_no_lead", "spam_or_abandoned"}:
        ud.next_step = "no_followup"
    ud.closing_attempted = True


def _derive_final_outcome(ud: CallUserdata) -> str:
    """Compute the closing outcome deterministically from call state.

    The final outcome is NOT taken from the LLM (it once invented a compound
    string). It is read off the state the tools already recorded.
    """
    reverse = {v: k for k, v in _OUTCOME_TO_INTEREST.items()}
    if ud.interest_status in reverse:
        return reverse[ud.interest_status]
    if ud.next_step == "site_visit_requested":
        return "site_visit_requested"
    if ud.next_step == "callback_requested":
        return "callback_requested"
    if ud.next_step == "future_followup":
        return "nurture_lead"
    if ud.interest_status in {"active", "casual"}:
        return "info_only_no_lead"
    return "spam_or_abandoned"


@function_tool
async def record_lead_qualification(
    context: RunContext[CallUserdata],
    interest_status: InterestStatus,
    config_interest: Config | None = None,
    purchase_timeline: Timeline | None = None,
    purchase_purpose: Purpose | None = None,
) -> dict[str, Any]:
    """Record explicit qualification signals as the caller volunteers them — do not guess.

    Args:
        interest_status: active, casual, not_interested, already_purchased, accidental_click, wrong_person, or opted_out.
        config_interest: "2 BHK" or "3 BHK", only if stated.
        purchase_timeline: unknown, within_3_months, 3_to_6_months, or over_6_months, only if stated.
        purchase_purpose: unknown, self_use, or investment, only if stated.
    """
    if interest_status not in INTEREST_STATUSES - {"unknown"}:
        raise ToolError(
            f"invalid interest_status {interest_status!r}; must be one of "
            f"{sorted(INTEREST_STATUSES - {'unknown'})}"
        )
    if purchase_timeline is not None and purchase_timeline not in PURCHASE_TIMELINES:
        raise ToolError(f"invalid purchase_timeline {purchase_timeline!r}")
    if purchase_purpose is not None and purchase_purpose not in PURCHASE_PURPOSES:
        raise ToolError(f"invalid purchase_purpose {purchase_purpose!r}")

    ud = context.userdata
    ud.interest_status = interest_status
    normalized = _normalize_config(config_interest)
    if normalized in CONFIG_INTERESTS:
        ud.config_interest = normalized
    if purchase_timeline is not None:
        ud.purchase_timeline = purchase_timeline
        ud.purchase_timeline_asked = True
    if purchase_purpose is not None:
        ud.purchase_purpose = purchase_purpose

    return {
        "recorded": True,
        "conversation_stage": ud.conversation_stage,
        "qualification": ud.qualification_snapshot(),
    }


@function_tool
async def schedule_site_visit(
    context: RunContext[CallUserdata],
    preferred_date: str,
    name: str | None = None,
    phone: str | None = None,
) -> dict[str, Any]:
    """Capture a site-visit REQUEST (primary next step). No calendar integration — logs intent for the team to confirm the slot.

    Args:
        preferred_date: The caller's preferred date/time in their own words, e.g. "this Saturday".
        name: Caller's name if stated anywhere in the call (extract even if given casually).
        phone: Best callback number if stated (falls back to the known caller number).
    """
    ud = context.userdata
    if name:
        ud.caller_name = name
    resolved_name = name or ud.caller_name
    resolved_phone = phone or ud.caller_phone
    if not resolved_name:
        raise ToolError("A caller name is required before saving a site-visit request.")
    if not resolved_phone:
        raise ToolError("A callback number is required before saving a site-visit request.")

    ud.next_step = "site_visit_requested"
    ud.closing_attempted = True
    kwargs = _current_lead_kwargs(ud, notes=f"Site visit requested, preferred: {preferred_date}")
    kwargs["name"] = resolved_name
    kwargs["caller_phone"] = resolved_phone
    record = _safe_log_lead(outcome="site_visit_requested", consent_to_be_contacted=True, **kwargs)
    if record is None:
        return {"logged": False, "instruction": _PERSIST_FAILED}
    ud.lead_logged = True
    return {
        "logged": True,
        "lead_id": record["lead_id"],
        "status": "requested_pending_team_confirmation",
        "message": (
            "The request is saved but not yet confirmed. Tell the caller the team will call to "
            "confirm the final slot; never say it is booked."
        ),
    }


@function_tool
async def log_callback(
    context: RunContext[CallUserdata],
    name: str | None = None,
    phone: str | None = None,
    notes: str = "",
) -> dict[str, Any]:
    """Capture a CALLBACK request (the fallback next step when a site visit isn't set).

    Args:
        name: Caller's name if stated anywhere in the call.
        phone: Best callback number if stated (falls back to the known caller number).
        notes: Anything the team should know before calling back.
    """
    ud = context.userdata
    if name:
        ud.caller_name = name
    ud.next_step = "callback_requested"
    ud.closing_attempted = True
    kwargs = _current_lead_kwargs(ud, notes=notes or "Callback requested")
    kwargs["name"] = name or ud.caller_name
    kwargs["caller_phone"] = phone or ud.caller_phone
    record = _safe_log_lead(outcome="callback_requested", consent_to_be_contacted=True, **kwargs)
    if record is None:
        return {"logged": False, "instruction": _PERSIST_FAILED}
    ud.lead_logged = True
    return {"logged": True, "lead_id": record["lead_id"]}


@function_tool
async def log_lead(
    context: RunContext[CallUserdata],
    outcome: Outcome,
    name: str | None = None,
    phone: str | None = None,
    notes: str = "",
    consent_to_be_contacted: bool = True,
) -> dict[str, Any]:
    """Persist a lead outcome (terminal or catch-all). Use schedule_site_visit / log_callback for those specific CTAs instead.

    Args:
        outcome: One of the valid outcomes (qualified_lead, callback_requested, site_visit_requested, nurture_lead, not_interested, already_purchased, accidental_click, wrong_number, info_only_no_lead, escalation_needed, opted_out, spam_or_abandoned).
        name: Caller's name if stated anywhere in the call.
        phone: Best callback number if stated.
        notes: Free-text notes.
        consent_to_be_contacted: False if the caller asked not to be contacted.
    """
    if outcome not in VALID_OUTCOMES:
        raise ToolError(f"invalid outcome {outcome!r}; must be one of {sorted(VALID_OUTCOMES)}")
    ud = context.userdata
    if name:
        ud.caller_name = name
    _apply_outcome_to_state(ud, outcome)
    kwargs = _current_lead_kwargs(ud, notes)
    kwargs["name"] = name or ud.caller_name
    kwargs["caller_phone"] = phone or ud.caller_phone
    record = _safe_log_lead(
        outcome=outcome, consent_to_be_contacted=consent_to_be_contacted, **kwargs
    )
    if record is None:
        return {"logged": False, "instruction": _PERSIST_FAILED}
    ud.lead_logged = True
    return {"logged": True, "lead_id": record["lead_id"], "qualification": ud.qualification_snapshot()}


@function_tool
async def escalate_to_human(context: RunContext[CallUserdata], reason: str) -> Agent:
    """Hand off when the caller asks for a human, is upset, or the topic is outside your authority (payments, legal/title, price negotiation). Don't argue or try to resolve it first.

    Args:
        reason: Brief reason, e.g. "caller asked for a human" or "caller is upset".
    """
    ud = context.userdata
    _safe_log_lead(
        outcome="escalation_needed",
        consent_to_be_contacted=True,
        **_current_lead_kwargs(ud, notes=reason),
    )
    ud.lead_logged = True
    context.disallow_interruptions()
    return HumanEscalationAgent(reason=reason)


@function_tool
async def end_call(context: RunContext[CallUserdata]) -> str:
    """Gracefully end the call. The final outcome is derived deterministically from what was recorded during the call — you do NOT pass it. Must be the last tool call in a turn; only a brief goodbye after.
    """
    ud = context.userdata
    outcome = _derive_final_outcome(ud)
    _apply_outcome_to_state(ud, outcome)
    if not ud.lead_logged:
        _safe_log_lead(
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
    get_configurations,
    get_unit_details,
    get_amenities,
    get_location,
    get_specifications,
    get_rera,
    get_pricing,
    get_possession,
    commercial_detail_unavailable,
    record_lead_qualification,
    schedule_site_visit,
    log_callback,
    log_lead,
    escalate_to_human,
    end_call,
]
