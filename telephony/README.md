# Telephony setup (Telnyx + LiveKit SIP)

Deviates from plan.md §3.1, which named Twilio - this project's actual
phone number is with Telnyx instead. LiveKit's SIP integration is
provider-agnostic (it's standard SIP), and LiveKit publishes a dedicated
Telnyx integration guide, so this is a straightforward swap, not a
workaround.

## Status: provisioned and confirmed working (2026-07-31)

Inbound is live end-to-end - **do not re-run the steps below**, they'd
create duplicate resources. What exists, IDs recorded in `.env`
(gitignored) since they're the only record:

- Telnyx outbound voice profile, FQDN connection ("The Real Estate Group
  LiveKit Trunk" - renamed from "VJ Real Estate LiveKit Trunk", cosmetic
  label only), and FQDN record → `3odkquyeoap.sip.livekit.cloud:5060`
- `+17083958770` attached to that connection. **This number was
  previously attached to a different Telnyx connection
  ("dograh-kp-local-outbound")** - reassigned with explicit confirmation.
  If that Dograh telephony setup is still needed, it now has no number and
  will need a new one.
- LiveKit inbound trunk `ST_QyCkvn2Frhk6` + dispatch rule
  `SDR_VPUnCWswYR3j` (individual rooms per caller, prefix `call-`,
  explicit dispatch to agent `the-real-estate-group-agent` - originally
  left as `vj-realestate-agent` since it's an internal routing string
  never spoken/shown to anyone, but later renamed to match `AGENT_NAME`
  in `.env` after that was changed - **both the dispatch rule and the
  worker's `AGENT_NAME` must always match exactly**, or calls stop
  routing silently - if you change one, change the other in the same
  breath)

To inspect or change any of this later, use `lk.sip.list_inbound_trunk` /
`list_dispatch_rule` (Python SDK, see §3 below for the pattern) or the
LiveKit Cloud dashboard - don't create new ones without deleting the old
first, or calls may route unpredictably across duplicates.

**Root cause of the initial failures, found and fixed:** first call
attempt failed instantly (SIP 487, hangup cause 96
`MANDATORY_IE_MISSING`, `send_cancel` per Telnyx's CDR -
`/v2/detail_records`, `filter[record_type]=sip-trunking`). Auth
credentials matched exactly on both sides, and removing the
`allowedAddresses` IP allowlist alone did **not** fix it (retested,
identical failure). What actually fixed it: **removing
`authUsername`/`authPassword` from the LiveKit inbound trunk entirely**.
Telnyx's own troubleshooting docs confirm this exact signature happens
when the far end (LiveKit) responds with a 401/407 digest-auth
challenge that a Telnyx FQDN connection's "Outbound Calls
Authentication: Credentials" setting doesn't handle cleanly on this leg.
First real call after removing auth connected, ran 128 seconds, MOS 4.49,
clean hangup - confirmed working end-to-end.

**Current live security posture:** no SIP auth (digest auth doesn't work
reliably with this Telnyx connection type) - `allowedAddresses` was
restored to the US-region signaling IPs (`192.76.120.10`,
`64.16.250.10`) as the sole access control, since evidence suggests those
IPs were never actually the blocker (only the auth removal changed
anything). **This IP-allowlist-plus-no-auth combination has not yet been
re-tested with a real call** - do that before treating this as fully
confirmed secure. If it turns out the IP list is wrong too, the trunk is
currently reachable by anyone who knows the SIP URI and the phone number
- narrow scope, but worth closing the loop.

---

The rest of this document is the setup process that was followed, kept
for reference (e.g. if the trunk/connection ever need recreating).

**Sources, verify against these before executing** (SIP provider UIs
change, and field names below were cross-checked against the actual
`livekit.api` protobuf definitions to catch doc-page inconsistencies -
still confirm nothing has moved since):
- https://docs.livekit.io/telephony/start/providers/telnyx/
- https://developers.telnyx.com/docs/voice/sip-trunking/livekit-configuration-guide
- https://docs.livekit.io/telephony/accepting-calls/inbound-trunk/
- https://docs.livekit.io/telephony/accepting-calls/dispatch-rule/

## 1. Telnyx portal setup

Real-time Communications → Voice → SIP Trunking, in the Telnyx portal.

1. **Outbound voice profile** - create one (controls routing/spend limits;
   required even for an inbound-heavy trunk since Telnyx's FQDN connection
   needs one attached).
2. **FQDN connection** (not IP connection - FQDN enables credential auth,
   which is what secures the LiveKit side too):
   - Connection type: **FQDN**
   - Transport protocol: **TCP** (Telnyx's recommendation)
   - FQDN: your LiveKit SIP URI (`<your-project>.sip.livekit.cloud` for
     LiveKit Cloud), port 5060
   - Outbound Calls Authentication: **Credentials** - set a username/password.
     These are the calls Telnyx sends *to* LiveKit, i.e. our **inbound**
     direction - the credentials go into `inbound-trunk.json`'s
     `authUsername`/`authPassword` below, not just the outbound config.
   - Inbound number format: `+E.164`
   - Attach the outbound voice profile from step 1
3. **Associate your Telnyx phone number** with this connection (My Numbers).
4. (Optional) Enable G.722 for HD voice in the connection's Inbound settings.

## 2. Fill in the LiveKit-side configs

`inbound-trunk.json` (already scaffolded in this directory):
- `numbers`: your Telnyx number, E.164 (e.g. `"+9181XXXXXXXX"`)
- `authUsername` / `authPassword`: the same credentials set in the Telnyx
  FQDN connection's Outbound Calls Authentication (step 1.2 above)
- `allowedAddresses`: **left out deliberately** - an earlier attempt at
  pinning this to Telnyx's documented "US region" signaling IPs
  (`192.76.120.10`, `64.16.250.10`) caused real calls to be rejected
  outright (see "Status" above), likely because `anchorsite_override:
  "Latency"` on the FQDN connection routes from whichever POP is closest,
  not a fixed pair of IPs. Credential auth alone is the security control
  here. If you want IP allowlisting back, verify the actual source IP from
  a real call's SIP trace first - don't trust the static regional list
  blindly.

## 3. Create the inbound trunk + dispatch rule

Either via the `lk` CLI (`./setup.sh`, needs the CLI installed) or directly
via the Python SDK already in this project's venv - both call the same
LiveKit API:

```bash
uv run python -c "
import asyncio, json
from livekit import api

async def main():
    lk = api.LiveKitAPI()  # reads LIVEKIT_URL/API_KEY/API_SECRET from env
    cfg = json.load(open('telephony/inbound-trunk.json'))['trunk']
    trunk = await lk.sip.create_inbound_trunk(
        api.CreateSIPInboundTrunkRequest(trunk=api.SIPInboundTrunkInfo(**cfg))
    )
    print('Created trunk:', trunk.sip_trunk_id)
    await lk.aclose()

asyncio.run(main())
"
```

Copy the printed `sip_trunk_id` into `dispatch-rule.json`'s `trunkIds`,
then create the dispatch rule the same way (or via `lk sip dispatch
create dispatch-rule.json`). `roomConfig.agents[].agentName` must exactly
match `AGENT_NAME` in `.env` / the `agent_name=` passed to `WorkerOptions`
in `agent/worker.py` (defaults to `The Real Estate Group`) - this is what
makes **explicit dispatch** work (plan.md §3.1 step 6), avoiding
unexpected auto-answers.

## 4. Outbound (deferred - not v1)

`outbound-trunk.json.example` is scaffolded for later (callback-to-confirm
use case) but explicitly **out of scope for v1** (inbound-only). Includes
the `headersToAttributes: {"X-Telnyx-Username": ...}` field LiveKit's
Telnyx guide calls out as necessary - without it, Telnyx's digest-auth
challenge can misroute the call to a different customer's connection by
source IP. Don't enable outbound until The Real Estate Group's TRAI DLT/NDNC compliance
posture is confirmed (plan.md §9.4) - that's a business/legal sign-off,
not a technical step.

## 5. Test

1. Start the worker: `uv run python -m agent.worker start`
2. Call the Telnyx number from a real phone.
3. Confirm the call connects and the agent's greeting plays.
4. Verify barge-in (interrupting the agent mid-sentence) actually stops
   agent speech - test explicitly with the Sarvam STT-based turn detection,
   don't assume it works identically to VAD-based detection (plan.md §9.6).
