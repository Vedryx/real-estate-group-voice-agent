"""Agent worker entrypoint: wires the voice pipeline and starts the
LiveKit job runner (plan.md §3.2, §12 build phase 3).

Run locally against LiveKit's dev/playground tooling with:
    uv run python -m agent.worker dev

Deploy as the telephony worker with:
    uv run python -m agent.worker start

Requires LIVEKIT_URL / LIVEKIT_API_KEY / LIVEKIT_API_SECRET and
SARVAM_API_KEY in the environment - see .env.example. Most LLM_MODEL
values (openai/..., deepseek-ai/..., google/..., etc.) run through
LiveKit Cloud's inference gateway (livekit.agents.inference.LLM),
authenticated with LIVEKIT_API_KEY/SECRET - no separate provider key
needed. Sarvam's own LLM (model names starting "sarvam-", e.g.
"sarvam-105b") is the one exception - it isn't on the inference gateway,
so it's called directly via livekit.plugins.sarvam.LLM using
SARVAM_API_KEY instead. See _build_llm() below.
"""

from __future__ import annotations

import logging
import os
import json
from collections.abc import Mapping
from typing import Any

from dotenv import load_dotenv
from livekit import rtc
from livekit.agents import (
    AgentSession,
    JobContext,
    JobProcess,
    WorkerOptions,
    cli,
    inference,
)
from livekit.agents.llm import LLM, ChatMessage, FallbackAdapter
from livekit.agents.voice.room_io import RoomInputOptions
from livekit.agents.voice.events import (
    ConversationItemAddedEvent,
    UserInputTranscribedEvent,
)
from livekit.plugins import noise_cancellation, sarvam, silero

from agent.assistant import CanopyAssistant
from agent.canopy import CanopyKnowledge
from agent.state import CallUserdata, select_language

load_dotenv()

logger = logging.getLogger("The Canopy")

# The LLM only ever reasons in text - Sarvam handles the voice ends - so
# its own Hindi/Marathi fluency matters less than tool-calling reliability
# (plan.md §3.2). See inference.LLMModels for other available models
# (OpenAI, Google, Moonshot, DeepSeek, zAI, xAI) or SarvamLLMModels for
# Sarvam's own models (e.g. "sarvam-105b" - "sarvam-m" is deprecated per
# their API as of this writing).
LLM_MODEL = os.getenv("LLM_MODEL", "openai/gpt-4.1-mini")  # openai/gpt-4.1-mini

# Optional backup model via FallbackAdapter. DeepSeek was DROPPED as the
# default fallback (Canopy migration): a real call showed deepseek-ai/
# deepseek-v3 hitting APITimeoutError / APIConnectionError and silently
# killing turns, so it is no longer trusted even as a backstop. Default is
# now EMPTY = no fallback (single reliable primary). Set FALLBACK_LLM_MODEL
# to another *reliable* model (e.g. openai/gpt-4.1) if a backstop is wanted.
FALLBACK_LLM_MODEL = os.getenv("FALLBACK_LLM_MODEL", "").strip()


def _build_llm(model: str) -> LLM:
    def _one(m: str) -> LLM:
        return (
            sarvam.LLM(model=m) if m.startswith("sarvam-") else inference.LLM(model=m)
        )

    if not FALLBACK_LLM_MODEL or model == FALLBACK_LLM_MODEL:
        return _one(model)

    # attempt_timeout=2.5s: abandon a stalling primary request quickly and
    # fail over rather than waiting out a full connection timeout.
    return FallbackAdapter([_one(model), _one(FALLBACK_LLM_MODEL)], attempt_timeout=2.5)


def prewarm(proc: JobProcess) -> None:
    # VAD is used alongside LiveKit's semantic turn detector. Loaded once
    # per worker process so a cold VAD load never blocks call setup.
    proc.userdata["vad"] = silero.VAD.load()


def _extract_caller_phone(participant: rtc.RemoteParticipant | None) -> str | None:
    if participant is None:
        return None
    # SIP participant attribute keys are set by the LiveKit SIP server, not
    # this SDK - verify "sip.phoneNumber" against current LiveKit SIP docs
    # before relying on it in production (plan.md's own "verify against
    # repo" caution applies here too, since this is server-side, not
    # SDK-side, surface).
    return participant.attributes.get("sip.phoneNumber")


def _extract_outbound_lead_context(
    metadata: str | None, attributes: Mapping[str, str] | None = None
) -> dict[str, Any]:
    """Read optional outbound campaign context without trusting arbitrary keys.

    Dispatchers may send a flat JSON object or a nested ``lead`` object in job
    metadata. Job attributes are also supported for campaign systems that use
    LiveKit attribute maps. Unknown fields are deliberately ignored.
    """
    decoded: dict[str, Any] = {}
    if metadata:
        try:
            candidate = json.loads(metadata)
            if isinstance(candidate, dict):
                nested = candidate.get("lead")
                if isinstance(nested, dict):
                    decoded.update(nested)
                decoded.update(candidate)
        except (json.JSONDecodeError, TypeError):
            logger.warning("Outbound job metadata is not valid JSON; ignoring it")

    attribute_values = dict(attributes or {})

    def _value(*keys: str) -> str | None:
        for key in keys:
            value = decoded.get(key)
            if value is None:
                value = attribute_values.get(key)
            if isinstance(value, (str, int, float)) and str(value).strip():
                return str(value).strip()
        return None

    return {
        "caller_name": _value("caller_name", "name", "lead.name"),
        "caller_phone": _value("caller_phone", "phone", "lead.phone"),
        "source_channel": _value(
            "source_channel", "source", "channel", "lead.source_channel"
        ),
        "source_campaign": _value(
            "source_campaign", "campaign", "campaign_id", "lead.campaign"
        ),
        "source_project": _value(
            "source_project", "project", "project_name", "lead.project"
        ),
        "source_enquiry_id": _value(
            "source_enquiry_id", "enquiry_id", "lead_id", "lead.enquiry_id"
        ),
        "consent_reference": _value(
            "consent_reference", "consent_id", "lead.consent_reference"
        ),
    }


def _metric_seconds(metrics: dict, key: str) -> float | None:
    """Return a compact scalar for logging without doing timing work here."""
    value = metrics.get(key)
    return round(value, 3) if isinstance(value, (int, float)) else None


async def entrypoint(ctx: JobContext) -> None:
    await ctx.connect()

    knowledge = CanopyKnowledge.load()
    lead_context = _extract_outbound_lead_context(
        ctx.job.metadata, getattr(ctx.job, "attributes", None)
    )
    userdata = CallUserdata(knowledge=knowledge, **lead_context)
    logger.info(
        "Outbound lead context loaded: source=%s campaign=%s project_supplied=%s",
        userdata.source_channel or "unknown",
        userdata.source_campaign or "unknown",
        bool(userdata.source_project),
    )

    # Pick up the caller's phone number opportunistically, without blocking
    # session start on it. A prior version called `await
    # ctx.wait_for_participant()` here - fine for real phone calls (the
    # caller's SIP participant joins essentially immediately, which is what
    # triggers the dispatch in the first place), but it hangs forever in
    # any room with no guaranteed second participant - e.g. the LiveKit
    # Agent Console/Playground, where the agent can get dispatched before a
    # human tester's browser participant joins. That silently broke testing
    # entirely (confirmed by dispatching to an empty room and watching the
    # agent sit frozen with zero published tracks).
    for existing in ctx.room.remote_participants.values():
        phone = _extract_caller_phone(existing)
        if phone:
            userdata.caller_phone = phone
            break

    def _on_participant_connected(participant: rtc.RemoteParticipant) -> None:
        if userdata.caller_phone is None:
            phone = _extract_caller_phone(participant)
            if phone:
                userdata.caller_phone = phone

    ctx.room.on("participant_connected", _on_participant_connected)

    llm = _build_llm(LLM_MODEL)
    logger.info(
        "LLM configured: primary=%s fallback=%s",
        LLM_MODEL,
        FALLBACK_LLM_MODEL,
    )

    def _on_llm_metrics(metrics) -> None:
        metadata = metrics.metadata
        logger.info(
            "LLM request completed: model=%s provider=%s duration=%.2fs ttft=%.2fs",
            metadata.model_name if metadata else "unknown",
            metadata.model_provider if metadata else "unknown",
            metrics.duration,
            metrics.ttft,
        )

    llm.on("metrics_collected", _on_llm_metrics)

    session = AgentSession[CallUserdata](
        userdata=userdata,
        vad=ctx.proc.userdata["vad"],
        stt=sarvam.STT(
            model="saaras:v3",
            language="unknown",  # auto-detect, incl. code-mixed Hinglish
        ),
        tts=sarvam.TTS(
            model="bulbul:v3",
            speaker="shubh",
            # Default until a substantive final transcript selects a language.
            target_language_code="hi-IN",
        ),
        llm=llm,
        turn_handling={
            "turn_detection": inference.TurnDetector(),
            "endpointing": {
                "mode": "dynamic",
                # min_delay raised 0.3 -> 0.6 after a live call: 0.3s was too
                # eager for natural Hindi/Marathi conversational pauses, letting
                # the agent take the turn before the caller had finished (it also
                # fed a mid-thought "Nahi, site visit" into an over-eager CTA).
                # 0.6s gives a bit more grace for a between-clause pause while
                # still feeling responsive.
                "min_delay": 0.6,
                "max_delay": 2.0,
            },
            # Keep preemptive LLM generation, but wait for the turn to be
            # confirmed before synthesizing speech. This avoids generating
            # audible fragments when a user pauses mid-sentence.
            "preemptive_generation": {"preemptive_tts": False},
            # min_duration raised from the 0.5s default: a 2026-08-03 export
            # showed the agent's own speech getting cut off mid-sentence 3
            # times in one call by brief filler/hesitation ("आप।", "म्हणजे")
            # rather than a real intent to interrupt - each one left a
            # question trailing off unfinished. 0.9s gives a bit more grace
            # before an interruption is confirmed, while still being fast
            # enough not to feel unresponsive to a genuine barge-in.
            # min_words=1 additionally requires at least one real
            # transcribed word (not just detected speech/noise) before an
            # interruption fires - closes the same class of issue that
            # caused the opening greeting to get cancelled by SIP call-setup
            # noise before allow_interruptions=False was added for it.
            "interruption": {
                "min_duration": 0.9,
                "min_words": 1,
                "resume_false_interruption": True,
            },
        },
    )

    def _on_user_input_transcribed(event: UserInputTranscribedEvent) -> None:
        if not event.is_final:
            return
        selected = select_language(
            userdata.preferred_language,
            event.transcript,
            str(event.language) if event.language is not None else None,
        )
        if selected is None or selected == userdata.preferred_language:
            return
        userdata.preferred_language = selected
        tts = session.tts
        if isinstance(tts, sarvam.TTS):
            tts.update_options(target_language_code=selected)

    session.on("user_input_transcribed", _on_user_input_transcribed)

    def _on_conversation_item_added(event: ConversationItemAddedEvent) -> None:
        # LiveKit has already calculated these values. Keep this callback
        # synchronous and tiny: no awaits, network I/O, transcript logging,
        # or manual timestamp correlation on the conversation path.
        if not logger.isEnabledFor(logging.INFO) or not isinstance(
            event.item, ChatMessage
        ):
            return

        item = event.item
        metrics = item.metrics
        if item.role == "user":
            logger.info(
                "Turn metrics: role=user item_id=%s transcription_delay_s=%s "
                "end_of_turn_delay_s=%s on_user_turn_completed_delay_s=%s",
                item.id,
                _metric_seconds(metrics, "transcription_delay"),
                _metric_seconds(metrics, "end_of_turn_delay"),
                _metric_seconds(metrics, "on_user_turn_completed_delay"),
            )
        elif item.role == "assistant":
            llm_metadata = metrics.get("llm_metadata") or {}
            logger.info(
                "Turn metrics: role=assistant item_id=%s llm_ttft_s=%s "
                "tts_ttfb_s=%s playback_latency_s=%s e2e_latency_s=%s "
                "interrupted=%s model=%s provider=%s",
                item.id,
                _metric_seconds(metrics, "llm_node_ttft"),
                _metric_seconds(metrics, "tts_node_ttfb"),
                _metric_seconds(metrics, "playback_latency"),
                _metric_seconds(metrics, "e2e_latency"),
                item.interrupted,
                llm_metadata.get("model_name", "unknown"),
                llm_metadata.get("model_provider", "unknown"),
            )

    session.on("conversation_item_added", _on_conversation_item_added)

    await session.start(
        agent=CanopyAssistant(),
        room=ctx.room,
        room_input_options=RoomInputOptions(
            # Telephony-tuned Krisp noise cancellation - real phone calls
            # from busy streets/offices are noisy (plan.md §3.2).
            noise_cancellation=noise_cancellation.BVCTelephony(),
        ),
    )


if __name__ == "__main__":
    cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint,
            prewarm_fnc=prewarm,
            # Explicit dispatch for telephony - MUST match the agentName in
            # telephony/dispatch-rule.json and .env exactly, or calls stop
            # routing silently. The fallback here is the canonical Canopy
            # agent name so a missing .env doesn't point dispatch at a wrong
            # string (the old "The Real Estate Mall" fallback was a latent bug).
            agent_name=os.getenv("AGENT_NAME", "the-canopy-agent"),
        )
    )
