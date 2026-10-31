# AX-13 — Project Context and Scope

> This file primes Claude (in Claude Code or a claude.ai Project) with everything decided so far about AX-13, and with how to work with Anand. Read it fully before doing anything. Keep it updated when decisions change.

---

## 1. How to work with Anand

**Communication style he wants**
- Factual and direct. No hedging, no self-congratulation, no hype words.
- Keep his voice in any written content (slides, pitch lines, README). Edit for clarity; don't rewrite into generic AI prose.
- Push back on weak ideas with a reason. He brings in suggestions from other tools (e.g. Google AI summaries); triage them honestly into "already covered / worth adding / correct or drop".
- Flag any number that is not sourced. Illustrative numbers must be labelled illustrative.
- One clarifying question at a time, only when it changes what gets built. Otherwise make a sensible assumption and state it.
- Before large builds, show the plan or structure first. Build in phases; confirm each phase works before moving on.
- Prefer prose explanations with light structure; use tables for comparisons and specs.

**Working rules for this repo**
- Python 3.11+. Deterministic outputs (fixed seeds). Everything runs offline on a laptop except LLM API calls.
- Never put real merchant data or real personal data in the repo. All data is synthetic.
- AX-13 code must only reach merchant data through the MCP servers (`mcp_servers/`), never by querying Postgres directly. Only `mcp_servers/` and `data/load_postgres.py` may open a Postgres connection to schema `ax13` (the LangGraph checkpointer uses its own schema). This is what makes "swap mock for real" true.
- All configuration (keys, endpoints, model ids) is read through `common/settings.py` from `.env`. No key or model id is hard-coded anywhere else.
- Hidden ground-truth labels are for evaluation only and must never be exposed through MCP tools or prompts.

---

## 2. Event and status

- **Event:** Paytm Build for India AI Hackathon — Bengaluru Edition (Paytm with devSemble).
- **Track:** Track 3 — Autonomous AI Teammates. Brief: an AI teammate that handles a customer-service or sales workflow end to end — resolving issues, taking actions across systems, escalating to humans only when needed — with measurable outcomes.
- **Status:** PPT round cleared. AX-13 shortlisted for the offline round at the Paytm venue (Bengaluru).
- **Open question:** whether the venue round is an on-site build within a fixed window or a demo of a prepared prototype. This decides how much to pre-build.
- **Pitch deck:** AX-13_Pitch.pptx (10 slides: Title, Problem, Solution, How it works, Walkthrough, Tech stack, USP, Impact, Business model, Live demo).

---

## 3. The idea in one paragraph

**AX-13: Autonomous Merchant Growth & Retention Agent.** AX = Action eXecutor · Autonomous eXperience · Agent eXpress.

An AI teammate for Paytm's merchant team that decides every day which merchants need saving and which are ready to grow, resolves what it can on its own across systems, sends field agents only where a human conversation is needed (with a plan and a brief), listens to visits and calls to capture what happened, and learns from every outcome.

Pitch line: *"S1-Scout told a sales team who to call. AX-13 makes the call, fixes the problem, and only pulls in a human when it should."*

---

## 4. Problem and why now

**Problem (slide 2)**
- Payment services are ~57% of Paytm revenue: ₹1,395 Cr of ₹2,448 Cr in Q1 FY27, growing 26% YoY (source: Paytm Q1 FY27 results chart). Merchants are the revenue engine.
- Churn is seen too late: a merchant drifting to a competitor or to cash shows up only when aggregate volume drops, weeks later.
- Field teams run blind routes: visits to closed shops, busy merchants, or for the wrong issue.
- Service failures break trust: failed-but-debited payments, device vs network faults, unexplained charges.

**Why now (narrative hook only)**
- NPCI deferred the proposed 0.4% MDR on P2M UPI payments above ₹2,000 (planned from 15 Oct 2026) to after Diwali; new date to be announced. As reported: cap ₹300 per transaction; small merchants under ₹1 lakh monthly UPI exempt. Treat these as "per the proposed framework" until the final notification.
- **Decision:** MDR is the "why now" story, not a system component. There is no news feed or fee-specific score in the architecture. Any pricing or policy shock shows up through the normal Save and Grow triggers (volume drops, complaints, hesitation near thresholds). USP line: *"Event-ready by design."*
- On slides, avoid the word "MDR" except in the factual "Why now" box; elsewhere say "UPI merchant fee".

---

## 5. Solution design

### 5.1 Two service lines, one teammate
- **Save line (customer service):** at-risk merchants. Triggers: churn signals (volume decline), complaints, failed-but-debited transactions, device faults, unexplained charges.
- **Grow line (sales):** ready merchants. Triggers: growth readiness, activation stage, stated intent (often captured by VoiceCore).
- **Router rule — Save before Grow:** a merchant with an open Save case never gets a Grow offer. Resolved Save merchants become eligible for Grow.

### 5.2 The loop (use these six words everywhere)
**Scout → Score → Map → Act → Listen → Learn.** Escalate is a gate across Act in both lines, not a separate step.

| Stage | Component | What it does |
|---|---|---|
| Scout | SignalCore | Reads payments, device health, tickets/CRM, voice-derived signals |
| Score | Sentinel 2.0 | Churn score and growth score per merchant. Features computed in code; LLM writes reasoning only. Every score cites its evidence (S1-Scout's anti-fabrication rule carried over) |
| Map | AlignForce 2.0 | RAG over retention playbook (Save) or product catalogue + activation ladder (Grow); returns one action with a section citation |
| Act | ActionCore | Calls tools via MCP: ticket, technician booking, merchant message, offer, CRM update. Compliance gate on regulated actions |
| Plan (inside Act) | RoutePlanner | Shared field visit plan per field agent: Save stops first; each stop typed "retain" or "pitch" with a one-line brief |
| Listen | VoiceCore | ASR + speaker diarisation on visits and calls → intents, objections, complaints, commitments → CRM write-back and new signals |
| Learn | LearnLoop | Outcomes (saved, converted, deferred, churned) adjust scoring weights and playbook rankings; central, flows back to zones |

### 5.3 Key rules agreed
- **Field visits are shared**, not Grow-only. Technician for device-only fixes; field agent for anything needing a conversation; remote for everything the agent can resolve alone. One visit can retain and sell.
- **Waiver economics:** a fee waiver (e.g. Soundbox rental) only if expected retained revenue > waiver cost, and only within the zone's waiver budget. Within cap → agent alone; above cap → RM.
- **Activation ladder (Grow):** QR → Soundbox → steady volume history → loan eligibility. No loans pitched to merchants with thin history.
- **Threshold holders:** merchants holding back UPI near ₹1 lakh/month get an honest cost comparison (small capped fee vs lost digital sales) and the true point that recorded digital sales are the history lenders assess. Never promise loan eligibility to push them over the threshold — the lender decides eligibility under the distribution model, and the compliance gate must block such drafts.
- **Compliance gate:** rules first (APR, charges, KFS link, consent, no misleading claims — per RBI Digital Lending Directions 2025), LLM judge second. Loan offers need RM approval.
- **Recording consent** captured in the visit flow; transcripts PII-masked.

### 5.4 Autonomy policy

| Agent acts alone | Agent prepares, human approves | Human only |
|---|---|---|
| Ticket chase, settlement status check, technician booking, standard offers, CRM updates, waivers within zone cap | Loan offers, waivers above zone cap, high-value merchants | Angry or disputed cases, low-confidence decisions |

### 5.5 Scaling: zone agents
- One zone agent per city zone / pin-code cluster runs the full loop for its merchants and field team (local language, calendar, capacity, competitors).
- Central coordinator owns playbooks, catalogue, scoring models, policies (AgntID), LearnLoop.
- Shared services (VoiceCore, compliance gate) called by zones over A2A.
- Risks handled: fragmented learning (outcomes pooled centrally), policy drift (autonomy and compliance rules central only).
- Hackathon: 2 zones on mock data.

---

## 6. Architecture and tech stack

| Layer | Choice |
|---|---|
| Orchestration | LangGraph (cycles, conditional routing, interrupts for human approval, Postgres checkpointer for per-merchant state) + FastAPI |
| Tools | MCP servers (Python MCP SDK / FastMCP) wrapping mock Paytm systems: merchant data, ticketing, technician booking, CRM, offers. Connected via `langchain-mcp-adapters` |
| Agent-to-agent | A2A, optional: VoiceCore / compliance as separate agents. Cut first if short on time |
| LLMs | LiteLLM (library, no proxy). Roles set in `.env`: `AX13_MODEL_REASON` (OpenAI, reasoning/drafting/judge), `AX13_MODEL_EXTRACT` (Groq, extraction), `AX13_MODEL_FALLBACK`. Embeddings: `AX13_EMBED_MODEL` |
| Scoring | pandas features + weighted model in code; LLM writes reasoning |
| Retrieval | AFRS stack: dense + BM25, RRF fusion, cross-encoder rerank (local). Qdrant: local Docker by default; Qdrant Cloud switchable via `.env` |
| Voice | WhisperX (faster-whisper + pyannote diarisation) or Indic ASR (AI4Bharat IndicConformer / Sarvam) — test on sample audio before committing |
| Privacy | Microsoft Presidio PII masking; consent flag per recording |
| Governance | AgntID runtime policy (agent identity, tool permissions, approvals). Confirm in its docs that it can gate MCP tool calls |
| Observability | OpenTelemetry (OpenLLMetry) → `grafana/otel-lgtm` container (Collector, Tempo, Prometheus, Loki, Grafana; no account). This is the demo story. LangSmith allowed in development for LangGraph debugging, `LANGSMITH_TRACING=false` for the demo — never both on during the demo |
| Evals | RAGAS (playbook answers), Promptfoo (red-team the compliance gate), custom precision/recall for scoring, extraction accuracy for VoiceCore |
| UI | Streamlit: RM approval queue, field agent day plan, merchant timeline, simulation clock |

**Why LangGraph over CrewAI:** AX-13 is a loop with branching, human-approval pauses and long-lived per-merchant state. CrewAI (used in S1-Scout) suits linear role pipelines; LangGraph models cycles, interrupts and checkpoints natively. Anand already knows it from AFRS.

---

## 7. Scope

**Must have (demo)**
1. Synthetic dataset with archetypes, hidden labels, three scripted demo merchants, simulation clock (see DATA_PREPARATION.md).
2. Mock MCP servers over Postgres.
3. LangGraph loop: score → router → Save/Grow → map → act, with escalation gate.
4. Scoring in code with cited evidence.
5. Playbook/catalogue RAG with section citations.
6. Compliance gate + human-approval interrupt.
7. VoiceCore on 2–3 visit/call transcripts (text first; recorded audio + ASR deferred, see DATA_PREPARATION.md §12), writing back to CRM.
8. Streamlit screens + simulation clock.
9. OTel traces into Grafana; one eval table (scoring precision/recall, gate catch rate, extraction accuracy).

**Nice to have:** A2A for VoiceCore; live AgntID enforcement; second language for ASR; waiver budget dashboard per zone.

**Out of scope:** real Paytm integrations; real merchant data; security products (POS malware etc.); news/sentiment feed as a component; reinforcement learning (we do outcome-based weight updates — call it that).

---

## 8. Phases

| Phase | Output | Done when |
|---|---|---|
| 0. Data | Generator, configs, Postgres load, docs, voice ground-truth transcripts (audio deferred) | Validation report passes; demo merchants behave as scripted |
| 1. Tools | Mock MCP servers (read + write) respecting the simulation date | Agent can fetch merchant view and perform each action via MCP |
| 2. Score + Map | Scoring with evidence; RAG with citations | Precision/recall vs hidden labels reported |
| 3. Act + gates | Router, actions, escalation gate, compliance gate, HITL interrupt | Bad loan draft blocked; RM approval pauses and resumes graph |
| 4. Listen | VoiceCore pipeline + CRM write-back + new signals | Planted lines extracted from all clips |
| 5. UI | Streamlit screens + clock | Full demo playable start to end |
| 6. Observe + eval | OTel → Grafana; eval table | Dashboard and scores shown live |
| 7. Rehearse | Demo script, Q&A prep, fallback recording | Two clean dry runs |

---

## 9. Demo storyline

**"Three merchants, one fee change"** (avoid "MDR week" as a title).
- **Small vendor** (No action): below the proposed threshold → one "you're not affected" message, nothing else.
- **Ravi, kirana** (Save line): hit by the fee, volume drops → actual monthly cost estimate, waiver only if the save pays off, field visit; VoiceCore hears loan interest → handed to Grow.
- **Meena** (Grow line): holding back UPI near the threshold → honest cost comparison → stays digital; loan path opens as history builds.
- **Shown live on the system side:** compliance gate blocking an over-promising loan draft before it reaches Meena; RM approval step; Grafana traces; eval scores.

**Ravi end-to-end walkthrough (slide 5):** UPI down 40% in 3 weeks + Soundbox offline 6 days + complaint → churn 82/100, growth paused → device swap booked, WhatsApp update, no human → recovery + festive spike → field plan stop 4 → visit diarised (prepayment question, rival offer, "come after Diwali") → CRM updated, follow-up booked, rival flagged, weights updated.

**Illustrative volume view (Sankey, slide 4):** zone-day of 1,000 signals → Save 55%, Grow 30%, Watchlist 15% → agent resolves 54%, field visit 20%, RM escalation 11% → retained 44%, activated/sold 23%, follow-up 18%. Always labelled illustrative / pilot target.

---

## 10. Metrics

- **Save:** merchants retained (esp. through fee go-live), time from signal to first contact, waiver spend per merchant saved.
- **Grow:** activation ladder progression, merchants staying digital past the threshold, conversion per field visit.
- **Cost:** wasted technician/field visits avoided, complaints prevented, manual CRM entry time (target zero).
- **Teammate:** share resolved without a human, escalation accuracy.
- **Quality:** scoring precision/recall vs hidden labels, compliance gate catch rate, diarisation extraction accuracy.

Business model levers (formulas, not invented numbers): revenue protected = merchants saved × monthly revenue per merchant; field cost avoided = wasted visits avoided × cost per visit; productivity = visits/day × conversion/visit; service cost avoided = complaints prevented × handling cost.

---

## 11. Decisions and rejected ideas (don't reintroduce)

- Loan-only CreditPilot → broadened into AX-13 (loan-only was narrow and data-dependent).
- News/sentiment source box → removed; MDR is narrative only.
- "Pre-approved loan for a merchant doing 2 UPI txns/day" → wrong credit logic; use the activation ladder.
- "Eligible for ₹2 lakh loan if you cross ₹1 lakh" → misleading; lender decides; gate blocks it.
- "3x more productive field force" → unsupported; use formulas and pilot targets.
- "Reinforcement learning loop" → it's outcome-based weight updates.
- POS memory-scraping malware → out of scope. Shadow AI → at most one governance line.
- Paytm Payments Bank history → don't feature in a pitch to Paytm.
- Single-source anecdotes (e.g. one LinkedIn post on maintenance charges) → use as a problem category, not evidence.

---

## 12. Lineage (reuse, don't reinvent)

- **S1-Scout:** universe → evidence-gated profiling → weighted scoring (Sentinel) → RAG mapping to an offering (AlignForce) → fact-check eval. AX-13 adds action, escalation, voice and learning; fixes "GPT judgment, no formula" scoring and fixed weights.
- **AFRS:** LangGraph patterns, hybrid retrieval, Docling, RAGAS/Promptfoo, rate limiting, multi-model arbitration.
- **AgntID hackathon work:** runtime policy enforcement (restricted actions, approvals).
- **Financial Sentiment Analyzer:** can score sentiment on VoiceCore transcripts if useful (merchant-level, not news).

---

## 13. Judge Q&A prep

- **Real data source?** Paytm already has transactions, device telemetry, tickets, settlements; AX-13 reads them through MCP — mocks swap for real servers.
- **Indic ASR accuracy?** Tested on sample clips; agent's voice note as fallback; low-confidence segments sent to the field agent to confirm.
- **Consent?** Captured at visit start; stored per recording; PII masked.
- **Why not CRM rules?** Rules can't read voice, cite evidence, pick from playbooks, or learn from outcomes; AX-13 also enforces autonomy and compliance at runtime.
- **Running cost?** LLM + voice + infra per zone; extraction on cheap fast models, reasoning only where needed.

---

## 14. Repo layout

```
ax13/
  CLAUDE.md               # this file
  DATA_PREPARATION.md     # dataset spec (Phase 0)
  README.md
  .env.example            # template; real .env is gitignored
  requirements.txt        # Phases 0–3, 5, 6
  requirements-voice.txt  # Phase 4 only (torch-heavy)
  common/                 # shared plumbing: settings.py, telemetry.py, llm.py
  data/
    config/               # sim_config, archetypes, categories, demo_merchants (YAML)
    generators/           # one module per table
    out/                  # parquet/csv outputs (gitignored except sample/)
    generator.py · validate.py · load_postgres.py
  docs/                   # playbook, catalogue, compliance rules, autonomy policy
  audio/                  # visit recordings + ground-truth transcripts
  mcp_servers/            # mock MCP servers (named to avoid shadowing the `mcp` SDK)
  agent/
    graph.py              # LangGraph graph
    nodes/                # score, router, map, act, gates, listen, learn
    scoring/              # features + weighted model in code
    prompts/
  app/                    # Streamlit screens
  eval/                   # precision/recall, RAGAS, gate tests, extraction tests
    fixtures/             # e.g. the over-promising loan draft
  infra/                  # docker-compose: Postgres, Qdrant, Grafana LGTM
  scripts/                # check_connections.py and other one-off utilities
```

Every folder with Python code is a package (`__init__.py`), so modules run as `python -m data.generator`, `python -m eval.scoring`, etc. from the repo root.

## 15. Environment and setup

| Service | Runs | Account |
|---|---|---|
| OpenAI, Groq | API | Keys in `.env` |
| Postgres 16, Qdrant, Grafana LGTM | Local Docker (`infra/docker-compose.yml`) | None |
| Qdrant Cloud | Optional | Key in `.env` if switched |
| LangSmith | Dev only | Key in `.env` |
| Hugging Face | Phase 4 (pyannote) | Token; accept model terms |

- Start infra (from repo root): `docker compose --env-file .env -f infra/docker-compose.yml up -d`
- Verify: `python scripts/check_connections.py --llm-call` must pass before any phase work.
- Ports: Postgres 5432, Qdrant 6333 (UI at /dashboard), Grafana 3000, OTLP 4318 (HTTP; app uses this). 4317 gRPC exposed but unused.
- Windows: Smart App Control / application-control policies can block compiled DLLs (seen with grpcio). Prefer HTTP clients; if blocked, disable Smart App Control or run Python inside WSL2.
- Demo laptop installs from a frozen `requirements.lock`.
- Open item: confirm in AgntID docs whether it needs an account/key and can gate MCP tool calls.

**Current phase: 0 — Data.** See DATA_PREPARATION.md.