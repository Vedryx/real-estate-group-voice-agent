# VJ Real Estate — Voice Agent Build Plan (plan.md)

> **Audience:** This document is written to be handed directly to Claude Code as a build spec.
> **Framework:** LiveKit Agents (Python) — chosen over Dograh/Pipecat-direct/Bolna after a comparative review (see §1).
> **Telephony:** Twilio (SIP trunk) → LiveKit SIP → LiveKit Agent.
> **Languages:** English + Hindi (code-switched "Hinglish" expected), Marathi supported as a bonus since the business is Pune-based.
> **Scope of this v1:** Answer real-estate enquiry calls, qualify the caller, match projects, answer follow-up questions grounded in real data, capture/log leads. No CRM/payment integration yet (explicitly out of scope per business owner).

---

## 0. How to use this document

Claude Code should treat this as the product + technical spec Dograh's own docs recommend giving to a coding agent: persona, call flow, rules, objection handling, success criteria, sample conversations — adapted here for LiveKit Agents instead. Sections are ordered so you can build top-to-bottom: data layer → agent logic → tools → telephony → tests.

Wherever this doc says "verify against repo," it means: the exact LiveKit Agents API surface changes between releases — confirm current syntax against the installed `livekit-agents` version's docs/examples before writing code, rather than trusting any code snippet here verbatim.

---

## 1. Why LiveKit Agents (not Dograh)

The user originally had Dograh (a no-code voice-agent builder built on top of Pipecat) running locally. After a comparison:

| Framework | Verdict |
|---|---|
| Dograh | No-code builder on Pipecat, MCP-native for Claude Code, but a wrapper layer — less control over multilingual STT/TTS provider mixing |
| Pipecat (direct) | Most mature raw pipeline framework, but you hand-wire everything |
| **LiveKit Agents** | **Chosen.** Native SIP telephony (works cleanly with Twilio), first-class multi-provider STT/LLM/TTS mixing (critical for Hindi/English), official multilingual turn-detector (supports Hindi), a dedicated **Sarvam AI plugin** built specifically for Indian languages including Hindi + Marathi + code-mixed "Hinglish," and a built-in pytest-based agent testing framework |
| Bolna | India-telephony-first (Exotel/Plivo), but user has already committed to Twilio |
| TEN Framework | Multimodal-first, unnecessary complexity here |
| Vocode | Stale (community/commits inactive) — avoid |

**Decision:** Build on `livekit-agents` (Python SDK). This does *not* preclude self-hosting — LiveKit server can be self-hosted or use LiveKit Cloud; that's an infra choice, not a scope change for this plan.

---

## 2. Business & data snapshot

### 2.1 Who VJ Real Estate actually is

Scraped from https://vjrealestates.com/ — **VJ Real Estate is a real-estate channel-partner/consultancy** based in Pune, Maharashtra, that markets residential inventory from multiple developers (Lodha, Raheja, Kolte Patil, Goyal Properties, Skyi, and independents). It is **not** the developer of these projects — it's the sales intermediary. This matters for the agent's tone: it should speak as VJ's consultant/representative, and can be transparent that it's presenting curated options from partner developers.

- Company phone: **+91 8180822450**
- Company email: virajjadhav91@gmail.com
- HQ: Pune, Maharashtra
- Tagline: trusted real-estate consultancy offering property solutions
- Standard process shown on site: **Find Property → Meet [Retailer/Agent] → Documents → Take the keys**

### 2.2 Important: the uploaded brochure ≠ VJ Real Estate's own brand

The uploaded PDF (`Brochure_WA_version_26-1.pdf`) is Goyal Properties' brochure for **"Codename Roots"** at Mamurdi — this is confirmed to be one of VJ's 9 listed projects (same PDF is linked from `vjrealestates.com/2-3-bhk-premium-residences-goyal-properties.php`). It is internally marked "For Internal Sales Training Purpose Only" and "conceptual, not a legal offering" — treat its facts as directional, not contractually binding, and the agent must never claim otherwise.

### 2.3 Current inventory — no commercial/shop listings found

**Flag for the business owner:** the user's original ask mentioned "flat or shop or business spaces," but nothing on vjrealestates.com or in the brochure lists standalone shops/offices for sale. Little Earth (Kolte Patil) mentions "40+ Commercials — Operational" but that reads as *already-operational retail within the township*, not inventory VJ is actively selling. **v1 of this agent should only claim residential (2/3 BHK) inventory**, and if a caller asks for commercial space, the agent should say so honestly and capture the lead rather than invent commercial listings. Confirm with the business owner whether commercial inventory exists before expanding scope.

### 2.4 Data quality issues found while scraping (fix before go-live)

1. **Raheja Vistas** project page shows identical specs/pricing to Lodha Panache (₹1.30Cr–₹1.70Cr, Hinjewadi) — apparent copy-paste bug. The homepage listing shows different, more plausible figures (2BHK ₹93L, 3BHK ₹1.52Cr, Mahalunge). **Do not trust either blindly — flag to VJ for confirmation before quoting a caller.**
2. **Codename Roots** homepage card has blank prices ("2 BHK |", "3 BHK |") — only the brochure PDF has real numbers. Use brochure figures.
3. Four nav "service areas" (**Punawale, Baner, Balewadi, Bavdhan**) have **zero actual listed projects** behind them (all `#` placeholder links) despite being in the site's location menu. Treat these as "areas VJ is interested in / may serve" not "areas with live inventory."
4. Metro-station proximity is explicitly stated for only **one** project (Lodha Sylvan — "5 mins walk from the metro station"). No other project page mentions metro distance. **The agent must never claim metro proximity for a project unless it's in the data — this is a common question and a common hallucination risk.**
5. RERA possession dates are given for only **one** project (AIKYAM — March 2029). All others are unknown. Do not fabricate possession dates.
6. Booking token offers (Platinum ₹99,000 / Gold ₹27,000 with listed benefits) are specific to **Codename Roots only** — do not generalize to other projects unless confirmed.
7. Little Earth's page carries a leftover page title "2-3 BHK Premium Residences Goyal Properties" (template bug) — the body content is genuinely Kolte Patil's Little Earth; ignore the mismatched heading.

These gaps should be surfaced to the VJ Real Estate owner as an action item; the seed data in §4 encodes them as `"unverified": true` flags rather than silently guessing.

---

## 3. Architecture

```
Caller (PSTN)
   │
   ▼
Twilio phone number  ──(Elastic SIP Trunk / TwiML <Dial><Sip>)──▶  LiveKit SIP inbound trunk
   │                                                                        │
   │ (outbound: LiveKit SIP outbound trunk → Twilio → PSTN)                ▼
   │                                                              LiveKit dispatch rule
   │                                                                        │
   │                                                                        ▼
   │                                                              LiveKit Room (per-call)
   │                                                                        │
   │                                                                        ▼
   │                                                       LiveKit Agent worker joins room
   │                                                        ┌─────────────────────────┐
   │                                                        │  AgentSession            │
   │                                                        │   STT: Sarvam (Saaras)   │
   │                                                        │   LLM: <see §3.2>        │
   │                                                        │   TTS: Sarvam (Bulbul)   │
   │                                                        │   Turn detection: STT-   │
   │                                                        │     based (Sarvam) or    │
   │                                                        │     MultilingualModel    │
   │                                                        │   VAD: Silero (fallback) │
   │                                                        └─────────────────────────┘
   │                                                                        │
   │                                                                        ▼
   │                                                          Tool calls → local data layer
   │                                                          (projects.json / SQLite,
   │                                                           leads log file)
```

### 3.1 Telephony setup (verify exact steps against current LiveKit docs — SIP provider UIs change)

1. Buy a Pune/India-capable number in Twilio (or confirm existing number).
2. Create a **Twilio Elastic SIP Trunk**, point Origination to your LiveKit SIP URI (`sip:<your-id>.sip.livekit.cloud` if using LiveKit Cloud, or your self-hosted SIP endpoint).
   - Alternative pattern also seen in the wild: a **TwiML Bin** with `<Dial><Sip>` pointing at the LiveKit SIP URI, assigned to the phone number's voice webhook. Either works; Elastic SIP Trunking is the more standard production path.
3. Create a LiveKit **inbound trunk** (`inbound-trunk.json` + `lk sip inbound create`) with Twilio's signaling IPs allow-listed for your Twilio region.
4. Create a **dispatch rule** so each inbound caller gets routed into their own room (prefix e.g. `call-`).
5. For **outbound** calling (e.g., callback to a lead), create a LiveKit **outbound trunk** and use `transfer_sip_participant` / outbound dial APIs — needed later for the "agent calls back to confirm site visit" use case, but not required for v1 inbound-only.
6. Give the agent an explicit name and use **explicit dispatch** for telephony (LiveKit recommends this over automatic dispatch for phone integrations, to avoid unexpected auto-answers).
7. Enable **SIP REFER** on the Twilio trunk (+ "Enable PSTN Transfer") if/when you add warm transfer-to-human escalation (§9.7).

### 3.2 LLM / STT / TTS decision (updated per Hindi/English requirement)

The user's original instinct — minimize the STT↔LLM↔TTS round trip — is sound, but a generic English-first speech-to-speech (S2S) model (e.g. OpenAI Realtime) has **unproven quality on Hindi/Hinglish/Marathi**, which is a bigger risk for this business than an extra ~100-200ms.

**Recommended primary stack (cascaded, but fast):**
- **STT:** Sarvam AI `saaras:v3` via `livekit-plugins-sarvam` — ~70ms processing latency, `language="unknown"` for auto-detect, natively understands **code-mixed Hindi-English ("Hinglish")** and covers Marathi (`mr-IN`) too, which matters since this is a Pune business and callers may mix Marathi in.
- **TTS:** Sarvam `bulbul:v3` — matching Indic voice quality, low latency, streaming supported.
- **Turn detection:** `turn_detection="stt"` (Sarvam plugin emits its own start/end-of-speech signals — do **not** also pass a separate VAD in this mode, per Sarvam+LiveKit integration guidance). Set `min_endpointing_delay≈0.07`.
- **LLM (the "brain" doing tool-calling/reasoning):** Use a strong general-purpose LLM here rather than Sarvam's own LLM, because **grounded tool-calling accuracy matters more than raw multilingual generation** for a business that must never misquote a price. Given the latency priority, favor a fast model — e.g. Claude Haiku-class or GPT-4.1-mini — over a larger/slower one, via LiveKit's `inference.LLM(...)` — confirm current supported model strings against LiveKit's docs at build time. The LLM only ever *reasons in text* — Sarvam handles the voice ends — so its own Hindi fluency matters less than its tool-calling reliability; keep the system prompt bilingual-aware so it composes natural Hindi/Hinglish replies for Sarvam to speak.
  - **Auth note:** this must be a proper Claude Console / Anthropic API key (or the equivalent OpenAI API key), created and billed separately from any Claude Code subscription. A Claude Code Pro/Max login is scoped to Claude Code itself and cannot be used to authenticate a separate running application per Anthropic's usage policy — see chat for detail.
- **Noise handling:** enable Krisp background-voice-cancellation plugin if available on the deploy OS (Linux/macOS) — real phone calls from busy streets/offices are noisy.

**Alternative worth empirically testing later:** Deepgram (`nova-3`, `language="multi"`) STT + Rime multilingual TTS, per LiveKit's own multilingual auto-switching tutorial — covers Hindi but not Marathi, and is a good fallback if Sarvam's LLM/voice quality doesn't meet bar in testing.

**Do not default to S2S (e.g. `openai.realtime.RealtimeModel`) for v1** given the Hindi/Hinglish requirement — keep it as a documented "fast-follow to test" item once the cascaded pipeline is stable, not the initial build target.

### 3.3 Agent structure: single agent + tools, with an escalation hand-off agent

Keep this a **single primary `Agent`** ("Aisha" or similar — pick a simple, easy-to-say-and-hear name in both English and Hindi) holding all the real-estate tools (§6), rather than over-splitting into many specialist agents — the conversation isn't complex enough to need it and a single agent keeps context (budget, location, BHK already stated) naturally in scope.

Use LiveKit's **multi-agent handoff** pattern for exactly one transition: handing off to a lightweight `HumanEscalationAgent` (or ending the session with a transfer) when the caller needs to speak to a human, is angry, or the topic is out of the AI's authority (payments, legal, price negotiation below published tokens).

---

## 4. Data model & seed data

Create `data/projects.json` from the content below (already scraped and cleaned — Claude Code does not need to re-scrape). Design the schema **city-first** even though every current record is Pune, so the same agent generalizes if VJ expands cities later (the user was explicit that this plan should not be Pune-hardcoded in its logic, even though the current data only covers Pune).

### 4.1 Schema

```json
{
  "id": "string (slug)",
  "name": "string",
  "developer": "string",
  "city": "Pune",
  "locality": "string (e.g. Ravet, Hinjewadi)",
  "property_type": "residential",
  "bhk_available": ["2BHK", "3BHK"],
  "unit_options": [
    {"label": "string", "carpet_sqft": 0, "price_inr_lakh_min": 0, "price_inr_lakh_max": 0, "notes": "string|null"}
  ],
  "land_parcel": "string|null",
  "amenities_highlights": ["string", "..."],
  "amenities_full_list": ["string", "..."] ,
  "metro_proximity": "string|null — ONLY set if explicitly confirmed; else null",
  "rera_possession": "string|null — ONLY set if explicitly confirmed; else null",
  "rera_number": null,
  "brochure_url": "string|null",
  "booking_tokens": [{"name": "string", "amount_inr": 0, "benefits": ["string"]}],
  "unverified_fields": ["list of field names Claude/agent should treat as low-confidence"],
  "source_url": "string"
}
```

### 4.2 Seed data (all 9 current VJ Real Estate listings)

```json
[
  {
    "id": "aikyam",
    "name": "AIKYAM",
    "developer": "Urway & Mangalam Group",
    "city": "Pune",
    "locality": "Ravet (Mumbai–Pune Highway)",
    "property_type": "residential",
    "bhk_available": ["2BHK", "3BHK"],
    "unit_options": [
      {"label": "2 BHK Comfort (single balcony)", "carpet_sqft": 715, "price_inr_lakh_min": 78, "price_inr_lakh_max": 78, "notes": "East-facing"},
      {"label": "2 BHK Comfort (double balcony)", "carpet_sqft": 755, "price_inr_lakh_min": 82, "price_inr_lakh_max": 82, "notes": "East-facing"},
      {"label": "2 BHK Luxe (double balcony + walk-in wardrobe)", "carpet_sqft": 810, "price_inr_lakh_min": 88, "price_inr_lakh_max": 88, "notes": "East-facing"},
      {"label": "3 BHK Comfort (walk-in wardrobe)", "carpet_sqft": 1035, "price_inr_lakh_min": 114, "price_inr_lakh_max": 114, "notes": "West/Highway-facing"},
      {"label": "3 BHK Luxe (walk-in wardrobe)", "carpet_sqft": 1133, "price_inr_lakh_min": 125, "price_inr_lakh_max": 125, "notes": "West/Highway-facing"}
    ],
    "land_parcel": "1 acre podium (amenities)",
    "amenities_highlights": ["30-storey towers", "Mivan construction", "50+ lifestyle amenities", "Mumbai-Pune Highway connectivity"],
    "amenities_full_list": [],
    "metro_proximity": null,
    "rera_possession": "March 2029",
    "rera_number": null,
    "brochure_url": "https://vjrealestates.com/image/Aikyam E Brochure.pdf",
    "booking_tokens": [],
    "unverified_fields": [],
    "source_url": "https://vjrealestates.com/2-3-bhk-premium-residences-aikyam.php"
  },
  {
    "id": "lodha-panache",
    "name": "Lodha Panache",
    "developer": "Lodha",
    "city": "Pune",
    "locality": "Hinjewadi",
    "property_type": "residential",
    "bhk_available": ["2BHK", "3BHK"],
    "unit_options": [
      {"label": "2 BHK Comfort", "carpet_sqft": 864, "price_inr_lakh_min": 130, "price_inr_lakh_max": 130, "notes": "East-facing"},
      {"label": "3 BHK Comfort", "carpet_sqft": 1133, "price_inr_lakh_min": 170, "price_inr_lakh_max": 170, "notes": "West/Highway-facing"}
    ],
    "land_parcel": "5.5 acres, 4 towers, 36 floors",
    "amenities_highlights": ["Fully air-conditioned apartments", "Premium Italian marble flooring", "World-class amenities"],
    "amenities_full_list": [],
    "metro_proximity": null,
    "rera_possession": null,
    "rera_number": null,
    "brochure_url": "https://vjrealestates.com/image/Lodha Panache - Unit and floor plans.pdf",
    "booking_tokens": [],
    "unverified_fields": [],
    "source_url": "https://vjrealestates.com/2-3-bhk-premium-residences-lodha-panache.php"
  },
  {
    "id": "lodha-magnus",
    "name": "Lodha Magnus (Tower 3)",
    "developer": "Lodha",
    "city": "Pune",
    "locality": "Hinjewadi",
    "property_type": "residential",
    "bhk_available": ["3BHK"],
    "unit_options": [
      {"label": "3 Bed with Study", "carpet_sqft": 1467, "price_inr_lakh_min": 235, "price_inr_lakh_max": 235, "notes": "starting price, '+' on source"},
      {"label": "3 Bed with Servant Room", "carpet_sqft": 1676, "price_inr_lakh_min": 270, "price_inr_lakh_max": 270, "notes": "starting price, '+' on source"}
    ],
    "land_parcel": "5.5 acres, 4 towers, 36 floors",
    "amenities_highlights": ["Fully air-conditioned apartments", "Premium Italian marble flooring", "World-class amenities"],
    "amenities_full_list": [],
    "metro_proximity": null,
    "rera_possession": null,
    "rera_number": null,
    "brochure_url": "https://vjrealestates.com/image/Lodha Magnus Brochure.pdf",
    "booking_tokens": [],
    "unverified_fields": ["price may be higher than listed ('+' suffix in source)"],
    "source_url": "https://vjrealestates.com/3-bhk-premium-residences-lodha-magnus.php"
  },
  {
    "id": "raheja-vistas",
    "name": "Raheja Vistas",
    "developer": "Raheja",
    "city": "Pune",
    "locality": "Mahalunge",
    "property_type": "residential",
    "bhk_available": ["2BHK", "3BHK"],
    "unit_options": [
      {"label": "2 BHK (homepage figure — UNCONFIRMED, conflicts with detail page)", "carpet_sqft": null, "price_inr_lakh_min": 93, "price_inr_lakh_max": 93, "notes": "detail page instead shows 1.30Cr, identical to Lodha Panache — likely a site content bug"},
      {"label": "3 BHK (homepage figure — UNCONFIRMED, conflicts with detail page)", "carpet_sqft": null, "price_inr_lakh_min": 152, "price_inr_lakh_max": 152, "notes": "detail page instead shows 1.70Cr, identical to Lodha Panache — likely a site content bug"}
    ],
    "land_parcel": null,
    "amenities_highlights": [],
    "amenities_full_list": [],
    "metro_proximity": null,
    "rera_possession": null,
    "rera_number": null,
    "brochure_url": "https://vjrealestates.com/image/Raheja Vistas - Opp doc e-version without CTA.pdf",
    "booking_tokens": [],
    "unverified_fields": ["price_inr_lakh_min", "price_inr_lakh_max", "carpet_sqft"],
    "source_url": "https://vjrealestates.com/2-3-bhk-premium-residences-raheja-vistas.php"
  },
  {
    "id": "lodha-sylvan",
    "name": "Lodha Sylvan (MIDC)",
    "developer": "Lodha",
    "city": "Pune",
    "locality": "MIDC, near Megapolis Circle, Hinjewadi Phase 3",
    "property_type": "residential",
    "bhk_available": ["2BHK", "2.5BHK", "3BHK", "3.5BHK"],
    "unit_options": [
      {"label": "2 BHK", "carpet_sqft": 836, "price_inr_lakh_min": 110, "price_inr_lakh_max": 110, "notes": null},
      {"label": "2.5 BHK", "carpet_sqft": 950, "price_inr_lakh_min": 135, "price_inr_lakh_max": 135, "notes": null},
      {"label": "3 BHK", "carpet_sqft": 1134, "price_inr_lakh_min": 175, "price_inr_lakh_max": 175, "notes": null},
      {"label": "3.5 BHK", "carpet_sqft": 1428, "price_inr_lakh_min": 220, "price_inr_lakh_max": 220, "notes": null}
    ],
    "land_parcel": "15 acres, 4 units/floor",
    "amenities_highlights": ["5-acre podium open space", "Reflection pond", "30,000 sq ft clubhouse", "Multipurpose sports lawn", "Kids play area", "5-star Lodha hospitality", "22 ft terrace decks (select units)", "Landscape by Landscape Tectonix Thailand", "Architecture by Kapadia Associates"],
    "amenities_full_list": [],
    "metro_proximity": "5 minutes' walk from the metro station",
    "rera_possession": null,
    "rera_number": null,
    "brochure_url": null,
    "booking_tokens": [],
    "unverified_fields": [],
    "source_url": "https://vjrealestates.com/2-3-bhk-premium-residences-midc.php"
  },
  {
    "id": "geo-aristo",
    "name": "GEO ARISTO",
    "developer": "GEO",
    "city": "Pune",
    "locality": "Ravet (BRT touch)",
    "property_type": "residential",
    "bhk_available": ["3BHK"],
    "unit_options": [
      {"label": "3 BHK", "carpet_sqft": 927, "price_inr_lakh_min": 102, "price_inr_lakh_max": 102, "notes": null},
      {"label": "3 BHK", "carpet_sqft": 1076, "price_inr_lakh_min": 118, "price_inr_lakh_max": 118, "notes": null},
      {"label": "3 BHK", "carpet_sqft": 1269, "price_inr_lakh_min": 140, "price_inr_lakh_max": 140, "notes": null},
      {"label": "3 BHK", "carpet_sqft": 1275, "price_inr_lakh_min": 141, "price_inr_lakh_max": 141, "notes": null}
    ],
    "land_parcel": null,
    "amenities_highlights": ["Sample flat ready", "20-storey tower", "4 high-speed lifts", "No MHADA flats", "Ground level: play area/temple", "Podium level: pool/gym", "Top floor: movie screening/bonfire with views"],
    "amenities_full_list": [],
    "metro_proximity": null,
    "rera_possession": null,
    "rera_number": null,
    "brochure_url": "https://vjrealestates.com/image/GEO Aristo -Ravet Ext & Int images(17 December 2025).pdf",
    "booking_tokens": [],
    "unverified_fields": [],
    "source_url": "https://vjrealestates.com/3-bhk-geo-aristo.php"
  },
  {
    "id": "codename-roots",
    "name": "Codename Roots",
    "developer": "Goyal Properties",
    "city": "Pune",
    "locality": "Mamurdi",
    "property_type": "residential",
    "bhk_available": ["2BHK", "3BHK"],
    "unit_options": [
      {"label": "2 BHK Classic", "carpet_sqft": 638, "price_inr_lakh_min": 62.5, "price_inr_lakh_max": 66, "notes": null},
      {"label": "2 BHK Premier", "carpet_sqft": 704, "price_inr_lakh_min": 66.25, "price_inr_lakh_max": 68.25, "notes": null},
      {"label": "2 BHK Signature", "carpet_sqft": 760, "price_inr_lakh_min": 70.5, "price_inr_lakh_max": 72.5, "notes": null},
      {"label": "3 BHK Classic", "carpet_sqft": 848, "price_inr_lakh_min": 81, "price_inr_lakh_max": 83, "notes": null},
      {"label": "3 BHK Premier", "carpet_sqft": 924, "price_inr_lakh_min": 87.5, "price_inr_lakh_max": 89.5, "notes": null},
      {"label": "3 BHK Signature", "carpet_sqft": 1036, "price_inr_lakh_min": 97, "price_inr_lakh_max": 99, "notes": null}
    ],
    "land_parcel": "26 acres total (10 acres Phase 1)",
    "amenities_highlights": ["Green Building Certified", "10-acre Phase 1", "Grand-scale community", "Multiple unit typologies", "Buzzing high street"],
    "amenities_full_list": [
      "Multipurpose sports court / basketball / box cricket", "Birds sanctuary", "Urban forest with hammock garden", "Tea pavilion / nature sitout", "Temple", "Amphitheatre seating along water body", "Social gathering space", "Bonfire seating", "Kids adventurous play area", "Senior citizen space", "Arrival plaza", "Pet park", "Jungle trail / jogging track", "Pickleball courts (3)", "Covered party space with event lawn", "Indoor games", "Digital room", "Podcast room", "Music room", "Coworking and book lounge", "Social den with thematic landscape", "Podium arrival plaza", "Botanical / fragrance garden", "Amphitheatre with stage", "Kids creche", "Multipurpose hall", "Stargazing deck", "Yoga deck", "Party lawn", "Landscape walkway", "Alfresco deck", "Futsal court", "Kids play pool", "Swimming pool (x2 clusters)", "Jacuzzi", "Pool deck", "Fitness centre", "Footover bridge", "Waterbody", "Lawn tennis", "Building-to-podium garden bridges"
    ],
    "metro_proximity": null,
    "rera_possession": null,
    "rera_number": null,
    "brochure_url": "https://vjrealestates.com/image/Brochure_WA version_26-1.pdf",
    "booking_tokens": [
      {"name": "Platinum Token", "amount_inr": 99000, "benefits": ["Spot booking advantage", "Flat discount ₹50,000", "Priority access to launch event & price", "₹2,00,000 discount on launch price", "Unit selection", "PLC waived off"]},
      {"name": "Gold Token", "amount_inr": 27000, "benefits": ["Refundable", "Access to launch event", "Access to pre-launch price", "Spot discount ₹50,000", "Discount on launch price ₹50,000", "PLC waived off"]}
    ],
    "unverified_fields": [],
    "source_url": "https://vjrealestates.com/2-3-bhk-premium-residences-goyal-properties.php",
    "developer_profile": {
      "years_experience": "39+",
      "projects_delivered": "32+",
      "happy_families": "10K+",
      "sqft_delivered": "3M",
      "sqft_ongoing": "1.2M",
      "sqft_upcoming": "8.4M",
      "green_building_certified": true,
      "after_sales_program": "The 'Hey' Initiative (Home, Experience & You) — pre-possession, in-the-interim, and post-possession support"
    }
  },
  {
    "id": "skyi-manas-lake-city",
    "name": "Skyi Manas Lake City",
    "developer": "Skyi",
    "city": "Pune",
    "locality": "Bhukum / Bhugaon",
    "property_type": "residential",
    "bhk_available": ["2BHK", "3BHK"],
    "unit_options": [
      {"label": "2 BHK", "carpet_sqft": 726, "price_inr_lakh_min": 64, "price_inr_lakh_max": 64, "notes": "onwards"},
      {"label": "3 BHK", "carpet_sqft": 909, "price_inr_lakh_min": 79, "price_inr_lakh_max": 79, "notes": "onwards"}
    ],
    "land_parcel": "100+ acre campus (new cluster 'Skyi Park': 10 acres, 10 buildings, 14 floors)",
    "amenities_highlights": ["70% open space near Sahyadri hills / NDA forest", "2500+ families already living", "4 top-rated schools nearby", "24/7 water supply", "Grocery, dispensary, restaurants, polyclinic, petrol pump, D-Mart, public transport on campus"],
    "amenities_full_list": [],
    "metro_proximity": null,
    "rera_possession": null,
    "rera_number": null,
    "brochure_url": "https://vjrealestates.com/image/294 A2 Unit Plans 2 BHK 1076 L Print file.pdf",
    "booking_tokens": [],
    "unverified_fields": [],
    "source_url": "https://vjrealestates.com/2-3-bhk-premium-residences-skyi-manas-lake-city.php"
  },
  {
    "id": "little-earth-kolte-patil",
    "name": "Little Earth",
    "developer": "Kolte Patil Developers Ltd.",
    "city": "Pune",
    "locality": "Mamurdi",
    "property_type": "residential",
    "bhk_available": ["2BHK", "2.5BHK", "3BHK"],
    "unit_options": [
      {"label": "2 BHK", "carpet_sqft": 681, "price_inr_lakh_min": 68, "price_inr_lakh_max": 68, "notes": "all-inclusive"},
      {"label": "2 BHK", "carpet_sqft": 711, "price_inr_lakh_min": 70, "price_inr_lakh_max": 70, "notes": "all-inclusive"},
      {"label": "2.5 BHK", "carpet_sqft": 860, "price_inr_lakh_min": 85, "price_inr_lakh_max": 85, "notes": "all-inclusive"},
      {"label": "3 BHK", "carpet_sqft": 947, "price_inr_lakh_min": 95, "price_inr_lakh_max": 95, "notes": "all-inclusive"},
      {"label": "3 BHK", "carpet_sqft": 1024, "price_inr_lakh_min": 100, "price_inr_lakh_max": 100, "notes": "all-inclusive, limited inventory"},
      {"label": "3 BHK", "carpet_sqft": 947, "price_inr_lakh_min": 94, "price_inr_lakh_max": 94, "notes": "all-inclusive, limited inventory"},
      {"label": "3 BHK", "carpet_sqft": 1015, "price_inr_lakh_min": 100, "price_inr_lakh_max": 100, "notes": "all-inclusive, limited inventory"}
    ],
    "land_parcel": "25 acres total; 24 buildings (16 delivered, 6 under construction, 2 future)",
    "amenities_highlights": ["60+ amenities", "5 clubhouses", "~1 km from Mumbai–Bangalore Expressway", "40+ operational commercial/retail within township (not standalone shop inventory for sale)"],
    "amenities_full_list": [],
    "metro_proximity": null,
    "rera_possession": null,
    "rera_number": null,
    "brochure_url": null,
    "booking_tokens": [],
    "unverified_fields": [],
    "source_url": "https://vjrealestates.com/2-3-bhk-premium-residences-little-earth-by-kolte-patil-developers-ltd.php"
  }
]
```

### 4.3 Service-interest-only areas (no live inventory — do not claim a project exists here)

```json
["Punawale", "Baner", "Balewadi", "Bavdhan"]
```
These appear in VJ's site navigation as areas of interest but have no linked project. If a caller asks about these specifically, the agent should say VJ doesn't currently have a live listing there, offer the nearest actual project, and log the area as an interest lead.

### 4.4 Approximate work-hub proximity map (for the "near my workplace" logic — §5)

**Flag: distances below are derived from general Pune geography knowledge, not a maps API. They are directional only — Claude Code should either (a) mark them clearly as approximate in the agent's phrasing ("roughly a 15–20 minute drive," never an exact number), or (b) better: wire a real distance/maps tool (Google Maps Distance Matrix or similar) so the agent computes live estimates instead of relying on this static table. Do not let the voice agent state a precise commute time that isn't backed by a real distance calculation.**

| Work hub / IT corridor | Reasonably close listed projects |
|---|---|
| Hinjewadi IT Park (Phases 1/2/3), Rajiv Gandhi Infotech Park | Lodha Panache, Lodha Magnus, Lodha Sylvan (closest — near Megapolis Circle) |
| Ravet / Mumbai–Pune Highway commuters, PCMC | AIKYAM, GEO ARISTO |
| Balewadi High Street / Baner-adjacent | Raheja Vistas (Mahalunge) |
| Bhukum / Bhugaon / south-west Pune | Skyi Manas Lake City |
| Mamurdi / Dehu Road / PCMC outskirts | Codename Roots, Little Earth |
| Kharadi, Magarpatta, Viman Nagar, Hadapsar (East Pune IT hubs) | **None of VJ's current listings are close** — agent must say so honestly rather than force a match |

### 4.5 Lead log schema (local file/SQLite — no CRM integration in v1)

```json
{
  "lead_id": "uuid",
  "timestamp": "iso8601",
  "caller_phone": "string (from SIP)",
  "name": "string|null",
  "preferred_language": "en|hi|mr|hinglish",
  "requested_city": "string",
  "requested_locality": "string|null",
  "bhk_preference": "string|null",
  "budget_range_inr_lakh": [0, 0],
  "property_type_requested": "residential|commercial|unspecified",
  "projects_discussed": ["project_id", "..."],
  "workplace_area": "string|null",
  "outcome": "qualified_lead|callback_requested|site_visit_requested|not_serviceable_area|info_only_no_lead|escalation_needed|opted_out|spam_or_abandoned",
  "notes": "string",
  "consent_to_be_contacted": true
}
```
Store as append-only JSON Lines (`leads.jsonl`) or a local SQLite table for v1 — deliberately simple since the business owner confirmed **no CRM/webhook integration for now**. Design the write function as a single `log_lead()` tool call so swapping in a CRM later is a one-function change, not a rewrite.

---

## 5. Location & language handling logic

### 5.1 City gate (generalized — not Pune-hardcoded)

The agent's data only covers Pune today, but the **logic** should be city-agnostic so this scales if VJ adds cities later:

1. Ask/detect the caller's city of interest.
2. If `requested_city` matches a city present in `projects.json` → proceed to locality/BHK/budget qualification.
3. If it does **not** match any served city (e.g., caller says "I'm relocating to Mumbai" or "Chennai") → the agent must **not** invent inventory. Say clearly that VJ Real Estate currently doesn't have listings in that city, then pivot: *"We do have properties in Pune, though — if you or your family have any connection to Pune, or if you're open to it, I can share what's available there."* Log as `not_serviceable_area` with the requested city noted (valuable market-expansion signal for the business).
4. If the caller's stated location is a **locality within a served city but has no matching project** (e.g., Baner/Balewadi/Bavdhan/Punawale in Pune, §4.3) → don't say a flat "no." Say VJ doesn't have a live project in that exact micro-market, then offer the nearest actual listed project with an honest approximate-distance caveat, and log an interest lead for that locality.

### 5.2 "Near my workplace" logic

If the caller mentions a workplace/office location instead of a residential-area preference (e.g., "I work in Hinjewadi, what's close by"), use the proximity map in §4.4 (or, better, a live maps tool — see the flag there) to suggest 1–3 relevant projects, always caveated as approximate unless backed by a real distance calculation.

### 5.3 Language handling

- Detect language per-utterance (Sarvam STT `language="unknown"` handles this, including code-mixed Hinglish).
- Default greeting: bilingual opener that lets the caller self-select — e.g., "VJ Real Estate mein aapka swagat hai — Hi, this is VJ Real Estate's assistant. Aap Hindi mein baat karna chahenge ya English mein?" (exact copy should be refined/reviewed by a native Marathi/Hindi speaker on the VJ team before go-live — do not treat this draft line as final, verify tone and phrasing culturally).
- Once a caller settles into a language/mix, **mirror it** rather than switching TTS output language every time a single English word (e.g. a project name or "BHK") appears mid-sentence — Sarvam's code-mixing support exists specifically so the agent doesn't need to hard-switch.
- Numbers (prices, sqft) should be read in the digit/unit convention natural to the spoken language (e.g., "72 lakh" not "seventy-two hundred thousand") — verify Sarvam TTS handles Indian numbering (lakh/crore) correctly in testing; this is a common failure point for non-Indic TTS engines and part of why Sarvam was chosen over a generic multilingual TTS.

---

## 6. Tool / function catalog

Implement each as a LiveKit `@function_tool` async Python function (verify current decorator import path against the installed SDK version — seen as `from livekit.agents.llm import function_tool` in current examples). Every tool must be the **only** path to specific facts — the agent's system prompt (§7) must instruct it to never state a price, amenity, possession date, or metro claim without having called the relevant tool first.

| Tool | Purpose | Key params | Returns |
|---|---|---|---|
| `search_projects` | Primary matching tool | `city`, `locality?`, `bhk?`, `budget_max_lakh?`, `budget_min_lakh?`, `property_type?` | List of matching project summaries (id, name, locality, price range, bhk) |
| `get_project_details` | Full facts for one project once caller narrows down | `project_id` | Full record from §4.2 including `unverified_fields` |
| `get_amenities` | Amenity Q&A | `project_id` | `amenities_full_list` if present, else `amenities_highlights` with a caveat that the full list isn't confirmed |
| `get_pricing` | Precise price lookup | `project_id`, `bhk?` | Unit options with price/size; must surface `unverified_fields` flags in the response text passed back to the LLM so it can hedge appropriately |
| `check_metro_proximity` | Handles the "is metro near this project" question directly | `project_id` | Returns the stated distance if present, otherwise an explicit "not confirmed for this project" signal — **never guess** |
| `get_nearby_projects_to_workplace` | Workplace-proximity matching | `workplace_area` | List of projects from the §4.4 map, or an honest "none nearby" result |
| `log_lead` | Persist a lead | full lead schema (§4.5) | confirmation + `lead_id` |
| `log_out_of_area_interest` | Persist demand signal for unserved city/locality | `requested_city_or_locality`, contact info | confirmation |
| `schedule_site_visit` | Capture a site-visit request (no calendar integration in v1 — just logs intent + preferred date/time for a human to action) | `project_id`, `preferred_date`, `contact` | confirmation |
| `escalate_to_human` | Triggers hand-off agent / warm transfer or callback logging | `reason` | either transfers via SIP REFER (if configured, §3.1 step 7) or logs a priority callback request |
| `end_call` | Graceful hang-up with outcome tag | `outcome` (enum from §4.5) | ends session |

---

## 7. Agent persona & global rules (system prompt content)

**Persona:** A warm, professional voice representative of VJ Real Estate — a Pune-based real estate consultancy (not a developer). Speaks naturally in English, Hindi, or a Hindi-English mix depending on the caller. Efficient but not pushy; a helpful local expert, not a hard-sell telemarketer.

**Non-negotiable rules — bake these into the system instructions verbatim in spirit:**

1. **Never state a price, size, possession date, RERA detail, or amenity that isn't returned by a tool call.** If a tool doesn't have the answer, say so honestly and offer a human follow-up — do not improvise.
2. **Always disclose it's an AI assistant** if asked directly, or proactively near the start of the call (jurisdictions increasingly require bot disclosure; also just good practice for trust).
3. **Never collect or process payment information over the call.** No card numbers, no UPI IDs, no bank details. Token bookings (§4.2 `booking_tokens`) are described informationally only; actual payment always routes to a human/secure link.
4. **Never request or store sensitive PII** beyond name, phone, email, city/locality preference, budget band, and BHK preference. If a caller volunteers something sensitive (PAN, Aadhaar, bank details) unprompted, don't repeat it back or store it verbatim — politely note it isn't needed over a call.
5. **Never guarantee investment returns, resale value, or price negotiation beyond published token benefits.** These are speculative/financial-advice territory — give general framing only ("our team can discuss current market trends with you") and don't commit VJ to a number the agent isn't authorized to promise.
6. **Never fabricate a RERA registration number.** None are in the current data; if asked, say the team will share it and log the request.
7. **Respect opt-outs immediately** — if a caller asks not to be contacted again, log `opted_out` and end the call without further pitching.
8. **Escalate rather than argue** — if a caller is upset, frustrated, or explicitly asks for a human, don't try to resolve everything in-model; use `escalate_to_human`.
9. **Consent/recording disclosure** at call start if the call is recorded (confirm with VJ what their actual recording policy is — don't assume; if recording, disclose it per applicable Indian telecom/consumer-protection norms).
10. Never claim commercial/shop inventory exists (per §2.3) unless the business owner confirms and the data model is updated.

---

## 8. Conversation flow

```
1. Greeting (bilingual opener, §5.3) + AI disclosure
2. Intent: residential or commercial?
     → commercial: honest "we don't currently have commercial listings" (§2.3) + offer to log interest + end/redirect
     → residential: continue
3. City: which city are you looking in?
     → not Pune: §5.1 step 3 pivot script, log_out_of_area_interest, offer Pune anyway, then either continue with Pune or gracefully close
     → Pune: continue
4. Locality / workplace-proximity preference (open question — accept either an area name or "near my office in X")
5. BHK preference (2/3/other) + budget band
6. search_projects tool call → present top 2–3 matches conversationally (not a data dump — mention name, locality, starting price, one standout amenity)
7. Open Q&A loop — handle any follow-up using the tool catalog (§6), looping as long as the caller has questions:
     - amenities, pricing detail, metro proximity, possession timeline, land parcel/scale,
       comparisons between 2 projects, booking token benefits, "which is best for me" (needs-based, not opinion-based)
8. Next-step branch:
     - wants site visit → schedule_site_visit
     - wants callback / more info → log_lead with outcome=callback_requested
     - not ready / just browsing → log_lead with outcome=info_only_no_lead (still capture name+phone if offered)
     - upset / wants human now → escalate_to_human
     - not interested at all → thank + end_call(outcome=info_only_no_lead or opted_out)
9. Close: confirm next steps out loud, thank the caller, end_call with correct outcome tag
```

Handle **topic jumps** gracefully — a caller may ask about metro proximity mid-way through budget qualification, or switch from discussing one project to another. Don't force strict linear state-machine progression; treat steps 4–7 as available at any point once intent + city are established, and track what's already been collected in session state so the agent doesn't re-ask.

---

## 9. Edge cases (build test coverage against every row)

### 9.1 Location
- Caller wants a city VJ doesn't serve (Mumbai, Chennai, Bangalore, etc.) → §5.1 step 3 script, never invent inventory.
- Caller gives a Pune locality with no live project (Baner/Balewadi/Bavdhan/Punawale) → §5.1 step 4 script.
- Caller gives a fuzzy/misspelled/nearby-but-not-exact area (e.g., "Wakad" — adjacent to Hinjewadi corridor but not itself in the data) → fuzzy-match to nearest known locality and confirm rather than silently guessing exact-match; if no reasonable match, ask a clarifying question.
- Caller is relocating in the future (not immediate) — still qualify normally, note timeline in lead notes.
- Multiple family members with different city needs on one call — handle sequentially, may need two separate `log_lead` calls.

### 9.2 Inventory / unit type
- Caller wants 1 BHK → not offered anywhere in current inventory; say so, offer smallest available (2 BHK) as an alternative, log interest for 1 BHK (useful demand signal for VJ).
- Caller wants 4 BHK / villa / bungalow / independent plot → not in current inventory; same honest-no + log pattern.
- Caller wants commercial/shop/office → §2.3 / §7 rule 10.
- Caller wants immediate/ready possession → only AIKYAM has a stated (2029, i.e., **not** immediate) date; for everything else, be explicit that possession timelines aren't confirmed in the data and offer a callback with accurate info.
- Caller asks for built-up area, not carpet area → only carpet area is in the data; state that clearly rather than converting/guessing.
- Budget doesn't match any inventory (e.g., 3 BHK under ₹50L) → say honestly no match at that price, offer nearest higher-priced option or a 2 BHK alternative instead of stretching the truth.
- Ambiguous project name (e.g., "the Lodha one" without specifying Panache/Magnus/Sylvan) → ask a clarifying question rather than guessing.
- Caller wants to compare two named projects → use `get_project_details` on both, give a factual side-by-side, only on dimensions present in data for both.

### 9.3 Pricing / financial
- Caller asks for a discount/negotiation beyond published tokens → don't authorize any number the agent doesn't have; offer to connect a human for negotiation.
- Caller asks about EMI/loan eligibility/interest rates → general info only ("our team can help connect you with lending partners"), no specific advice — this is financial-advice territory (per legal_and_financial_advice policy).
- Caller asks about total cost including stamp duty/registration/GST → only some listings are marked "all-inclusive" (Little Earth); for others, explicitly flag that quoted price may not include statutory charges and offer to confirm exact figures.
- Caller asks about resale value / investment ROI → no guarantees, general framing only.
- Caller asks in a non-INR currency (USD, etc.) → don't attempt a live FX conversion (no live rate available to the agent); state prices in INR lakh/crore and suggest the caller check current rates themselves.
- Caller wants to actually pay/book the token over the call → refuse per §7 rule 3, route to human/secure payment link.

### 9.4 Legal / compliance
- Caller asks for a RERA number → not in data for any project; don't fabricate, log request.
- Caller asks about litigation/title/legal status → outside agent's authority; escalate.
- Caller asks "are you legally allowed to call me" / DND-list questions (relevant especially for **outbound** calling) → have an honest, compliant answer ready referencing consent basis; **flag to the business**: confirm VJ's TRAI DLT/NDNC compliance posture for any outbound calling before enabling outbound in this system — this plan does not certify legal compliance, that's a business/legal decision for VJ.
- Caller invokes "do not call me again" → immediate opt-out log + end, no further pitching, and this should be checked before any future outbound attempt (v1 has no CRM to enforce this automatically — flag as a real limitation, not silently ignored).

### 9.5 Data-grounding / hallucination risk
- Caller asks about metro proximity for any project except Lodha Sylvan → must say "not confirmed" rather than assume proximity or absence.
- Caller asks about a specific tower/flat number's live availability → this is dynamic, not in static seed data; never claim to know real-time unit-level availability, always offer to have a human confirm.
- Caller asks something with zero data coverage at all (e.g., school district, specific builder's other cities) → say honestly it's outside current information, offer callback.

### 9.6 Conversational / UX
- Caller speaks in a language/dialect mix the STT struggles with → graceful re-prompt ("could you say that again?") rather than guessing after 1 failed attempt; escalate after repeated failures.
- Background noise / call quality issues → confirm details back before logging, don't silently record a possibly-misheard phone number.
- Caller interrupts mid-sentence (barge-in) → LiveKit's turn handling should stop agent speech immediately and listen; verify this works with the Sarvam STT-based turn detection specifically (test explicitly, don't assume).
- Long silence / caller goes quiet → timeout with a gentle re-engagement prompt, then graceful end after repeated silence (avoid dead air hangups with no warning).
- Caller goes off-topic (asks about weather, tells a joke, chats generally) → brief, warm, then redirect to real estate; don't sustain long out-of-domain chat.
- Caller asks "are you a real person" → always answer truthfully (§7 rule 2).
- Caller is a prank call / spam / no response at all → simple retry-then-hangup loop guard, don't loop indefinitely.
- Repeat caller who's called before → v1 has no CRM/dedup, so the agent won't recognize returning callers — flag as a known limitation, not a silent failure.

### 9.7 Escalation / human handoff
- Caller explicitly asks for a human at any point → immediate `escalate_to_human`, don't argue or try to retain the call.
- Caller is angry about a past experience with VJ or a builder → de-escalate calmly, log the complaint verbatim in `notes`, escalate.
- Caller wants brochure sent via WhatsApp/email → v1 has no SMS/WhatsApp/email-sending integration; capture contact details and log the request rather than promising a document that can't actually be sent yet (flag as a v2 feature, §12).

---

## 10. Sample conversations

*(Draft only — have a native Hindi/Marathi speaker on the VJ team review tone and phrasing before using in production; word choices below are directional.)*

### 10.1 English, happy path

> **Agent:** Hi, thanks for calling VJ Real Estate — I'm an AI assistant here to help you find the right property. Are you looking for something residential, or a commercial space?
> **Caller:** Residential. Looking for a 2 BHK.
> **Agent:** Great — which city and area are you looking in?
> **Caller:** Pune, somewhere near Hinjewadi, I work there.
> **Agent:** *(calls get_nearby_projects_to_workplace)* Got it. For Hinjewadi, our closest options are Lodha Sylvan — that one's actually a 5-minute walk from the metro station — and Lodha Panache, both right in Hinjewadi. What's your budget range roughly?
> **Caller:** Around 1.1 to 1.2 crore.
> **Agent:** *(calls search_projects)* Lodha Sylvan has a 2 BHK at 836 sq ft for about ₹1.10 crore — that fits well. Want me to walk you through the amenities, or should we look at scheduling a site visit?
> **Caller:** Is it really that close to the metro?
> **Agent:** *(calls check_metro_proximity)* Yes — that's specifically listed as a 5-minute walk from the metro station for Lodha Sylvan.
> **Caller:** Great, can you have someone call me?
> **Agent:** Of course — can I get your name and the best number to reach you?
> *(→ log_lead, outcome=callback_requested)*

### 10.2 Out-of-area pivot

> **Caller:** Hi, I'm moving to Chennai for work, do you have anything there?
> **Agent:** *(calls log_out_of_area_interest)* We don't currently have listings in Chennai — VJ Real Estate's projects are all in Pune right now. If you'd still like, I'm happy to note your interest in case that changes, or if you have any connection to Pune, I can share what's available there.
> **Caller:** No, it has to be Chennai.
> **Agent:** Totally understand — I've noted your interest for Chennai. If VJ expands there, we'll have your details. Thanks for calling!
> *(→ end_call, outcome=not_serviceable_area)*

### 10.3 Hinglish, grounded refusal on unknown data

> **Caller:** Codename Roots ka RERA number kya hai?
> **Agent:** Woh number abhi mere paas confirm nahi hai — main note kar deti hoon, aur hamari team aapko exact RERA number share kar degi. Aapka number bata sakte hain?
> *(→ log_lead with notes="requested RERA number for codename-roots")*

---

## 11. Testing plan

LiveKit Agents ships a pytest-based testing harness (`AgentSession.run(user_input=...)` + `result.expect...`) — use it to build a **golden test suite** mapping directly to §9's edge-case list, not just the happy path. At minimum, write one test per row in §9 (roughly 30+ tests). Structure suggestion:

- `test_location_out_of_city.py` — asserts the agent calls `log_out_of_area_interest` and never calls `search_projects` with a fabricated city match.
- `test_no_hallucinated_metro.py` — for every project except Lodha Sylvan, assert the agent's response to a metro question does not affirmatively claim proximity.
- `test_no_payment_collection.py` — assert the agent refuses to accept card/UPI details if a caller offers them mid-call.
- `test_unit_type_not_offered.py` — 1BHK/4BHK/commercial requests correctly get an honest "not available" + logged interest.
- `test_price_conflict_hedged.py` — Raheja Vistas price question surfaces the `unverified_fields` hedge rather than confidently quoting either conflicting number.
- Bilingual smoke tests — same scenarios run with Hindi/Hinglish `user_input` to confirm tool-calling still triggers correctly regardless of input language.

---

## 12. Build phases (for Claude Code to execute in order)

1. **Data layer** — create `data/projects.json` (§4.2), `data/service_interest_areas.json` (§4.3), `data/workplace_proximity.json` (§4.4), lead storage (SQLite or `leads.jsonl`).
2. **Core agent** — `Agent` class with system instructions (§7), wired to `search_projects`/`get_project_details`/etc. tools (§6) reading from the data layer. Test in text/console mode first (no audio) to validate tool-calling logic before adding voice.
3. **Voice pipeline** — wire Sarvam STT/TTS + chosen LLM (§3.2) into `AgentSession`, verify turn detection and barge-in work via LiveKit's local dev/playground tooling before touching telephony.
4. **Telephony** — Twilio SIP trunk + LiveKit inbound trunk + dispatch rule (§3.1); test with a real inbound call before adding outbound.
5. **Edge-case pass** — implement + pass all tests in §11.
6. **Compliance pass** — recording disclosure, AI disclosure, opt-out handling, review of any outbound-calling legal posture with the business owner (§9.4) — do not enable outbound calling until this is explicitly signed off.
7. **Handoff to VJ** — get the business owner to resolve the open items in §13 before go-live.

**Explicitly out of scope for this build** (confirmed with the business owner): CRM/webhook integration, payment processing, WhatsApp/SMS/email brochure sending, multi-city expansion beyond Pune, live/real-time unit-availability sync. Design the tool layer so these are clean additions later (e.g., `log_lead` as a single swappable function) rather than blocking v1 on them.

---

## 13. Open questions for the VJ Real Estate business owner (resolve before go-live)

1. Does VJ actually sell commercial/shop/office inventory anywhere, or was that out of scope from the start? (§2.3)
2. Confirm real pricing for Raheja Vistas — the site has two conflicting numbers. (§2.4 item 1)
3. Is the site's list of "service areas" (Punawale, Baner, Balewadi, Bavdhan) meaningful — does VJ actually want leads from those areas even without live inventory? (§2.4 item 3)
4. What is VJ's actual call-recording and consent policy, so the agent's opening disclosure line is accurate? (§7 rule 9)
5. Confirm compliance posture (TRAI DLT/NDNC registration, consent basis) before any **outbound** calling is enabled. (§9.4)
6. Who reviews the draft bilingual scripts (§5.3, §10) for tone/correctness before launch — need a native Hindi/Marathi speaker sign-off.
7. Where should captured leads actually surface for the sales team in the interim (shared spreadsheet export from `leads.jsonl`? manual check-ins?) given there's no CRM integration yet.
