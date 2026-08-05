"""Per-call session state (AgentSession userdata).

Tracks what's already been collected from the caller so the agent
doesn't re-ask (plan.md §8), and carries the read-only DataStore.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from agent.data_store import DataStore


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
INVENTORY_FITS = frozenset({"unknown", "exact_match", "no_exact_match"})
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
    data_store: DataStore

    caller_phone: str | None = None
    caller_name: str | None = None
    preferred_language: str | None = None

    # Outbound lead context supplied by the campaign/dispatch system. These
    # fields let the opener mention the real source without inventing one.
    source_channel: str | None = None
    source_campaign: str | None = None
    source_project: str | None = None
    source_enquiry_id: str | None = None
    consent_reference: str | None = None

    requested_city: str | None = None
    requested_locality: str | None = None
    bhk_preference: str | None = None
    budget_min_lakh: float | None = None
    budget_max_lakh: float | None = None
    property_type_requested: str = "unspecified"
    workplace_area: str | None = None
    developer_preference: str | None = None

    interest_status: str = "unknown"
    purchase_timeline: str = "unknown"
    purchase_timeline_asked: bool = False
    purchase_purpose: str = "unknown"
    inventory_fit: str = "unknown"
    matching_project_ids: list[str] = field(default_factory=list)
    closing_attempted: bool = False
    next_step: str = "none"

    projects_discussed: list[str] = field(default_factory=list)
    lead_logged: bool = False

    def note_project_discussed(self, project_id: str) -> None:
        if project_id not in self.projects_discussed:
            self.projects_discussed.append(project_id)

    @property
    def requirements_complete(self) -> bool:
        """Minimum facts needed to call an outbound lead qualified."""
        return bool(
            self.requested_city
            and self.bhk_preference
            and self.purchase_timeline_asked
            and (
                self.budget_min_lakh is not None
                or self.budget_max_lakh is not None
            )
        )

    @property
    def conversation_stage(self) -> str:
        """Deterministic stage rail exposed to the LLM on every turn."""
        if self.interest_status in _TERMINAL_INTEREST:
            return "close"
        if self.interest_status == "unknown":
            return "confirm_interest"
        if not self.requirements_complete:
            return "collect_requirements"
        if self.inventory_fit == "unknown":
            return "match_projects"
        if self.inventory_fit == "exact_match" and not self.closing_attempted:
            return "present_matches_and_offer_next_step"
        if self.inventory_fit == "no_exact_match" and not self.closing_attempted:
            return "explain_gap_and_offer_callback"
        return "close"

    def qualification_snapshot(self) -> dict[str, object]:
        """Classify the lead from explicit, auditable signals.

        Terminal intent always overrides any score. A lead becomes qualified
        only after we have minimum requirements and an exact inventory match.
        Hot additionally requires a near-term timeline and an accepted CTA.
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
                "requirements_complete": self.requirements_complete,
            }

        if self.interest_status == "casual":
            reasons.append("Caller described the enquiry as casual browsing")
            return {
                "qualification_status": "casual_enquiry",
                "lead_temperature": "nurture",
                "qualification_reasons": reasons,
                "requirements_complete": self.requirements_complete,
            }

        if self.interest_status == "unknown":
            reasons.append("Interest has not been confirmed yet")
            return {
                "qualification_status": "incomplete",
                "lead_temperature": "unscored",
                "qualification_reasons": reasons,
                "requirements_complete": self.requirements_complete,
            }

        if not self.requirements_complete:
            reasons.append(
                "City, BHK, budget, and an answered timeline question are not all captured yet"
            )
            return {
                "qualification_status": "incomplete",
                "lead_temperature": "nurture",
                "qualification_reasons": reasons,
                "requirements_complete": False,
            }

        if self.inventory_fit != "exact_match":
            reasons.append("No exact inventory match has been found")
            return {
                "qualification_status": "unqualified",
                "lead_temperature": "nurture",
                "qualification_reasons": reasons,
                "requirements_complete": True,
            }

        reasons.extend(
            ["Caller confirmed active interest", "Requirements are complete", "Exact inventory match found"]
        )
        accepted_cta = self.next_step in {
            "site_visit_requested",
            "callback_requested",
        }
        if self.purchase_timeline == "within_3_months" and accepted_cta:
            reasons.extend(["Purchase timeline is within 3 months", "Caller accepted a next step"])
            temperature = "hot"
        elif self.purchase_timeline == "over_6_months":
            reasons.append("Purchase timeline is over 6 months")
            temperature = "nurture"
        else:
            if accepted_cta:
                reasons.append("Caller accepted a next step")
            temperature = "warm"

        return {
            "qualification_status": "qualified",
            "lead_temperature": temperature,
            "qualification_reasons": reasons,
            "requirements_complete": True,
        }
