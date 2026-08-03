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

from dotenv import load_dotenv
from livekit import rtc
from livekit.agents import AgentSession, JobContext, JobProcess, WorkerOptions, cli, inference
from livekit.agents.llm import LLM, FallbackAdapter
from livekit.agents.voice.room_io import RoomInputOptions
from livekit.plugins import noise_cancellation, sarvam, silero

from agent.assistant import RealEstateGroupAssistant
from agent.data_store import DataStore
from agent.state import CallUserdata

load_dotenv()

logger = logging.getLogger("The Real Estate Group")

# The LLM only ever reasons in text - Sarvam handles the voice ends - so
# its own Hindi/Marathi fluency matters less than tool-calling reliability
# (plan.md §3.2). See inference.LLMModels for other available models
# (OpenAI, Google, Moonshot, DeepSeek, zAI, xAI) or SarvamLLMModels for
# Sarvam's own models (e.g. "sarvam-105b" - "sarvam-m" is deprecated per
# their API as of this writing).
LLM_MODEL = os.getenv("LLM_MODEL", "deepseek-ai/deepseek-v3")

# Backup model used via FallbackAdapter if LLM_MODEL's provider connection
# fails or hangs - added 2026-08-03 after a live test hit a hard
# httpcore.ReadTimeout / APIConnectionError streaming from DeepSeek via
# LiveKit's inference gateway ~10s into a response, which silently killed
# the turn with no retry (the agent just went quiet). gpt-4.1-mini tested
# reliably with zero connection failures across the full behavior suite,
# unlike deepseek-ai/deepseek-v3 which has shown repeated timeouts.
FALLBACK_LLM_MODEL = os.getenv("FALLBACK_LLM_MODEL", "openai/gpt-4.1-mini")


def _build_llm(model: str) -> LLM:
    def _one(m: str) -> LLM:
        return sarvam.LLM(model=m) if m.startswith("sarvam-") else inference.LLM(model=m)

    if model == FALLBACK_LLM_MODEL:
        return _one(model)

    # attempt_timeout=2.5 (lowered from 5.0 on 2026-08-03): a real call's
    # traces showed deepseek-ai/deepseek-v3 failing 3 times in ~10 turns -
    # two hit APITimeoutError at ~5.1-5.25s (bumping right against the old
    # 5.0s ceiling), one took 7.46s to surface APIConnectionError. Every
    # single fallback attempt to gpt-4.1-mini then succeeded in 1.65-3.42s.
    # Cutting the primary's budget to 2.5s means a stalling request gets
    # abandoned faster - worst case per turn drops from ~5-7.5s to roughly
    # 2.5s + the fallback's own ttft (~1.2-2.5s observed), instead of
    # waiting out the full failure first. Tradeoff: a handful of DeepSeek
    # responses that are merely slow to start (one observed at 3.98s ttft,
    # not a failure) will now get preempted into an unnecessary fallback
    # too - acceptable given every observed fallback was itself fast.
    return FallbackAdapter([_one(model), _one(FALLBACK_LLM_MODEL)], attempt_timeout=2.5)


def prewarm(proc: JobProcess) -> None:
    # Fallback VAD only - primary turn detection is Sarvam's own STT-based
    # end-of-speech signal (turn_detection="stt" below). Loaded once per
    # worker process so a cold VAD load never blocks call setup.
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


async def entrypoint(ctx: JobContext) -> None:
    await ctx.connect()

    data_store = DataStore.load()
    userdata = CallUserdata(data_store=data_store, caller_phone=None)

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
            # Default before the caller has picked a language (persona.py's
            # greeting asks Hindi/Marathi/English) - set_conversation_language
            # switches this via tts.update_options() once they choose.
            target_language_code="hi-IN",
        ),
        llm=_build_llm(LLM_MODEL),
        turn_handling={
            "turn_detection": "stt",  # Sarvam emits its own start/end-of-speech; do not also pass VAD here
            "endpointing": {"min_delay": 0.07},
            # Start TTS synthesis before the turn is fully confirmed, not just
            # LLM generation (LiveKit's default) - shaves the TTS
            # time-to-first-byte off the perceived response delay, which the
            # 2026-08-03 observability export showed averaging ~0.74s (and
            # spiking to 10s once) on top of ~0.9s STT + ~1.2s LLM TTFT.
            "preemptive_generation": {"preemptive_tts": True},
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
            "interruption": {"min_duration": 0.9, "min_words": 1},
        },
    )

    await session.start(
        agent=RealEstateGroupAssistant(),
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
            # Explicit dispatch for telephony (plan.md §3.1 step 6) - must
            # match the agentName in telephony/dispatch-rule.json so calls
            # don't auto-answer via automatic dispatch.
            agent_name=os.getenv("AGENT_NAME", "The Real Estate Group"),
        )
    )
