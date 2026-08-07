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
import time
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
from livekit.agents.llm import LLM, ChatMessage, FallbackAdapter, LLMError
from livekit.agents.voice.room_io import RoomInputOptions
from livekit.agents.voice.events import (
    AgentStateChangedEvent,
    ConversationItemAddedEvent,
    ErrorEvent,
    UserInputTranscribedEvent,
    UserStateChangedEvent,
)
from livekit.plugins import google, noise_cancellation, sarvam, silero

from agent.assistant import CanopyAssistant
from agent.canopy import CanopyKnowledge
from agent.persona import instructions_for_call
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

# Voice pipeline mode (branch experiment):
#   "cascade" (default) = Sarvam STT + text LLM (LLM_MODEL) + Sarvam TTS.
#   "s2s"               = a single Gemini Live realtime model does audio-in/
#                         audio-out, replacing all three. Needs GOOGLE_API_KEY.
# Tools, persona and state are identical across both modes.
VOICE_MODE = os.getenv("VOICE_MODE", "cascade").strip().lower()
# Empty -> plugin default (gemini-2.5-flash-native-audio-preview-12-2025).
S2S_MODEL = os.getenv("S2S_MODEL", "").strip()
# Gemini Live prebuilt voice name (Puck/Charon/Kore/Fenrir/Aoede/...).
S2S_VOICE = os.getenv("S2S_VOICE", "Puck").strip() or "Puck"


def _build_realtime_llm() -> LLM:
    """Build the Gemini Live speech-to-speech model (VOICE_MODE=s2s).

    One realtime model replaces the STT+LLM+TTS trio. api_key is read from
    GOOGLE_API_KEY (via the env) if not passed. input/output transcription is
    enabled so the conversation still shows up as text in the turn-metrics logs.
    """
    from google.genai import types as genai_types
    from livekit.plugins.google.realtime import RealtimeModel

    kwargs: dict[str, Any] = {
        "voice": S2S_VOICE,
        "temperature": 0.8,
        # Keep text transcripts flowing for observability + our turn logging.
        "input_audio_transcription": genai_types.AudioTranscriptionConfig(),
        "output_audio_transcription": genai_types.AudioTranscriptionConfig(),
    }
    if S2S_MODEL:
        kwargs["model"] = S2S_MODEL
    api_key = os.getenv("GOOGLE_API_KEY")
    if api_key:
        kwargs["api_key"] = api_key
    return RealtimeModel(**kwargs)


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
    phone = participant.attributes.get("sip.phoneNumber")
    # Guard: in console/dev mode participant.attributes is a mock and .get()
    # returns a MagicMock, which then leaked into the lead record as an ugly
    # "<MagicMock ...>" string. Only accept a real non-empty string.
    return phone if isinstance(phone, str) and phone.strip() else None


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


def _guard_commercial_data(knowledge: CanopyKnowledge) -> None:
    """E3: never let DUMMY commercial data reach a real caller.

    Hard-block startup in production; otherwise log a loud demo-mode banner.
    """
    if not knowledge.commercial_is_dummy():
        return
    env = os.getenv("ENVIRONMENT", os.getenv("APP_ENV", "development")).strip().lower()
    if env in ("production", "prod"):
        raise RuntimeError(
            "Production startup blocked: dummy Canopy commercial data is active. "
            "Replace data/canopy_commercial.json with the real cost sheet before going live."
        )
    logger.warning("DEMO MODE — DUMMY COMMERCIAL DATA (price/possession are placeholders)")


async def entrypoint(ctx: JobContext) -> None:
    await ctx.connect()

    knowledge = CanopyKnowledge.load()
    _guard_commercial_data(knowledge)
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

    def _on_llm_metrics(metrics) -> None:
        metadata = getattr(metrics, "metadata", None)
        logger.info(
            "LLM request completed: model=%s provider=%s duration=%.2fs ttft=%.2fs",
            getattr(metadata, "model_name", "unknown") if metadata else "unknown",
            getattr(metadata, "model_provider", "unknown") if metadata else "unknown",
            getattr(metrics, "duration", 0.0) or 0.0,
            getattr(metrics, "ttft", 0.0) or 0.0,
        )

    if VOICE_MODE == "s2s":
        # Speech-to-speech (Gemini Live): one realtime model does audio-in /
        # audio-out, replacing the Sarvam-STT + text-LLM + Sarvam-TTS trio. No
        # separate vad/stt/tts and no turn-detector config - the model runs its
        # own server-side turn detection. Tools, persona and state are unchanged.
        realtime_llm = _build_realtime_llm()
        logger.info(
            "VOICE MODE: s2s (Gemini Live) model=%s voice=%s",
            S2S_MODEL or "<plugin default>",
            S2S_VOICE,
        )
        try:
            realtime_llm.on("metrics_collected", _on_llm_metrics)
        except Exception:  # noqa: BLE001 - realtime metrics shape may differ / be absent
            pass
        session = AgentSession[CallUserdata](
            userdata=userdata,
            llm=realtime_llm,
        )
    else:
        llm = _build_llm(LLM_MODEL)
        logger.info(
            "VOICE MODE: cascade | LLM configured: primary=%s fallback=%s",
            LLM_MODEL,
            FALLBACK_LLM_MODEL,
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
                # The v1 audio turn detector predicts end-of-turn from audio
                # (semantic + acoustic), NOT from the transcript. A live call showed
                # it committing on a short affirmation ("Ha, yes yes.", EOU prob 0.767
                # > the default hi threshold ~0.575) while the caller was still mid-
                # thought. The doc-sanctioned lever for that is unlikely_threshold
                # (higher = more patient / needs more confidence to end the turn), NOT
                # cranking min_delay (which fights the detector's design). Raise hi and
                # en so the model waits for stronger EOU evidence in Indian-accented /
                # code-mixed speech. Note: Marathi is NOT among the detector's 14
                # supported languages, so mr-IN calls fall back to the English
                # threshold -> "en" covers them too.
                "turn_detection": inference.TurnDetector(
                    unlikely_threshold={"hi": 0.70, "en": 0.65},
                ),
                # Endpointing back at the documented audio-detector defaults
                # (min 0.3 / max 2.5). With the audio model giving a confident signal,
                # these delays are meant to be short; patience comes from the
                # threshold above. max 2.5 gives a genuinely hesitant/long utterance
                # room before the turn is force-committed.
                "endpointing": {
                    "mode": "dynamic",
                    "min_delay": 0.3,
                    "max_delay": 2.5,
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

    def _on_error(ev: ErrorEvent) -> None:
        # C3: no dead air on a genuine (non-recoverable) LLM failure. The
        # framework retries recoverable errors and fails over if a fallback
        # model is configured; this canned line covers the case where the turn
        # would otherwise end in silence. (STT/TTS failures can't be masked by
        # speaking, so we only recover LLM failures.)
        err = ev.error
        if isinstance(err, LLMError) and not err.recoverable:
            logger.warning("Unrecoverable LLM error; playing recovery line: %s", err.label)
            # say() needs a TTS; in S2S (RealtimeModel, no TTS) it would raise, so
            # skip the canned line there — the realtime model recovers on its own.
            if getattr(session, "tts", None) is not None:
                session.say("Sorry, ek second—main detail dobara check kar raha hoon.")

    session.on("error", _on_error)

    # Perceived-latency tracker — the human-felt gap from the caller going quiet
    # to the agent starting to speak. Works in BOTH modes and is the ONLY latency
    # signal in s2s (the realtime path emits no ttft/tts_ttfb/e2e cascade metrics).
    # Measured the same way in both so cascade (~2.9s) and s2s are comparable.
    _perceived = {"user_quiet_at": None}

    def _on_user_state(ev: UserStateChangedEvent) -> None:
        # User just stopped speaking -> start the clock (last pause wins).
        if ev.new_state == "listening":
            _perceived["user_quiet_at"] = time.monotonic()

    def _on_agent_state(ev: AgentStateChangedEvent) -> None:
        # Agent audio begins -> log the gap since the caller went quiet.
        started = _perceived["user_quiet_at"]
        if ev.new_state == "speaking" and started is not None:
            logger.info(
                "Perceived latency (user-quiet -> agent-speaking): %.2fs [mode=%s]",
                time.monotonic() - started,
                VOICE_MODE,
            )
            _perceived["user_quiet_at"] = None

    session.on("user_state_changed", _on_user_state)
    session.on("agent_state_changed", _on_agent_state)

    # In s2s, hand the full persona+brief instructions in at construction so the
    # agent never calls update_instructions() before the realtime session is
    # active (which would reconnect and time out the opener). Cascade builds them
    # per turn in on_enter/on_user_turn_completed.
    s2s_instructions = instructions_for_call(userdata) if VOICE_MODE == "s2s" else None

    await session.start(
        agent=CanopyAssistant(s2s_instructions),
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
