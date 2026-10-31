# AX-13 — Data Preparation Spec (Phase 0)

> Read CLAUDE.md first for project context. This file specifies the synthetic dataset AX-13 runs on: what to generate, how, and how to check it. Build exactly this; ask before changing structure.

---

## 1. Goals

1. **Realistic enough to convince judges** that AX-13 would work on Paytm data.
2. **Built-in stories:** every merchant follows a hidden behaviour pattern (archetype), so the scoring has real signals to find.
3. **Measurable:** hidden ground-truth labels let us report precision/recall (S1-Scout's fact-check idea, now continuous).
4. **Repeatable demo:** three hand-scripted merchants behave identically on every run.
5. **Time-aware:** a simulation clock lets the demo "advance the day" into the fee go-live week and watch cases appear.

## 2. Principles

- **Synthetic only.** No real merchant data, no real personal data. Names from Faker `en_IN`; phone numbers fake and non-dialable format.
- **Deterministic.** One global seed in config; same seed → identical output.
- **Config-driven.** All proportions, dates, thresholds and ranges live in YAML, not hard-coded.
- **Labels are hidden.** Ground truth lives in a separate table that MCP servers never expose.
- **Facts vs assumptions.** Fee rules follow the proposed framework as reported (see §6); everything else is a plausible assumption and is marked as such in config comments.
- **Laptop scale.** Whole generation + load under 1 minute.

---

## 3. Scale and time window

| Item | Value | Notes |
|---|---|---|
| Merchants | 200 | Includes 3 scripted demo merchants |
| Zones | 2 | Zone A: Electronic City · Zone B: Jayanagar (names configurable) |
| Field agents | 6 (3 per zone) | Each with language set and visit capacity |
| History window | 120 days | Ends at `sim_start_date` |
| Future window | 30 days | Pre-generated, released day by day by the simulation clock |
| Festive window | configurable | `festive_start`, `festive_peak`, `festive_end` |
| Fee go-live | configurable | `fee_go_live_date`, placed inside the future window, after festive peak |

**Suggested defaults** (edit in `sim_config.yaml`): `sim_start_date` a few days before the festive peak; `fee_go_live_date` ~10 days into the future window. Keep dates as parameters — the real go-live date is not announced.

---

## 4. Files and folders

```
data/
  config/
    sim_config.yaml        # seed, dates, scale, fee rules, zones
    archetypes.yaml        # archetype proportions + generation parameters
    categories.yaml        # merchant categories, ticket sizes, weekly patterns
    demo_merchants.yaml    # the three scripted merchants, day-indexed events
  generator.py             # entry point: python -m data.generator
  generators/              # one module per table
  load_postgres.py         # loads outputs into Postgres
  validate.py              # sanity checks + summary report
  out/                     # parquet + csv outputs (gitignored except sample)
docs/                      # four markdown knowledge docs (§11)
audio/                     # recordings + ground-truth transcripts (§12)
```

Outputs: one Parquet (and CSV for eyeballing) per table in `data/out/`, then loaded into Postgres schema `ax13`.

---

## 5. Merchant categories

Mirror the merchant mix of a typical Bengaluru street (also matches Paytm's merchant illustration).

| Category | Share | Avg UPI ticket (₹) | Daily UPI txns (healthy) | Weekly pattern | Products typical |
|---|---|---|---|---|---|
| Kirana / general store | 25% | 150–450 | 60–180 | Weekend +10% | QR, Soundbox |
| Chai / snack stall | 12% | 20–60 | 80–250 | Weekday mornings strong | QR, Soundbox |
| Fruit / vegetable vendor | 12% | 80–250 | 40–120 | Weekend +15% | QR |
| Pharmacy | 10% | 250–900 | 40–120 | Flat | QR, Soundbox, POS |
| Restaurant / food stall | 12% | 200–700 | 40–150 | Fri–Sun +25% | QR, Soundbox, POS |
| Beauty salon | 8% | 300–1,500 | 10–40 | Weekend +30% | QR, Soundbox |
| Tailor / boutique | 7% | 400–2,500 | 5–25 | Festive-heavy | QR, Soundbox |
| Books / stationery | 6% | 100–600 | 15–60 | School-season bumps | QR |
| Electronics / mobile shop | 8% | 800–6,000 | 8–35 | Festive-heavy | QR, Soundbox, POS |

All values are assumptions for plausibility. Sample per merchant: base ticket from the range (lognormal), base daily count from the range, then apply archetype modifiers. Categories with high tickets (electronics, tailor, salon, pharmacy) naturally produce more payments above ₹2,000 and more fee-affected merchants.

---

## 6. Fee rules (proposed framework, as reported)

Encode in `sim_config.yaml` under `fee_rules`, with a comment that these follow press reports of the proposed NPCI framework and may change on final notification.

| Rule | Value |
|---|---|
| Fee rate | 0.4% |
| Applies to | P2M UPI payments above ₹2,000 |
| Cap | ₹300 per transaction |
| Exemption | Merchants with monthly UPI value under ₹1,00,000 |
| Go-live | `fee_go_live_date` (parameter) |

Derived fields per merchant-month (table `merchant_fee_monthly`, §8.13): `monthly_upi_value`, `value_above_2000`, `is_fee_affected`, `est_monthly_fee = min(0.4% × each txn above ₹2,000, ₹300)` summed. Because we store daily aggregates, approximate per-transaction cap using `value_above_2000 × 0.004` and apply the cap only if a single-transaction value above ₹75,000 is simulated (rare; electronics only).

**Definition:** `is_fee_affected = not exempt AND at least one payment above ₹2,000 in the month`. No separate materiality threshold. Each category has a share of merchants that take payments above ₹2,000 at all (`categories.yaml › large_ticket`), which is what makes the fee-affected share category-driven. Labels use the last full month before go-live (`reference_month`).

The fee layer stays minimal: the fee is one event in the story, not the focus.

---

## 7. Archetypes

Each non-scripted merchant gets exactly one archetype. Proportions sum to 100%.

| # | Archetype | Share | Data pattern (generation rules) | Expected line | Expected action |
|---|---|---|---|---|---|
| 1 | Healthy steady | 30% | Stable volume ±8% noise, uptime 98–100%, rare tickets | None | Watchlist |
| 2 | Festive grower | 12% | Volume ramps +20–45% into festive peak, stays elevated; clean device; no open tickets | Grow | Pitch next product (Soundbox/POS upgrade or loan if history ≥ 90 days steady) |
| 3 | Slow decliner | 10% | Volume declines 25–50% over 3–6 weeks; device healthy; no fault tickets; optional "competitor" note in a ticket or voice | Save | Retention call/visit; possible capped waiver |
| 4 | Device fault | 8% | Sudden drop 50–90% on onset day; Soundbox offline 3–10 days; complaint ticket 1–3 days after onset | Save | Remote fix: technician booking + message |
| 5 | Network not device | 6% | Failures and network errors concentrated in peak hours; device hardware OK; volume dip 10–20% at peaks only | Save | Network guidance message; **no** technician visit |
| 6 | Threshold holder | 6% | Monthly UPI ₹85k–₹99k; daily UPI drops 30–60% in last third of month once month-to-date passes ~₹90k; repeats over 2+ months | Grow | Honest cost comparison; no loan promise |
| 7 | Low-active ("zombie") | 12% | 1–6 UPI txns/day, often QR only, irregular days | Grow | Activation ladder: Soundbox / usage nudge |
| 8 | Closed shop | 4% | Zero txns for ≥ 14 days; device offline same period; no tickets | Neither (verify) | Remote check before any visit; mark inactive |
| 9 | Charge complainer | 6% | Unnotified deduction (₹3,999–₹7,999 "maintenance" or similar) in settlements; complaint ticket within 1–5 days; mild volume dip after | Save | Charge explanation; refund/waiver check; escalate if angry |
| 10 | Post-fee dropper | 6% | Healthy until go-live; drops 20–45% within 7 days after `fee_go_live_date`. About two thirds are fee-affected (monthly UPI ≥ ₹1L, payments above ₹2,000); about one third are **exempt merchants dropping from confusion** | Save | Fee-affected: personal cost estimate; waiver only if net positive; visit if high-value. Exempt: one "you are not affected" message, agent alone |

Onset dates: random within the history window for #3, #4, #5, #9 (some active near `sim_start_date` so cases are open at demo start); fixed relative to `fee_go_live_date` for #10. Keep ~30% of Save-archetype onsets in the future window so cases appear while the clock advances.

Noise: add daily Gaussian noise (σ 8–12% of base) and weekly pattern from §5 to all archetypes so signals aren't trivially clean.

---

## 8. Tables (Postgres schema `ax13`)

### 8.1 `zones`
| Column | Type | Notes |
|---|---|---|
| zone_id | text PK | `Z_A`, `Z_B` |
| name | text | Electronic City, Jayanagar |
| languages | text[] | e.g. {kn, hi, en} / {kn, en, ta} |
| waiver_budget_month | numeric | Zone waiver cap per month (assumption, e.g. ₹50,000) |
| waiver_cap_per_merchant | numeric | Agent-alone limit (e.g. ₹500/month) |

### 8.2 `field_agents`
| Column | Type | Notes |
|---|---|---|
| agent_id | text PK | |
| name | text | Faker en_IN |
| zone_id | text FK | |
| languages | text[] | |
| visits_per_day | int | 10–15 |
| home_lat, home_lng | float | Start point for routing |

### 8.3 `merchants`
| Column | Type | Notes |
|---|---|---|
| merchant_id | text PK | `M0001`… scripted ones fixed: `M_DEMO_SMALL`, `M_DEMO_RAVI`, `M_DEMO_MEENA` |
| name, owner_name | text | Faker en_IN; shop name + category word |
| category | text | From §5 |
| zone_id | text FK | |
| pin_code | text | Realistic Bengaluru pin codes per zone |
| lat, lng | float | Jittered within zone bounding box |
| language_pref | text | From zone languages |
| onboard_date | date | 6–60 months before sim start |
| has_qr, has_soundbox, has_pos | bool | Per category typical, with variation |
| soundbox_rent_month | numeric | e.g. ₹99–₹199 if has_soundbox (assumption) |
| assigned_agent_id | text FK | Nearest agent in zone |
| phone_masked | text | Fake, masked format |
| merchant_value_tier | text | low / mid / high, from trailing 90-day UPI value |

### 8.4 `txn_daily` (history) and `txn_daily_future`
One row per merchant per day. Future table holds the 30 future days, released by the clock (§10).

| Column | Type | Notes |
|---|---|---|
| merchant_id, date | PK | |
| upi_count | int | |
| upi_value | numeric | |
| upi_count_above_2000 | int | |
| upi_value_above_2000 | numeric | Needed for fee estimate |
| card_count, card_value | int, numeric | POS merchants only |
| failed_count | int | Base 0.5–1.5% of attempts |
| failed_debited_count | int | 5–15% of failures; higher for #5 at peaks |
| peak_hour_share | float | Share of txns 11:00–14:00 and 18:00–21:00 |

### 8.5 `device_health_daily`
| Column | Type | Notes |
|---|---|---|
| merchant_id, device_id, date | PK | Soundbox and/or POS |
| device_type | text | soundbox / pos |
| uptime_pct | float | 98–100 healthy |
| offline_minutes | int | |
| network_errors_peak | int | High for #5 |
| network_errors_offpeak | int | Low for #5 (key to "network not device") |
| hardware_fault_flag | bool | True for #4 during fault |
| last_heartbeat | timestamp | |

### 8.6 `settlements`
| Column | Type | Notes |
|---|---|---|
| settlement_id | text PK | |
| merchant_id, date | | T+1 settlement of previous day |
| gross_amount, net_amount | numeric | |
| delay_hours | int | Mostly 0; occasional delays |
| status | text | settled / delayed / held |

### 8.7 `deductions`
| Column | Type | Notes |
|---|---|---|
| deduction_id | text PK | |
| merchant_id, settlement_id, date | | |
| type | text | soundbox_rent / maintenance_charge / upi_fee / other. `upi_fee`: daily, notified, from G0, on fee-affected merchants |
| amount | numeric | |
| notified | bool | False for archetype #9 trigger deduction |

### 8.8 `tickets`
| Column | Type | Notes |
|---|---|---|
| ticket_id | text PK | |
| merchant_id | text | |
| opened_at, resolved_at | timestamp | resolved_at null if open |
| channel | text | call / app / field |
| category | text | device / settlement / charge / failed_txn / other |
| status | text | open / in_progress / resolved |
| summary | text | Short templated text in English (and some Hindi/Kannada transliterated), e.g. "Soundbox not announcing payments since Tuesday" |
| sentiment | text | calm / frustrated / angry (template-driven) |

Include occasional competitor mentions in #3 summaries ("other company offered free device").

### 8.9 `offers_history`
| Column | Type | Notes |
|---|---|---|
| offer_id | text PK | |
| merchant_id, date | | |
| product | text | soundbox / pos / loan / insurance / plan_change |
| channel | text | app / whatsapp / field |
| response | text | accepted / declined / no_response |

### 8.10 Empty tables AX-13 writes to
- `actions` (action_id, merchant_id, ts, line, action_type, payload_json, autonomy_tier, approved_by, status)
- `visits` (visit_id, agent_id, merchant_id, planned_date, stop_order, stop_type retain/pitch, brief, outcome, transcript_id)
- `crm_notes` (note_id, merchant_id, ts, source agent/voice/human, text, structured_json)
- `approvals` (approval_id, action_id, requested_at, decided_at, decision, decided_by)

### 8.11 Hidden: `merchant_labels` (evaluation only — never exposed via MCP)
Loaded into a separate schema, `ax13_eval`, so MCP servers working in `ax13` cannot reach it.
| Column | Type | Notes |
|---|---|---|
| merchant_id | text PK | |
| archetype | text | From §7 or `scripted` |
| onset_date | date | Null for steady archetypes |
| expected_line | text | save / grow / none |
| expected_action | text | e.g. technician_booking, cost_explainer, activation_nudge |
| expected_escalation | bool | e.g. angry #9, high-value #10 |
| is_fee_affected | bool | |
| notes | text | |

### 8.12 `sim_state`
Single row: `sim_date`, `fee_live` (bool). Updated by the clock. (Named `sim_date`, not `current_date`: that is a Postgres keyword and would silently return the wall-clock date.)

### 8.13 `merchant_fee_monthly` (derived)
Completed calendar months fully inside the history window only. The current month is computed live by the MCP server from released `txn_daily` rows (no cap adjustment live; the cap is rare).

| Column | Type | Notes |
|---|---|---|
| merchant_id, month | PK | month = first day of month |
| monthly_upi_value | numeric | |
| value_above_2000 | numeric | |
| is_fee_affected | bool | §6 definition |
| est_monthly_fee | numeric | 0 when not affected |

---

## 9. Generation logic (order)

1. Load configs; set seed.
2. Create zones and field agents.
3. Create merchants: sample category → products → base ticket and daily count → location in zone → assigned agent.
4. Assign archetypes by proportion (excluding scripted merchants); assign onset dates.
5. Generate daily series per merchant for history + future: base × weekly pattern × festive multiplier × archetype modifier × noise. Derive counts/values above ₹2,000 from ticket distribution.
6. Generate device health consistent with the transaction series (offline days → near-zero txns for Soundbox-dependent merchants; peak network errors for #5).
7. Generate settlements (T+1) and deductions (monthly rent; unnotified charge for #9).
8. Generate tickets from events with realistic lags; resolve most historical ones; leave recent ones open.
9. Generate offers history (sparse, random, with plausible responses).
10. Compute fee fields and `merchant_value_tier`; write labels.
11. Overlay the three scripted merchants from `demo_merchants.yaml` (§10), replacing any generated rows for them.
12. Write Parquet/CSV; run `validate.py`; load into Postgres.

---

## 10. Scripted demo merchants and simulation clock

Define day-indexed events relative to `sim_start_date` (D0) and `fee_go_live_date` (G0) in `demo_merchants.yaml`.

**M_DEMO_SMALL — small vendor (No action)**
- Fruit/vegetable vendor, Zone A, QR only, monthly UPI ₹30–40k, steady.
- At G0: not fee-affected. Expected: one "you are not affected" message, nothing else.

**M_DEMO_RAVI — Ravi, kirana (Save → Grow)**
- Kirana, Zone A, QR + Soundbox, Hindi preference, monthly UPI ~₹2.5–3L (fee-affected; ~₹1.2–1.5L of it in payments above ₹2,000 → est. fee ~₹500–600/month).
- History: D−21 → D0 UPI declines ~40%; Soundbox offline D−6 → D0 (`hardware_fault_flag`); complaint ticket at D−4, open ("Soundbox keeps disconnecting").
- Expected at D0: Save, churn ≈ 80+, growth paused → technician booking + WhatsApp update (agent alone).
- Future: device fixed D+1; volume recovers by D+7 with festive spike.
- At G0: volume dips ~25% within 5 days → second Save case: cost estimate, waiver check, field visit planned.
- Field visit at D+3 (device follow-up after the fix, before Diwali and before G0): visit recording (clip 1) carries loan interest + prepayment question + competitor mention + "come after Diwali" → Grow handoff.
- Sequence: D0 device Save → D+1 fixed → D+3 visit, clip 1, Grow handoff → D+5 Diwali → G0 fee dip, second Save case.

**M_DEMO_MEENA — Meena, tailor/boutique (Grow)**
- Tailor/boutique, Zone B, QR + Soundbox, Kannada/English, monthly UPI ₹88–98k across the last 3 months.
- Pattern: daily UPI drops ~50% in the last third of each month once month-to-date passes ~₹90k (threshold holder).
- Expected: Grow, honest cost comparison message. The agent's first loan draft must be blockable by the compliance gate (test fixture: a templated over-promising draft in `eval/fixtures/`).

**Simulation clock**
- MCP servers return only rows with `date <= sim_state.sim_date` (history + released future days).
- `advance_day()` increments `sim_date`; sets `fee_live = true` once `sim_date >= fee_go_live_date`.
- Streamlit control: "Advance 1 day", "Jump to fee go-live", "Reset".

---

## 11. Knowledge documents (`docs/`)

Write in plain English, in Anand's voice, with **numbered sections** so RAG citations read like "Retention playbook §3.1". Keep each 1–3 pages.

**`retention_playbook.md`**
- §1 Principles (Save before Grow, fix the cause, explain before offering).
- §2 Diagnosis guide (device vs network vs competitor vs charge vs fee).
- §3 Device issues: §3.1 Soundbox swap via technician; §3.2 remote reset steps; §3.3 POS faults.
- §4 Network issues: peak-hour guidance; no technician.
- §5 Settlement and failed-but-debited: status check, expected reversal timeline message, follow-up to closure.
- §6 Charges and fees: §6.1 explaining deductions; §6.2 fee estimate message template; §6.3 refund/waiver rules.
- §7 Waivers: economics check (retained revenue > waiver cost), zone caps, approval tiers.
- §8 Competitor risk: retention conversation guide for field agents.
- §9 Closed or inactive shops: remote verification before visits.

**`product_catalogue.md`**
- §1 Activation ladder (QR → Soundbox → steady volume → loan eligibility).
- §2 Soundbox; §3 POS; §4 Merchant loan via partner lenders (lender decides eligibility; disclosures required); §5 Insurance; §6 Plan changes.
- Each with: who it suits, signals that indicate readiness, what not to promise.

**`compliance_rules.md`** (own words; reference RBI Digital Lending Directions, 2025)
- §1 Any loan offer must state lender name, amount, tenure, APR, all charges, and include a KFS link.
- §2 No eligibility promises; lender decides.
- §3 No misleading or pressure claims; no linking credit to fee avoidance.
- §4 Consent before using data or recording.
- §5 Human approval required for credit offers.
- §6 Fee communication: accurate, based on the proposed framework, with the merchant's own numbers.

**`autonomy_policy.md`**
- §1 Three tiers (agent alone / agent prepares, human approves / human only) with the action lists from CLAUDE.md §5.4.
- §2 Confidence thresholds (e.g. score reasoning confidence, ASR confidence).
- §3 Waiver caps per zone and per merchant.
- §4 Escalation triggers (angry sentiment, high-value tier, third repeat issue, disputes).
- §5 Audit logging requirements.

---

## 12. Voice data (`audio/`)

> **Status (decided 31 Oct 2026): audio recording is deferred.** Phase 0 ships the three ground-truth transcripts as text (`audio/clip*.truth.json`), written as realistic Hindi/Kannada-English dialogue (Indian-language words in Roman script) containing every planted line. VoiceCore runs on these text transcripts first (extraction, CRM write-back, new signals). ASR and diarisation are added later, when the clips are recorded against the same scripts; the truth files then double as the reference for ASR/diarisation accuracy, with timestamps corrected after recording.

| Clip file | Merchant | Date | Channel |
|---|---|---|---|
| `clip1_ravi.truth.json` | M_DEMO_RAVI | D+3 (3 Nov), device follow-up visit | field_visit |
| `clip2_charge.truth.json` | M0082 (generated charge complainer: unnotified ₹3,999 on 14 Oct, ticket T00079) | D−1 (30 Oct) | support_call |
| `clip3_meena.truth.json` | M_DEMO_MEENA | D+4 (4 Nov) | field_visit |

Clip 2 points at a generated merchant, so `validate.py` checks that each transcript's merchant, deduction and ticket facts still match the data; a seed or config change that breaks this fails validation.

Truth-file fields beyond the format below: `date`, `channel`, `context`, `language`, `source`, `duration_s`, `consent` (turn indices), and in `expected_extractions`: `service_status`, `sentiment`, `entities`, `expected_escalation`, `planted_lines` (turn index per planted line).

The original recording plan follows, kept for when audio resumes.

**Clips to record (role-play with a friend, phone recording is fine)**

| Clip | Merchant | Language | Length | Planted lines (must be extractable) |
|---|---|---|---|---|
| 1 | Ravi (field visit after fix) | Hindi/English mix | 60–120 s | Device now working; asks about loan for festive stock; asks about prepayment; mentions competitor offering lower fees; "come back after Diwali" |
| 2 | Charge complainer (support call) | English/Kannada mix | 45–90 s | Unexplained ₹ deduction; angry tone; asks for refund; threatens to switch |
| 3 | Meena (field visit) | Kannada/English | 60–90 s | Says she stops taking UPI at month-end to avoid the fee; interested in growing; asks what the fee would actually cost |

**Recording guidance**
- Start each clip with a consent line ("This visit is being recorded for service quality, is that okay?" — "Yes").
- Two distinct speakers; avoid heavy overlap; mild background noise is good (realism).
- Save as 16 kHz mono WAV: `audio/clip1_ravi.wav`, etc.

**Ground truth per clip** (`audio/clip1_ravi.truth.json`)
```json
{
  "clip_id": "clip1_ravi",
  "merchant_id": "M_DEMO_RAVI",
  "speakers": {"A": "field_agent", "B": "merchant"},
  "turns": [{"speaker": "B", "start": 12.4, "end": 16.0, "text": "..."}],
  "expected_extractions": {
    "intents": ["loan_interest"],
    "questions": ["prepayment"],
    "objections": ["competitor_lower_fee"],
    "complaints": [],
    "commitments": [{"type": "follow_up", "when": "after_diwali"}]
  }
}
```
Fill turns after recording (manual or Whisper first-pass then correct).

---

## 13. Validation (`validate.py`)

Fail loudly if any check fails; print a summary report.

- Row counts: merchants = 200; txn_daily = merchants × 120; future = merchants × 30.
- Archetype proportions within ±2 percentage points of config.
- No negative counts or values; uptime within 0–100.
- Consistency: Soundbox offline days → UPI count drop for Soundbox-dependent merchants; closed shops have zero txns over their window.
- Fee: share of fee-affected merchants reported (expect roughly 25–40%, driven by categories); threshold holders' monthly UPI within ₹85k–₹99k; threshold holders' post-trigger daily UPI at least 30% below their pre-trigger level in every completed month.
- #10: fee-affected droppers are fee-affected in the reference month; exempt droppers are not.
- #5 network-not-device: peak errors ≫ off-peak errors; `hardware_fault_flag` false.
- #9 charge complainers: one unnotified deduction followed by a charge ticket within 5 days.
- Scripted merchants: each scripted event present on the right day.
- Voice: each `audio/*.truth.json` names a real merchant, and its deduction/ticket entities match the data.
- Labels: every merchant has exactly one label row; `merchant_labels` not referenced anywhere in `mcp_servers/` or `agent/` (grep check).
- Determinism: two runs with the same seed produce identical file hashes.

**Summary report** (`data/out/summary.md`): seed used; counts by zone, category, archetype; fee-affected count; open tickets at D0; three small charts (volume by archetype over time, fee-affected distribution, ticket categories).

---

## 14. Acceptance criteria for Phase 0

- `python -m data.generator && python -m data.validate` runs clean from a fresh clone.
- `python -m data.load_postgres` loads all tables into schema `ax13` via docker-compose Postgres (`infra/docker-compose.yml`), reading connection details from `.env` through `common/settings.py`.
- Summary report reviewed by Anand.
- Four docs drafted with numbered sections.
- Three ground-truth transcripts written as text (`audio/*.truth.json`); audio recording deferred (§12).
- Demo merchants verified by printing their 150-day timelines (`python -m data.timelines` → `data/out/demo_timelines.md`).

---

## 15. Starter prompt for Claude Code

> Read CLAUDE.md and DATA_PREPARATION.md. First run `python scripts/check_connections.py` and confirm Postgres is up. Then create `common/settings.py` (pydantic-settings, reads `.env`). Implement Phase 0. Start by proposing the three YAML configs (sim_config, archetypes, categories) and the demo_merchants.yaml with default values, and show them to me before writing the generator. Then build generator.py and validate.py, run them, and show me the summary report.
