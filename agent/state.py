"""Per-call session state (AgentSession userdata) for the Canopy agent.

Single project (The Canopy). Tracks what the caller has told us so the agent
doesn't re-ask, carries the read-only Canopy knowledge, and exposes a *light*
stage signal (opening -> engaged -> closing) plus an internal qualification
snapshot. The stage does NOT gate answers — the agent stays reactive; the
stage just tells it where the call is.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from agent.canopy import CanopyKnowledge


SUPPORTED_LANGUAGE_CODES = frozenset({"hi-IN", "mr-IN", "en-IN"})

INTEREST_STATUSES = frozenset(
    {
        "unknown",
        "active",
        "casual",
        "not_interested",
        "already_purchased",
        "accidental_click",
        "wrong_person",
        "opted_out",
    }
)
PURCHASE_TIMELINES = frozenset(
    {"unknown", "within_3_months", "3_to_6_months", "over_6_months"}
)
PURCHASE_PURPOSES = frozenset({"unknown", "self_use", "investment"})
CONFIG_INTERESTS = frozenset({"2 BHK", "3 BHK"})
NEXT_STEPS = frozenset(
    {"none", "site_visit_requested", "callback_requested", "future_followup", "no_followup"}
)

_TERMINAL_INTEREST = {
    "not_interested": "Caller said they are no longer interested",
    "already_purchased": "Caller has already purchased a property",
    "accidental_click": "Caller said the enquiry was accidental",
    "wrong_person": "The contacted person is not the intended lead",
    "opted_out": "Caller requested no further contact",
}

_BACKCHANNELS = frozenset(
    {
        "achha",
        "acha",
        "ahe",
        "haan",
        "ha",
        "hai",
        "hmm",
        "hm",
        "ji",
        "theek",
        "अच्छा",
        "आहे",
        "जी",
        "बरं",
        "हो",
        "है",
        "हां",
        "हाँ",
        "yes",
        "yeah",
        "yep",
        "ok",
        "okay",
        "ठीक",
    }
)

_EXPLICIT_LANGUAGE_PATTERNS = (
    (
        "hi-IN",
        (
            r"\b(?:switch|change|speak|talk|continue|use|prefer|reply|respond)\s+(?:in|to\s+)?hindi\b",
            r"\bhindi\s+(?:please|mein|me|mai|bol(?:o|iye)?|baat)\b",
            r"(?:हिंदी|हिन्दी)\s*(?:में|मे|बोल|बात|भाषा)",
        ),
    ),
    (
        "mr-IN",
        (
            r"\b(?:switch|change|speak|talk|continue|use|prefer|reply|respond)\s+(?:in|to\s+)?marathi\b",
            r"\bmarathi\s+(?:please|madhe|madhye|bol(?:a|u)?|bhasha)\b",
            r"मराठी\s*(?:त|मध्ये)?\s*(?:बोल|बोला|भाषा|चालेल)",
        ),
    ),
    (
        "en-IN",
        (
            r"\b(?:switch|change|speak|talk|continue|use|prefer|reply|respond)\s+(?:in|to\s+)?english\b",
            r"\benglish\s+(?:please|mein|me|madhe|madhye|bol(?:o|a|iye)?|bhasha)\b",
            r"(?:इंग्लिश|अंग्रेजी|अंग्रेज़ी)\s*(?:में|मे|मध्ये|बोल|बात|भाषा)",
        ),
    ),
)


def explicit_language_request(transcript: str) -> str | None:
    """Return a requested language only when the transcript asks to switch."""
    text = " ".join(transcript.casefold().split())
    for code, patterns in _EXPLICIT_LANGUAGE_PATTERNS:
        if any(re.search(pattern, text) for pattern in patterns):
            return code
    return None


def is_substantive_language_sample(transcript: str) -> bool:
    """Reject short/backchannel turns whose auto-detected language is unreliable."""
    tokens = re.findall(r"[^\W_]+", transcript.casefold(), flags=re.UNICODE)
    content_tokens = [token for token in tokens if token not in _BACKCHANNELS]
    letter_count = sum(len(token) for token in content_tokens if not token.isdigit())
    return len(content_tokens) >= 2 and letter_count >= 6


def update_qualification_from_text(ud: "CallUserdata", text: str) -> None:
    """Update qualification signals from the caller's words — a cheap, no-LLM
    heuristic run OUTSIDE the tool-selection loop (D4). This keeps the CTA gate
    fed even though the basics now answer straight from the brief (no tool call).
    Conservative: only sets a signal on a clear cue.
    """
    if not text:
        return
    t = text.lower()

    if any(k in t for k in ("3 bhk", "3bhk", "three bhk", "teen bhk", "तीन बीएचके", "3 बीएचके")):
        ud.config_interest = "3 BHK"
    elif any(k in t for k in ("2 bhk", "2bhk", "two bhk", "do bhk", "दो बीएचके", "2 बीएचके")):
        ud.config_interest = "2 BHK"

    if any(k in t for k in ("investment", "invest", "nivesh", "निवेश")):
        ud.purchase_purpose = "investment"
    elif any(k in t for k in ("self use", "self-use", "rehne", "khud ke", "family", "रहने")):
        ud.purchase_purpose = "self_use"

    if any(k in t for k in ("3 month", "teen mahine", "3 mahine", "jald", "turant")):
        ud.purchase_timeline = "within_3_months"
        ud.purchase_timeline_asked = True
    elif any(k in t for k in ("6 month", "chhe mahine", "6 mahine")):
        ud.purchase_timeline = "3_to_6_months"
        ud.purchase_timeline_asked = True
    elif any(k in t for k in ("saal", "year", "baad mein", "next year", "later")):
        ud.purchase_timeline = "over_6_months"
        ud.purchase_timeline_asked = True

    # A concrete signal means they're genuinely engaged.
    if (
        ud.interest_status == "unknown"
        and (ud.config_interest or ud.purchase_purpose != "unknown" or ud.purchase_timeline_asked)
    ):
        ud.interest_status = "active"


def select_language(
    current: str | None, transcript: str, detected_language: str | None
) -> str | None:
    """Resolve sticky language state from one final transcription event."""
    explicit = explicit_language_request(transcript)
    if explicit is not None:
        return explicit
    if current is not None:
        return current
    if detected_language not in SUPPORTED_LANGUAGE_CODES:
        return None
    return detected_language if is_substantive_language_sample(transcript) else None


@dataclass
class CallUserdata:
    knowledge: CanopyKnowledge

    caller_phone: str | None = None
    caller_name: str | None = None
    preferred_language: str | None = None

    # Outbound campaign context supplied by the dispatcher (parsed in worker.py).
    source_channel: str | None = None
    source_campaign: str | None = None
    source_project: str | None = None
    source_enquiry_id: str | None = None
    consent_reference: str | None = None

    # What the caller has volunteered about their requirement.
    interest_status: str = "unknown"
    config_interest: str | None = None  # "2 BHK" / "3 BHK"
    purchase_timeline: str = "unknown"
    purchase_timeline_asked: bool = False
    purchase_purpose: str = "unknown"

    next_step: str = "none"
    closing_attempted: bool = False

    # CTA control (B): structured memory so the agent doesn't offer a visit /
    # callback twice, and a deterministic gate for WHEN to offer at all.
    site_visit_offered: bool = False
    site_visit_declined: bool = False
    callback_offered: bool = False
    cta_offer_count: int = 0

    lead_logged: bool = False
    # Spoken "let me note that down" ack before a write — played at most ONCE per
    # call (founder: never repeat the filler phrase in one call).
    write_ack_spoken: bool = False
    # Outcomes already written this call — prevents logging the same callback /
    # site-visit twice (the model once called log_callback and then log_lead
    # with the same outcome at close).
    logged_outcomes: set[str] = field(default_factory=set)

    @property
    def buying_signals(self) -> int:
        """Count of explicit buying signals — drives the CTA gate."""
        signals = 0
        if self.config_interest:
            signals += 1
        if self.purchase_timeline_asked or self.purchase_timeline != "unknown":
            signals += 1
        if self.purchase_purpose != "unknown":
            signals += 1
        if self.interest_status == "active":
            signals += 1
        return signals

    @property
    def cta_ready(self) -> bool:
        """Code-driven CTA gate (B2): only offer a visit/callback once enough
        buying signals exist — NOT merely because a project fact was answered.
        A caller explicitly asking to visit bypasses this (handled in persona).
        """
        if self.site_visit_declined:
            return False
        return self.buying_signals >= 2

    @property
    def conversation_stage(self) -> str:
        """Light 3-state signal exposed to the LLM — informs, does not gate."""
        if self.interest_status in _TERMINAL_INTEREST or self.closing_attempted:
            return "closing"
        if self.interest_status == "unknown":
            return "opening"
        return "engaged"

    def qualification_snapshot(self) -> dict[str, object]:
        """Classify the lead from explicit, auditable signals (internal CRM tags).

        Never spoken aloud. Terminal intent overrides any score. Hot needs
        active interest + a near-term timeline + an accepted next step.
        """
        reasons: list[str] = []

        if self.interest_status in _TERMINAL_INTEREST:
            reasons.append(_TERMINAL_INTEREST[self.interest_status])
            status = (
                "do_not_contact"
                if self.interest_status == "opted_out"
                else "wrong_or_invalid"
                if self.interest_status == "wrong_person"
                else "not_interested"
            )
            return {
                "qualification_status": status,
                "lead_temperature": "no_opportunity",
                "qualification_reasons": reasons,
            }

        if self.interest_status == "casual":
            reasons.append("Caller described the enquiry as casual browsing")
            return {
                "qualification_status": "casual_enquiry",
                "lead_temperature": "nurture",
                "qualification_reasons": reasons,
            }

        if self.interest_status == "unknown":
            reasons.append("Interest has not been confirmed yet")
            return {
                "qualification_status": "incomplete",
                "lead_temperature": "unscored",
                "qualification_reasons": reasons,
            }

        # interest_status == "active"
        reasons.append("Caller confirmed active interest")
        accepted_cta = self.next_step in {"site_visit_requested", "callback_requested"}

        if self.purchase_timeline == "within_3_months" and accepted_cta:
            reasons.extend(
                ["Purchase timeline is within 3 months", "Caller accepted a next step"]
            )
            temperature = "hot"
        elif self.purchase_timeline == "over_6_months":
            reasons.append("Purchase timeline is over 6 months")
            temperature = "nurture"
        else:
            if accepted_cta:
                reasons.append("Caller accepted a next step")
            temperature = "warm"

        return {
            "qualification_status": "qualified" if accepted_cta else "in_progress",
            "lead_temperature": temperature,
            "qualification_reasons": reasons,
        }
