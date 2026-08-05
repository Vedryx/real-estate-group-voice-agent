"""Function-tool catalog for The Real Estate Group assistant (plan.md §6).

Every tool here is the *only* path to a fact the agent may speak: a
price, amenity, possession date, or metro claim must come from a tool
result, never from the LLM's own knowledge (plan.md §7 rule 1). Tools
return small dicts with an explicit "not confirmed" / "no match"
signal rather than omitting data, so the caller-facing behavior stays
an honest "I don't know" instead of a silent hallucination.
"""

from __future__ import annotations

from typing import Any

from livekit.agents import Agent, RunContext, function_tool
from livekit.agents.llm import ToolError
from livekit.agents.voice.speech_handle import SpeechHandle

from agent import data_store as ds
from agent.data_store import VALID_OUTCOMES
from agent.escalation_agent import HumanEscalationAgent
from agent.state import (
    INTEREST_STATUSES,
    PURCHASE_PURPOSES,
    PURCHASE_TIMELINES,
    CallUserdata,
)

def _format_inr_lakh(value: float) -> str:
    """Format an INR lakh amount for natural speech.

    Converts to crore + lakh above 99 lakh (e.g. 130 -> "1 crore 30 lakh",
    235 -> "2 crore 35 lakh") - TTS reads a bare 3-digit lakh figure as
    disconnected digits rather than a number, and callers expect crore
    notation once a price crosses 1 crore anyway.
    """

    def _num(n: float) -> str:
        return str(int(n)) if n == int(n) else f"{n:g}"

    if value < 100:
        return f"{_num(value)} lakh"
    crore, remainder = divmod(value, 100)
    if remainder == 0:
        return f"{_num(crore)} crore"
    return f"{_num(crore)} crore {_num(remainder)} lakh"


def _augment_units_with_display(units: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Copy unit_options with *_display price strings added - never mutates
    the shared DataStore project data in place."""
    out = []
    for u in units:
        u2 = dict(u)
        if u2.get("price_inr_lakh_min") is not None:
            u2["price_inr_lakh_min_display"] = _format_inr_lakh(u2["price_inr_lakh_min"])
        if u2.get("price_inr_lakh_max") is not None:
            u2["price_inr_lakh_max_display"] = _format_inr_lakh(u2["price_inr_lakh_max"])
        out.append(u2)
    return out


def _project_summary(
    project: dict[str, Any], bhk: str | None = None
) -> dict[str, Any]:
    units = project["unit_options"]
    if bhk:
        needle = _normalize(bhk)
        units = [unit for unit in units if needle in _normalize(unit["label"])]
    prices = [
        unit["price_inr_lakh_min"]
        for unit in units
        if unit.get("price_inr_lakh_min") is not None
    ]
    standout_amenity = (project.get("amenities_highlights") or [None])[0]
    starting_price = min(prices) if prices else None
    return {
        "id": project["id"],
        "name": project["name"],
        "developer": project["developer"],
        "locality": project["locality"],
        "bhk_available": project["bhk_available"],
        "starting_price_inr_lakh": starting_price,
        "starting_price_display": _format_inr_lakh(starting_price) if starting_price is not None else None,
        "standout_amenity": standout_amenity,
        "has_unverified_fields": bool(project.get("unverified_fields")),
    }


def _normalize(text: str) -> str:
    return text.strip().strip("\"'").upper().replace(" ", "")


@function_tool
async def record_lead_qualification(
    context: RunContext[CallUserdata],
    interest_status: str,
    purchase_timeline: str | None = None,
    purchase_purpose: str | None = None,
    developer_preference: str | None = None,
) -> dict[str, Any]:
    """Record explicit outbound qualification signals without guessing them.

    Call after the caller answers whether the original enquiry is still
    relevant. Update it later if they give a timeline, purpose, or preferred
    developer. For terminal interest states, stop qualifying and close.

    Args:
        interest_status: One of active, casual, not_interested, already_purchased, accidental_click, wrong_person, opted_out.
        purchase_timeline: One of unknown, within_3_months, 3_to_6_months, over_6_months, only when stated or clearly implied.
        purchase_purpose: One of unknown, self_use, investment, only when stated.
        developer_preference: Preferred builder/developer, if stated.
    """
    if interest_status not in INTEREST_STATUSES - {"unknown"}:
        raise ToolError(
            f"invalid interest_status {interest_status!r}; must be one of "
            f"{sorted(INTEREST_STATUSES - {'unknown'})}"
        )
    if purchase_timeline is not None and purchase_timeline not in PURCHASE_TIMELINES:
        raise ToolError(
            f"invalid purchase_timeline {purchase_timeline!r}; must be one of "
            f"{sorted(PURCHASE_TIMELINES)}"
        )
    if purchase_purpose is not None and purchase_purpose not in PURCHASE_PURPOSES:
        raise ToolError(
            f"invalid purchase_purpose {purchase_purpose!r}; must be one of "
            f"{sorted(PURCHASE_PURPOSES)}"
        )

    ud = context.userdata
    ud.interest_status = interest_status
    if purchase_timeline is not None:
        ud.purchase_timeline = purchase_timeline
        ud.purchase_timeline_asked = True
    if purchase_purpose is not None:
        ud.purchase_purpose = purchase_purpose
    if developer_preference:
        ud.developer_preference = developer_preference.strip()

    return {
        "recorded": True,
        "conversation_stage": ud.conversation_stage,
        "qualification": ud.qualification_snapshot(),
    }


@function_tool
async def search_projects(
    context: RunContext[CallUserdata],
    city: str,
    locality: str | None = None,
    bhk: str | None = None,
    budget_min_lakh: float | None = None,
    budget_max_lakh: float | None = None,
    property_type: str = "residential",
    developer: str | None = None,
    relax_locality: bool = False,
    relax_bhk: bool = False,
    relax_budget: bool = False,
    relax_developer: bool = False,
) -> dict[str, Any]:
    """Find The Real Estate Group projects matching a caller's stated preferences.

    Only call this once the city has been confirmed as served. Do not
    use it to test whether a city is served - check the "served" flag
    it returns, and if false, call log_out_of_area_interest instead of
    retrying with a different city.

    Args:
        city: City the caller is interested in, e.g. "Pune".
        locality: Preferred locality/area within the city, if stated, e.g. "Hinjewadi".
        bhk: BHK preference, e.g. "2BHK" or "3BHK".
        budget_min_lakh: Minimum budget in INR lakh, if the caller stated one.
        budget_max_lakh: Maximum budget in INR lakh, if the caller stated one.
        property_type: "residential" or "commercial". The Real Estate Group currently has no confirmed commercial inventory - see the returned message if "commercial" is requested.
        developer: Preferred builder/developer, if the caller stated one, e.g. "Kolte Patil".
        relax_locality: Set true only after the caller explicitly agrees to broaden/clear their stored locality.
        relax_bhk: Set true only after the caller explicitly agrees to broaden/clear their stored BHK.
        relax_budget: Set true only after the caller explicitly agrees to broaden/clear their stored budget.
        relax_developer: Set true only after the caller explicitly agrees to consider other developers.
    """
    ud = context.userdata
    store = ud.data_store

    if relax_locality:
        ud.requested_locality = None
    if relax_bhk:
        ud.bhk_preference = None
    if relax_budget:
        ud.budget_min_lakh = None
        ud.budget_max_lakh = None
    if relax_developer:
        ud.developer_preference = None

    ud.requested_city = city
    ud.requested_locality = locality or ud.requested_locality
    ud.bhk_preference = bhk or ud.bhk_preference
    ud.budget_min_lakh = budget_min_lakh if budget_min_lakh is not None else ud.budget_min_lakh
    ud.budget_max_lakh = budget_max_lakh if budget_max_lakh is not None else ud.budget_max_lakh
    ud.property_type_requested = property_type
    ud.developer_preference = developer or ud.developer_preference

    effective_locality = ud.requested_locality
    effective_bhk = ud.bhk_preference
    effective_budget_min = ud.budget_min_lakh
    effective_budget_max = ud.budget_max_lakh
    effective_developer = ud.developer_preference

    served_cities = {c.lower() for c in store.served_cities()}
    if city.strip().lower() not in served_cities:
        ud.inventory_fit = "no_exact_match"
        ud.matching_project_ids = []
        return {
            "served": False,
            "matches": [],
            "message": (
                f"The Real Estate Group has no listings in {city!r}. Do not invent "
                "inventory - tell the caller honestly and call "
                "log_out_of_area_interest to capture the demand signal."
            ),
            "qualification": ud.qualification_snapshot(),
        }

    if property_type == "commercial":
        ud.inventory_fit = "no_exact_match"
        ud.matching_project_ids = []
        return {
            "served": True,
            "matches": [],
            "message": (
                "The Real Estate Group has no confirmed standalone commercial/shop/office "
                "inventory. Tell the caller honestly rather than offering a "
                "residential project as a substitute, and offer to log their interest."
            ),
            "qualification": ud.qualification_snapshot(),
        }

    matches = []
    for project in store.projects:
        if project["city"].strip().lower() != city.strip().lower():
            continue
        if effective_locality and effective_locality.strip().lower() not in project["locality"].lower():
            continue
        if effective_developer:
            developer_needle = effective_developer.strip().lower()
            project_developer = project["developer"].lower()
            if developer_needle not in project_developer and project_developer not in developer_needle:
                continue
        if effective_bhk and _normalize(effective_bhk) not in {
            _normalize(b) for b in project["bhk_available"]
        }:
            continue
        if effective_budget_min is not None or effective_budget_max is not None:
            candidate_units = project["unit_options"]
            if effective_bhk:
                bhk_needle = _normalize(effective_bhk)
                candidate_units = [
                    unit for unit in candidate_units if bhk_needle in _normalize(unit["label"])
                ]
            prices = [
                v
                for u in candidate_units
                for v in (u.get("price_inr_lakh_min"), u.get("price_inr_lakh_max"))
                if v is not None
            ]
            if not prices:
                continue
            lo, hi = min(prices), max(prices)
            if effective_budget_max is not None and lo > effective_budget_max:
                continue
            if effective_budget_min is not None and hi < effective_budget_min:
                continue
        matches.append(project)

    for project in matches:
        ud.note_project_discussed(project["id"])

    if not matches:
        ud.inventory_fit = "no_exact_match"
        ud.matching_project_ids = []
        return {
            "served": True,
            "matches": [],
            "message": (
                "No project matched these criteria exactly. Tell the caller "
                "honestly rather than stretching a match - consider suggesting "
                "the nearest alternative (relax budget or BHK by one step) as a "
                "question, not a claim."
            ),
            "qualification": ud.qualification_snapshot(),
        }

    ud.inventory_fit = "exact_match"
    ud.matching_project_ids = [project["id"] for project in matches]
    return {
        "served": True,
        "matches": [_project_summary(p, effective_bhk) for p in matches[:3]],
        "total_exact_matches": len(matches),
        "qualification": ud.qualification_snapshot(),
    }


@function_tool
async def get_project_details(context: RunContext[CallUserdata], project_id: str) -> dict[str, Any]:
    """Get the full stored record for one project once the caller has narrowed down to a specific option, including any unverified_fields flags.

    Args:
        project_id: The project's id, e.g. "lodha-sylvan" (from a prior search_projects result).
    """
    project = context.userdata.data_store.get_project(project_id)
    if project is None:
        return {
            "found": False,
            "message": f"No project with id {project_id!r}. Ask a clarifying question about which project the caller means rather than guessing.",
        }
    context.userdata.note_project_discussed(project_id)
    project_out = dict(project)
    project_out["unit_options"] = _augment_units_with_display(project["unit_options"])
    return {"found": True, "project": project_out}


@function_tool
async def get_amenities(context: RunContext[CallUserdata], project_id: str) -> dict[str, Any]:
    """Look up amenities for a project.

    Args:
        project_id: The project's id.
    """
    project = context.userdata.data_store.get_project(project_id)
    if project is None:
        return {"found": False, "message": f"No project with id {project_id!r}."}
    context.userdata.note_project_discussed(project_id)

    full_list = project.get("amenities_full_list") or []
    if full_list:
        return {"found": True, "amenities": full_list, "confirmed_full_list": True}

    return {
        "found": True,
        "amenities": project.get("amenities_highlights") or [],
        "confirmed_full_list": False,
        "message": (
            "Only highlight amenities are confirmed for this project, not a "
            "full list - say so honestly rather than implying this is exhaustive."
        ),
    }


@function_tool
async def get_pricing(
    context: RunContext[CallUserdata], project_id: str, bhk: str | None = None
) -> dict[str, Any]:
    """Get precise unit pricing and carpet size for a project, optionally filtered by BHK. Only carpet area is in the data - never convert to/guess a built-up area figure.

    Args:
        project_id: The project's id.
        bhk: Filter to a specific BHK type, e.g. "2BHK". Omit to return all unit options.
    """
    project = context.userdata.data_store.get_project(project_id)
    if project is None:
        return {"found": False, "message": f"No project with id {project_id!r}."}
    context.userdata.note_project_discussed(project_id)

    units = project["unit_options"]
    if bhk:
        needle = _normalize(bhk)
        units = [u for u in units if needle in _normalize(u["label"])]

    result: dict[str, Any] = {
        "found": True,
        "project_name": project["name"],
        "units": _augment_units_with_display(units),
    }
    unverified = set(project.get("unverified_fields") or [])
    if unverified & {"price_inr_lakh_min", "price_inr_lakh_max", "carpet_sqft"}:
        result["message"] = (
            "Pricing/size for this project is flagged unverified or conflicting "
            "in source data - hedge accordingly (e.g. 'our team will confirm the "
            "exact figure') rather than stating it with full confidence."
        )
    return result


@function_tool
async def check_metro_proximity(context: RunContext[CallUserdata], project_id: str) -> dict[str, Any]:
    """Check whether metro-station proximity is confirmed for a project. Never claim metro proximity that isn't returned here - this is a common hallucination risk.

    Args:
        project_id: The project's id.
    """
    project = context.userdata.data_store.get_project(project_id)
    if project is None:
        return {"found": False, "message": f"No project with id {project_id!r}."}
    context.userdata.note_project_discussed(project_id)

    metro = project.get("metro_proximity")
    if metro:
        return {"found": True, "confirmed": True, "metro_proximity": metro}
    return {
        "found": True,
        "confirmed": False,
        "message": (
            "Metro proximity is not confirmed in the data for this project. "
            "Tell the caller honestly that it isn't confirmed rather than "
            "guessing proximity or absence."
        ),
    }


@function_tool
async def get_nearby_projects_to_workplace(
    context: RunContext[CallUserdata], workplace_area: str
) -> dict[str, Any]:
    """Find projects reasonably close to a caller's stated workplace/office area, when they describe a commute preference instead of a residential area.

    Distances come from a static approximate table, not a live maps API -
    always phrase any commute claim as approximate (e.g. "roughly a
    15-20 minute drive"), never an exact number.

    Args:
        workplace_area: The workplace/office area the caller mentioned, e.g. "Hinjewadi" or "near Kharadi".
    """
    ud = context.userdata
    ud.workplace_area = workplace_area
    needle = workplace_area.strip().lower()

    hubs = ud.data_store.workplace_proximity.get("hubs", [])
    matched_hub = None
    for hub in hubs:
        names = [hub["workplace_area"], *hub.get("aliases", [])]
        if any(needle in n.lower() or n.lower() in needle for n in names):
            matched_hub = hub
            break

    if matched_hub is None:
        return {
            "matched_hub": False,
            "matches": [],
            "message": (
                f"No known work-hub proximity data for {workplace_area!r}. Say so "
                "honestly rather than forcing a match, and offer to log the interest."
            ),
        }

    project_ids = matched_hub.get("nearby_project_ids", [])
    if not project_ids:
        return {
            "matched_hub": True,
            "hub": matched_hub["workplace_area"],
            "matches": [],
            "message": (
                f"None of The Real Estate Group's current listings are close to "
                f"{matched_hub['workplace_area']}. Say so honestly rather than "
                "forcing a match."
            ),
        }

    matches = []
    for project_id in project_ids:
        project = ud.data_store.get_project(project_id)
        if project:
            matches.append(_project_summary(project))
            ud.note_project_discussed(project_id)

    return {
        "matched_hub": True,
        "hub": matched_hub["workplace_area"],
        "matches": matches,
        "approximate_only": True,
        "message": (
            "These distances are directional estimates only, not a live "
            "calculation - phrase any commute claim as approximate, never an "
            "exact number."
        ),
    }


def _current_lead_kwargs(ud: CallUserdata, notes: str) -> dict[str, Any]:
    budget = (
        (ud.budget_min_lakh, ud.budget_max_lakh)
        if (ud.budget_min_lakh is not None or ud.budget_max_lakh is not None)
        else None
    )
    qualification = ud.qualification_snapshot()
    return {
        "caller_phone": ud.caller_phone,
        "name": ud.caller_name,
        "preferred_language": ud.preferred_language,
        "requested_city": ud.requested_city,
        "requested_locality": ud.requested_locality,
        "bhk_preference": ud.bhk_preference,
        "budget_range_inr_lakh": budget,
        "property_type_requested": ud.property_type_requested,
        "developer_preference": ud.developer_preference,
        "projects_discussed": list(ud.projects_discussed),
        "matching_project_ids": list(ud.matching_project_ids),
        "workplace_area": ud.workplace_area,
        "source_channel": ud.source_channel,
        "source_campaign": ud.source_campaign,
        "source_project": ud.source_project,
        "source_enquiry_id": ud.source_enquiry_id,
        "consent_reference": ud.consent_reference,
        "interest_status": ud.interest_status,
        "purchase_timeline": ud.purchase_timeline,
        "purchase_timeline_asked": ud.purchase_timeline_asked,
        "purchase_purpose": ud.purchase_purpose,
        "inventory_fit": ud.inventory_fit,
        "next_step": ud.next_step,
        "closing_attempted": ud.closing_attempted,
        "qualification_status": qualification["qualification_status"],
        "lead_temperature": qualification["lead_temperature"],
        "qualification_reasons": qualification["qualification_reasons"],
        "notes": notes,
    }


def _apply_outcome_to_state(ud: CallUserdata, outcome: str) -> None:
    """Keep persistence outcome and deterministic qualification state aligned."""
    terminal_interest = {
        "not_interested": "not_interested",
        "already_purchased": "already_purchased",
        "accidental_click": "accidental_click",
        "wrong_number": "wrong_person",
        "opted_out": "opted_out",
    }
    if outcome in terminal_interest:
        ud.interest_status = terminal_interest[outcome]
        ud.next_step = "no_followup"
        ud.closing_attempted = True
    elif outcome == "site_visit_requested":
        ud.next_step = "site_visit_requested"
        ud.closing_attempted = True
    elif outcome == "callback_requested":
        ud.next_step = "callback_requested"
        ud.closing_attempted = True
    elif outcome == "nurture_lead":
        ud.next_step = "future_followup"
        ud.closing_attempted = True
    elif outcome == "qualified_lead":
        ud.closing_attempted = True
    elif outcome in {"info_only_no_lead", "spam_or_abandoned"}:
        ud.next_step = "no_followup"
        ud.closing_attempted = True


@function_tool
async def log_lead(
    context: RunContext[CallUserdata],
    outcome: str,
    name: str | None = None,
    phone: str | None = None,
    notes: str = "",
    consent_to_be_contacted: bool = True,
) -> dict[str, Any]:
    """Persist a lead record. Call once per distinct outcome - for multiple family members with different needs on one call, call this again for the second lead.

    Args:
        outcome: One of qualified_lead, callback_requested, site_visit_requested, nurture_lead, not_interested, already_purchased, accidental_click, wrong_number, not_serviceable_area, info_only_no_lead, escalation_needed, opted_out, spam_or_abandoned.
        name: Caller's name, if they've stated it anywhere in the conversation so far - extract it from their own words even if they gave it casually rather than in response to a direct "what's your name" question. Don't pass null if they already told you.
        phone: Best callback number, if they've stated it anywhere in the conversation so far - same rule as name, extract it even if given casually (falls back to the caller's SIP number if known).
        notes: Free-text notes capturing anything relevant not covered by other fields.
        consent_to_be_contacted: Whether the caller consented to follow-up contact. Set False if they asked not to be contacted.
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
    record = ds.log_lead(
        outcome=outcome, consent_to_be_contacted=consent_to_be_contacted, **kwargs
    )
    ud.lead_logged = True
    return {
        "logged": True,
        "lead_id": record["lead_id"],
        "qualification": ud.qualification_snapshot(),
    }


@function_tool
async def log_out_of_area_interest(
    context: RunContext[CallUserdata],
    requested_city_or_locality: str,
    name: str | None = None,
    phone: str | None = None,
) -> dict[str, Any]:
    """Log demand signal when a caller wants a city or locality The Real Estate Group doesn't currently serve. Use this instead of inventing inventory for the area.

    Args:
        requested_city_or_locality: The city or locality the caller asked about that has no live inventory.
        name: Caller's name, if they've stated it anywhere in the conversation so far - extract it from their own words even if given casually. Don't pass null if they already told you.
        phone: Callback number, if they've stated it anywhere in the conversation so far - same rule as name.
    """
    ud = context.userdata
    if name:
        ud.caller_name = name
    ud.inventory_fit = "no_exact_match"
    ud.matching_project_ids = []
    ud.next_step = "future_followup"
    ud.closing_attempted = True

    ud.requested_city = requested_city_or_locality
    kwargs = _current_lead_kwargs(
        ud, notes=f"Requested unserved area: {requested_city_or_locality}"
    )
    kwargs["name"] = name or ud.caller_name
    kwargs["caller_phone"] = phone or ud.caller_phone
    record = ds.log_lead(
        outcome="not_serviceable_area", consent_to_be_contacted=True, **kwargs
    )
    ud.lead_logged = True
    return {
        "logged": True,
        "lead_id": record["lead_id"],
        "qualification": ud.qualification_snapshot(),
    }


@function_tool
async def schedule_site_visit(
    context: RunContext[CallUserdata],
    project_id: str,
    preferred_date: str,
    name: str | None = None,
    phone: str | None = None,
) -> dict[str, Any]:
    """Capture a site-visit request. No calendar integration in v1 - this logs intent for a human to action and confirm.

    Args:
        project_id: The project the caller wants to visit.
        preferred_date: The caller's preferred date/time in their own words, e.g. "this Saturday afternoon".
        name: Caller's name; omit if already present in outbound lead context.
        phone: Contact number; omit to use the known outbound/SIP caller number.
    """
    ud = context.userdata
    if name:
        ud.caller_name = name

    project = ud.data_store.get_project(project_id)
    if project is None:
        raise ToolError(f"No project with id {project_id!r}; ask which project they mean.")
    ud.note_project_discussed(project_id)
    project_name = project["name"]
    resolved_name = name or ud.caller_name
    resolved_phone = phone or ud.caller_phone
    if not resolved_name:
        raise ToolError("A caller name is required before saving a site-visit request.")
    if not resolved_phone:
        raise ToolError("A callback phone number is required before saving a site-visit request.")
    ud.next_step = "site_visit_requested"
    ud.closing_attempted = True

    kwargs = _current_lead_kwargs(
        ud, notes=f"Site visit requested for {project_name}, preferred: {preferred_date}"
    )
    kwargs["name"] = resolved_name
    kwargs["caller_phone"] = resolved_phone
    record = ds.log_lead(outcome="site_visit_requested", consent_to_be_contacted=True, **kwargs)
    ud.lead_logged = True
    return {
        "logged": True,
        "lead_id": record["lead_id"],
        "project_name": project_name,
        "status": "requested_pending_human_confirmation",
        "message": (
            "The site-visit request is saved but not yet confirmed. Tell the caller "
            "that the team will call to confirm the final slot; never say it is booked."
        ),
        "qualification": ud.qualification_snapshot(),
    }


@function_tool
async def escalate_to_human(context: RunContext[CallUserdata], reason: str) -> Agent:
    """Hand off when the caller explicitly asks for a human, is upset or frustrated, or the topic is outside the AI's authority (payments, legal/title questions, price negotiation beyond published tokens). Don't argue or try to resolve it yourself first.

    Args:
        reason: Brief reason for escalation, e.g. "caller is angry about a past experience" or "caller asked for a human".
    """
    ud = context.userdata
    record = ds.log_lead(outcome="escalation_needed", consent_to_be_contacted=True, **_current_lead_kwargs(ud, notes=reason))
    ud.lead_logged = True
    context.disallow_interruptions()
    _ = record
    return HumanEscalationAgent(reason=reason)


@function_tool
async def end_call(context: RunContext[CallUserdata], outcome: str) -> str:
    """Gracefully end the call, tagging the final outcome. If no lead has been logged yet this session (e.g. a quick "not interested" close), this call also logs one using whatever fields were collected. This must be the last tool call in a turn - do not generate further text after it resolves beyond a brief goodbye.

    Args:
        outcome: One of qualified_lead, callback_requested, site_visit_requested, nurture_lead, not_interested, already_purchased, accidental_click, wrong_number, not_serviceable_area, info_only_no_lead, escalation_needed, opted_out, spam_or_abandoned.
    """
    if outcome not in VALID_OUTCOMES:
        raise ToolError(f"invalid outcome {outcome!r}; must be one of {sorted(VALID_OUTCOMES)}")

    ud = context.userdata
    _apply_outcome_to_state(ud, outcome)
    if not ud.lead_logged:
        ds.log_lead(
            outcome=outcome,
            consent_to_be_contacted=outcome != "opted_out",
            **_current_lead_kwargs(ud, notes="auto-logged at end_call"),
        )
        ud.lead_logged = True

    def _on_speech_done(_: SpeechHandle) -> None:
        context.session.shutdown()

    context.speech_handle.add_done_callback(_on_speech_done)
    return "Outcome logged. Say a brief, warm goodbye now - nothing else."


ALL_TOOLS = [
    record_lead_qualification,
    search_projects,
    get_project_details,
    get_amenities,
    get_pricing,
    check_metro_proximity,
    get_nearby_projects_to_workplace,
    log_lead,
    log_out_of_area_interest,
    schedule_site_visit,
    escalate_to_human,
    end_call,
]
