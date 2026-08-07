"""Function-tool catalog for The Canopy outbound sales agent.

Single project, six tools (D4):
  get_detailed_project_info(topic, config?) — one retrieval tool over all facts
      (configs, unit_details, amenities, location, specs, pricing, possession,
      rera). The brief covers the basics; this goes deeper.
  request_site_visit / request_callback — the two CTAs (deterministic confirm).
  log_terminal_outcome — terminal / catch-all lead outcome.
  escalate_to_human — human hand-off.
  end_call — deterministic close.

Qualification is updated outside the tool loop (state.update_qualification_from_text,
called in the assistant's on_user_turn_completed). Every project fact the agent
speaks comes from a tool result or the Layer-0 brief, never the model's own
knowledge. Price/possession are DUMMY placeholders, always flagged indicative.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Literal

from livekit.agents import Agent, RunContext, function_tool
from livekit.agents.llm import StopResponse, ToolError
from livekit.agents.voice.speech_handle import SpeechHandle

from agent import data_store as ds
from agent.data_store import VALID_OUTCOMES
from agent.escalation_agent import HumanEscalationAgent
from agent.scheduling import IST_NAME, resolve_datetime
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


def _write_ack_line(lang: str | None) -> str:
    """Spoken 'let me note that down' ack, said just before a write so the caller
    never hears dead air during the save. Number-free so the TTS can't mangle it."""
    if lang == "en-IN":
        return "One second, let me note that down..."
    if lang == "mr-IN":
        return "Ek second, mi note karto..."
    return "Ek second, main note kar leta hoon..."


def _speak_write_ack(context: RunContext[CallUserdata], ud: CallUserdata) -> None:
    """Say the write-ack ONCE per call (founder: never repeat the phrase in one
    call). Fires right before an actual persistence write, after validation has
    passed — so a failed/premature tool call never triggers it."""
    if ud.write_ack_spoken:
        return
    ud.write_ack_spoken = True
    context.session.say(_write_ack_line(ud.preferred_language))


async def _log_with_filler(
    context: RunContext[CallUserdata], ud: CallUserdata, **kwargs: Any
) -> dict[str, Any] | None:
    """Persist off the event loop. Speaks a short "note kar leta hoon" ack first
    (once per call) so the caller hears the write happening, then runs the write
    off-thread; the deterministic confirmation follows once it completes.
    """
    _speak_write_ack(context, ud)
    return await asyncio.to_thread(_safe_log_lead, ud, **kwargs)


def _cta_confirmation(kind: str, name: str | None, when: str, lang: str | None) -> str:
    """Deterministic confirmation line assembled from tool output (C1) — spoken
    directly via session.say, so no extra LLM generation is needed."""
    who = f" {name} ji" if name else ""
    lang = lang or "hi-IN"
    if kind == "visit":
        if lang == "en-IN":
            return f"Done{who}. Your site visit request for {when} is saved; the team will confirm the slot."
        if lang == "mr-IN":
            return f"Done{who}. {when} chi site visit request save zali aahe; team slot confirm karel."
        return f"Done{who}. {when} ki site visit request save ho gayi hai; team slot confirm karegi."
    # callback
    if lang == "en-IN":
        return f"Done{who}. Your callback request for {when} is saved; the team will call you back."
    if lang == "mr-IN":
        return f"Theek aahe{who}. {when} cha callback request save zala aahe; team tumhala call karel."
    return f"Theek hai{who}. {when} ka callback request save ho gaya hai; team aapko call karegi."


def _safe_log_lead(ud: CallUserdata, **kwargs: Any) -> dict[str, Any] | None:
    """Persist a lead once, converting failure into a signal instead of a crash.

    Deduplicates by outcome per call: the same outcome (e.g. callback_requested)
    is never written twice, even if the model calls request_callback and then
    log_terminal_outcome for it. Returns the record on success, {"_duplicate": True}
    marker if it was already logged, or None on write failure (never confirm
    success on None).
    """
    outcome = kwargs.get("outcome")
    if outcome in ud.logged_outcomes:
        return {"_duplicate": True, "lead_id": None}
    try:
        record = ds.log_lead(**kwargs)
    except Exception:  # noqa: BLE001 - never let a write error break the call
        logger.exception("log_lead failed for outcome=%s", outcome)
        return None
    ud.logged_outcomes.add(outcome)
    return record

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


# --------------------------------------------------------------------- project facts
ProjectTopic = Literal[
    "overview",
    "configurations",
    "unit_details",
    "amenities_building",
    "amenities_township",
    "location",
    "specifications",
    "pricing",
    "possession",
    "rera",
]


@function_tool
async def get_detailed_project_info(
    context: RunContext[CallUserdata],
    topic: ProjectTopic,
    config: Config | None = None,
) -> dict[str, Any]:
    """Look up a detail about The Canopy that ISN'T already in your call brief.

    The brief already covers the basics (configs, carpet sizes, price band,
    location, possession, amenities headline, RERA) — answer those directly. Use
    this only to go deeper: a specific configuration's layouts/carpet, the full
    township amenity list, the full spec sheet, the pricing breakdown, etc.

    Args:
        topic: what to look up — one of overview, configurations, unit_details,
            amenities_building, amenities_township, location, specifications,
            pricing, possession, rera.
        config: "2 BHK" or "3 BHK", for unit_details or pricing.
    """
    k = context.userdata.knowledge
    normalized = _normalize_config(config)
    if normalized in CONFIG_INTERESTS:
        context.userdata.config_interest = normalized

    if topic == "configurations":
        return {"configs": k.configs(), "summary": {c: k.config_summary(c) for c in k.configs()}}

    if topic == "unit_details":
        units = k.unit_types_for(config) if config else k.unit_types()
        if not units:
            return {
                "found": False,
                "message": f"No layout matches {config!r}; The Canopy has only 2 BHK and 3 BHK.",
            }
        carpets = sorted(u["carpet_sqft"] for u in units)
        return {
            "found": True,
            "config": normalized,
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
        }

    if topic == "amenities_township":
        t = k.amenities_township()
        return {
            "scope": "township",
            "amenities": t["list"],
            "caveat": t["caveat"],
            "must_state_caveat": True,
        }

    if topic == "amenities_building":
        return {"scope": "building", "amenities": k.amenities_building(), "must_state_caveat": False}

    if topic == "location":
        loc = k.location()
        return {
            "address": loc["address"],
            "township": k.township(),
            "township_size_acres": loc["township_size_acres"],
            "commute": loc["commute"]["phrasing"],
            "positioning": k.building()["positioning"],
            "nearby": k.estimated_connectivity().get("places", []),
            "note": k.estimated_connectivity().get(
                "traffic_disclaimer", "Distances are APPROXIMATE — say 'roughly', never exact."
            ),
        }

    if topic == "specifications":
        return {
            "specifications": k.specifications(),
            "note": "Brands are 'or equivalent' — present as indicative, not guaranteed.",
        }

    if topic == "pricing":
        return {
            "pricing": k.pricing(config),
            "indicative_only": True,
            "price_basis": k.price_basis(),
            "disclaimer": k.commercial_disclaimer(),
        }

    if topic == "possession":
        return {"possession": k.possession(), "indicative_only": True}

    if topic == "rera":
        return k.rera()

    # overview / fallback
    return {
        "project": k.project_name(),
        "developer": k.developer(),
        "configs": k.configs(),
        "location": k.location()["address"],
        "rera": k.rera()["number"],
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
        ud.site_visit_declined = True  # terminal → not visiting; don't re-offer
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
async def request_site_visit(
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
    ud.site_visit_offered = True
    ud.cta_offer_count += 1
    kwargs = _current_lead_kwargs(ud, notes=f"Site visit requested, preferred: {preferred_date}")
    kwargs["name"] = resolved_name
    kwargs["caller_phone"] = resolved_phone
    kwargs["preferred_datetime_raw"] = preferred_date
    kwargs["preferred_datetime_iso"] = resolve_datetime(preferred_date)
    kwargs["timezone_name"] = IST_NAME
    record = await _log_with_filler(
        context, ud, outcome="site_visit_requested", consent_to_be_contacted=True, **kwargs
    )
    if record is None:
        return {"logged": False, "instruction": _PERSIST_FAILED}
    ud.lead_logged = True
    # C1: speak a deterministic confirmation directly (no extra LLM turn), then
    # stop the model from generating a second reply.
    context.session.say(
        _cta_confirmation("visit", resolved_name, preferred_date, ud.preferred_language)
    )
    raise StopResponse()


@function_tool
async def request_callback(
    context: RunContext[CallUserdata],
    preferred_time: str,
    name: str | None = None,
    phone: str | None = None,
    notes: str = "",
) -> dict[str, Any]:
    """Capture a CALLBACK request (the fallback next step when a site visit isn't set). Ask the caller what time suits them BEFORE calling this — don't call it with a guessed time.

    Args:
        preferred_time: The caller's preferred callback time in their own words, e.g. "kal shaam" or "evening after 6".
        name: Caller's name if stated anywhere in the call.
        phone: Best callback number if stated (falls back to the known caller number).
        notes: Anything else the team should know before calling back.
    """
    ud = context.userdata
    if name:
        ud.caller_name = name
    ud.next_step = "callback_requested"
    ud.closing_attempted = True
    ud.callback_offered = True
    ud.cta_offer_count += 1
    note = f"Callback requested, preferred time: {preferred_time}"
    if notes:
        note = f"{note}. {notes}"
    kwargs = _current_lead_kwargs(ud, notes=note)
    kwargs["name"] = name or ud.caller_name
    kwargs["caller_phone"] = phone or ud.caller_phone
    kwargs["preferred_datetime_raw"] = preferred_time
    kwargs["preferred_datetime_iso"] = resolve_datetime(preferred_time)
    kwargs["timezone_name"] = IST_NAME
    record = await _log_with_filler(
        context, ud, outcome="callback_requested", consent_to_be_contacted=True, **kwargs
    )
    if record is None:
        return {"logged": False, "instruction": _PERSIST_FAILED}
    ud.lead_logged = True
    # C1: deterministic confirmation, no extra LLM turn.
    context.session.say(
        _cta_confirmation("callback", name or ud.caller_name, preferred_time, ud.preferred_language)
    )
    raise StopResponse()


@function_tool
async def log_terminal_outcome(
    context: RunContext[CallUserdata],
    outcome: Outcome,
    name: str | None = None,
    phone: str | None = None,
    notes: str = "",
    consent_to_be_contacted: bool = True,
) -> dict[str, Any]:
    """Persist a terminal / catch-all lead outcome (not_interested, already_purchased, wrong_number, info_only_no_lead, etc.). Use request_site_visit / request_callback for those specific CTAs instead.

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
        ud, outcome=outcome, consent_to_be_contacted=consent_to_be_contacted, **kwargs
    )
    if record is None:
        return {"logged": False, "instruction": _PERSIST_FAILED}
    ud.lead_logged = True
    return {"logged": True, "lead_id": record.get("lead_id"), "qualification": ud.qualification_snapshot()}


@function_tool
async def escalate_to_human(context: RunContext[CallUserdata], reason: str) -> Agent:
    """Hand off when the caller asks for a human, is upset, or the topic is outside your authority (payments, legal/title, price negotiation). Don't argue or try to resolve it first.

    Args:
        reason: Brief reason, e.g. "caller asked for a human" or "caller is upset".
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
    get_detailed_project_info,
    request_site_visit,
    request_callback,
    escalate_to_human,
    log_terminal_outcome,
    end_call,
]
