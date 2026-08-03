# The Real Estate Group Voice Agent

A LiveKit Agents (Python) voice assistant for The Real Estate Group, a Pune-based
real-estate channel-partner/consultancy. Answers inbound phone enquiries in
English/Hindi/Hinglish, qualifies callers against real project data, and
logs leads locally. Built from `plan.md` (the original build spec - see
that document for the full product spec, sample conversations, and open
business questions).

## Status

Scaffolded end-to-end (data layer, agent, tools, voice pipeline wiring,
telephony config, test suite) against real `livekit-agents` 1.6.7 /
`livekit-plugins-sarvam` APIs, and against the actual `livekit.api`
protobuf field definitions for the SIP/telephony config - verified by
introspecting the installed packages, not from memorized snippets or
doc-page summaries alone (a few doc pages disagreed with each other on
field casing; the protobuf definitions were the tiebreaker). LiveKit +
Sarvam credentials are live and the LLM has been run for real (see
"Running it" and the test results below). Telephony uses **Telnyx**, not
the Twilio the original plan.md named - see telephony/README.md. See
"What's not yet verified" below for what's still untested.

## Project layout

```
data/                   Seed data (projects, service-interest areas, workplace proximity)
                         + leads.jsonl (gitignored, created at runtime)
agent/
  data_store.py          Read-only data access + append-only lead logging
  state.py                Per-call session state (CallUserdata)
  persona.py               System prompt (plan.md §7)
  assistant.py               Primary RealEstateGroupAssistant agent
  escalation_agent.py          Human hand-off agent (plan.md §3.3, §9.7)
  worker.py                      Entrypoint: wires STT/TTS/LLM, runs the job worker
tools/
  catalog.py              12 @function_tool definitions (plan.md §6 + set_conversation_language)
telephony/
  README.md                Telnyx + LiveKit SIP setup checklist (manual steps)
  inbound-trunk.json         lk CLI config (fill in and run)
  dispatch-rule.json           lk CLI config (fill in and run)
  outbound-trunk.json.example    deferred - not v1
  setup.sh                         wraps the lk CLI calls
scripts/
  run-openai-agent.sh      Named agent worker: openai/gpt-4.1-mini via LiveKit inference gateway
  run-deepseek-agent.sh    Named agent worker: deepseek-ai/deepseek-v3 via LiveKit inference gateway
  run-sarvam-agent.sh      Named agent worker: sarvam-105b, called directly (not on the gateway)
tests/                   pytest suite (plan.md §11) - 54 tests total
```

## Setup

Requires Python 3.13 and [`uv`](https://docs.astral.sh/uv/).

```bash
uv sync
cp .env.example .env   # then fill in real credentials, see below
```

### Credentials needed

| Variable | Where to get it |
|---|---|
| `LIVEKIT_URL` / `LIVEKIT_API_KEY` / `LIVEKIT_API_SECRET` | LiveKit Cloud project, or your self-hosted server. **Also authenticates the LLM** - see below. |
| `SARVAM_API_KEY` | https://dashboard.sarvam.ai |
| `TELNYX_API_KEY` / `TELNYX_PHONE_NUMBER` | Telnyx portal, only needed for telephony (not for local dev/testing) - see telephony/README.md |

**No separate LLM key required.** `agent/worker.py` uses LiveKit Cloud's
inference gateway (`livekit.agents.inference.LLM`), which authenticates
with the `LIVEKIT_API_KEY`/`LIVEKIT_API_SECRET` above and is billed to the
LiveKit project - not a separate Anthropic/OpenAI account. Default model is
`deepseek-ai/deepseek-v3`; override with `LLM_MODEL` in `.env`. See
`inference.LLMModels` for other options (OpenAI/Google/Moonshot/DeepSeek/
zAI/xAI - no Anthropic models are exposed through this gateway as of
writing).

## Running it

**Local dev, no telephony, text or audio via terminal:**
```bash
uv run python -m agent.worker console
```

**Local dev connected to a real LiveKit room (playground):**
```bash
uv run python -m agent.worker dev
```
Registers under `AGENT_NAME` from `.env` (`The Real Estate Group` - see
scripts/ below for two additional named variants).

**Production worker (after telephony/README.md is complete):**
```bash
uv run python -m agent.worker start
```

### Comparing LLMs side-by-side (OpenAI / DeepSeek / Sarvam)

Three extra worker scripts each run under their own `agent_name`,
independent of the `.env`-configured default and the live Telnyx
telephony dispatch (unaffected either way):

```bash
./scripts/run-openai-agent.sh      # agent_name=real-estate-group-openai,   LLM=openai/gpt-4.1-mini (LiveKit inference gateway)
./scripts/run-deepseek-agent.sh    # agent_name=real-estate-group-deepseek, LLM=deepseek-ai/deepseek-v3 (LiveKit inference gateway)
./scripts/run-sarvam-agent.sh      # agent_name=real-estate-group-sarvam,   LLM=sarvam-105b (called directly via SARVAM_API_KEY, not on the gateway)
```

`agent/worker.py`'s `_build_llm()` picks the right client automatically
based on the model name - anything starting `sarvam-` goes straight to
`livekit.plugins.sarvam.LLM`, everything else goes through LiveKit's
inference gateway. Sarvam's own model list is `sarvam-105b` /
`sarvam-105b-32k` / `sarvam-30b` / `sarvam-30b-16k` - `sarvam-m` is
deprecated per their API as of this writing, don't use it.

Run one or both (separate terminals), then talk to a specific one via the
LiveKit Agent Console/Playground - see below.

### Testing via LiveKit's Agent Console (no phone/Telnyx needed)

LiveKit Cloud has a browser-based console for talking to an agent
directly with your mic, without any telephony setup:

1. Log into [cloud.livekit.io](https://cloud.livekit.io) with the account
   that owns this project, open the project, go to **Agents**, and click
   **Launch Console** (or go straight to
   `cloud.livekit.io/projects/p_/agents`). This is a step only you can
   do - it needs your account login, not just the API key/secret in
   `.env`.
2. With one of the workers above running (`dev` mode), the console
   should let you pick it by its `agent_name` for explicit dispatch (this
   project uses explicit dispatch throughout, not automatic - see
   plan.md §3.1 step 6) and start a session with your microphone.
3. To compare models, run both scripts in separate terminals and switch
   between `real-estate-group-openai` and `real-estate-group-deepseek` in
   the console between conversations.

If your project's console doesn't expose an agent-name picker, the
classic standalone playground at
[agents-playground.livekit.io](https://agents-playground.livekit.io) also
works - connect it to this project manually with `LIVEKIT_URL` and your
API key/secret from `.env` (paste these into the playground's own connect
form yourself; treat them the same as any other credential).

## Tests

```bash
uv run pytest
```

Two tiers:
- **Pure unit tests** (`test_tools_unit.py`, 12 tests) - deterministic tool
  logic (search filtering, price hedging, metro confirmation, workplace
  matching, lead logging). Run with zero credentials, always.
- **LLM behavior tests** (everything else, 42 tests) - use LiveKit's
  `AgentSession.run(user_input=...)` + `result.expect...` harness against a
  **real** model (via LiveKit's inference gateway) to verify the agent
  makes the right tool-calling and refusal decisions for every edge case
  in plan.md §9. These automatically skip with a clear reason if
  `LIVEKIT_API_KEY`/`LIVEKIT_API_SECRET` aren't set.

**Last real run: 41/54 passed.** All 12 unit tests passed, plus most
behavior tests (payment/PII refusal, RERA non-fabrication, metro
hallucination checks across all 8 non-Sylvan projects, escalation on
anger, off-topic redirects). 13 failed - some are genuine gaps worth
looking at (e.g. a direct Raheja Vistas pricing question got a text answer
with no `get_pricing` tool call; a litigation question didn't trigger
`escalate_to_human`), others are test assertions that were stricter than
the agent's actually-fine behavior (e.g. asking a clarifying question
before searching, or giving an honest "I don't have that, let me connect
you" answer worded differently than the test expected). Re-run with
`uv run pytest -q` and look at the failures before treating this as done.

## What's not yet verified (do this next)

1. **13 test failures above need triage** - some are real agent gaps
   (see above), some are test calibration. Fix or loosen as appropriate.
2. **No live LiveKit room tested.** `agent/worker.py console` mode hasn't
   been run - do this before touching telephony.
3. **SIP trunk is provisioned but no real call has been made yet.** Telnyx
   FQDN connection + LiveKit inbound trunk/dispatch rule are live (see
   telephony/README.md "Status") - `+17083958770` should route to this
   agent once the worker is running (`uv run python -m agent.worker
   start`), but nobody has actually dialed it to confirm. Do that next.
4. **Sarvam STT/TTS Indian-numbering behavior unverified** - plan.md §5.3
   flags that TTS reading of "72 lakh" vs "seventy-two hundred thousand" is
   a common failure point and needs explicit listening-test verification.
5. **Barge-in with Sarvam's STT-based turn detection unverified** - plan.md
   §9.6 flags this needs explicit testing, not assumption.
6. **Business open questions** (plan.md §13) are unresolved - most
   importantly: commercial inventory existence, the Raheja Vistas price
   conflict, The Real Estate Group's actual recording/consent policy, and native-speaker
   review of the bilingual script drafts in `agent/persona.py`. Don't go
   live before these are answered.

## Explicitly out of scope for v1 (plan.md §12)

CRM/webhook integration, payment processing, WhatsApp/SMS/email brochure
sending, multi-city expansion beyond Pune, live/real-time unit-availability
sync, outbound calling (compliance sign-off required first, plan.md §9.4).
