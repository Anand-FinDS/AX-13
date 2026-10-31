# AX-13: Autonomous Merchant Growth & Retention Agent

**AX** = Action eXecutor · Autonomous eXperience · Agent eXpress
Paytm Build for India AI Hackathon, Bengaluru · Track 3: Autonomous AI Teammates · Shortlisted for the offline round

## What we are building

An AI teammate for Paytm's merchant team. Every day it decides which merchants need saving and which are ready to grow, fixes what it can on its own across systems, sends field agents only where a human conversation is needed, listens to visits and calls, and learns from every outcome.

## Why it matters

Payment services are ~57% of Paytm's revenue (₹1,395 Cr of ₹2,448 Cr in Q1 FY27, +26% YoY). Today, merchant churn is spotted late, field teams run blind routes, and service failures break trust. The proposed UPI merchant fee, deferred to after Diwali, will test merchant retention on a known date.

## How it works

- **Two service lines:** Save (customer service) and Grow (sales), with one rule: Save before Grow.
- **One loop:** Scout → Score → Map → Act → Listen → Learn.
- **Humans only when needed:** the agent acts alone on routine fixes. Loan offers and large waivers need RM approval. Angry or unclear cases go to a human.
- **Shared field visit plan:** one visit can retain and sell.
- **VoiceCore:** diarises visits and calls and turns them into CRM updates and new signals.
- **Built to scale by zone:** central playbooks, policies and learning.

**[AGENT_FLOW.md](AGENT_FLOW.md)** explains the built system on one page: the overall flow, the LangGraph loop, every LLM call, and where the agent hands work to people.

## What runs today

- **Data:** synthetic merchants (200, three of them scripted for the demo), 120 days of history and 30 future days, with a simulation clock.
- **MCP:** 6 mock Paytm servers with 32 tools over Postgres. They are the only way the agent touches merchant data.
- **Scoring:** Sentinel 2.0 computes churn and growth in code, and every score cites its evidence.
- **Retrieval:** AlignForce 2.0 retrieves policy sections with citations.
- **LangGraph loop:** an escalation gate (rules), a compliance gate (rules + LLM judge), and RM approval that pauses the graph until a decision.
- **Streamlit control room:** the simulation clock, the RM approval queue, the human queue, the field plan, merchant timelines, the actions log and saved days.
- **Cache-first LLM use:** the LLM cache is included in this repo, so the whole demo replays with **no API calls**.

## Stack

LangGraph · MCP (mock Paytm systems) · LiteLLM · hybrid RAG · WhisperX / Indic ASR · AgntID governance · OpenTelemetry → Grafana · RAGAS / Promptfoo · Streamlit

## Phases

| Phase | Status |
|---|---|
| 0. Synthetic dataset | Done |
| 1. Mock MCP tools | Done |
| 2. Scoring and RAG | Done |
| 3. Actions and gates | Done |
| 4. VoiceCore | Next (ground-truth transcripts ready; audio deferred) |
| 5. UI | Done (Streamlit) |
| 6. Observability and evals | Partly done (scoring, retrieval and gate evals; OpenLLMetry traces) |
| 7. Rehearsal | — |

## Setup

```bash
cp .env.example .env            # add API keys; AX13_LLM_MODE=replay keeps API spend at zero
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
docker compose --env-file .env -f infra/docker-compose.yml up -d    # Postgres, Qdrant, Grafana
python scripts/check_connections.py

python -m data.generator          # synthetic data (seed 13, deterministic)
python -m data.validate           # 31 checks
python -m data.load_postgres      # tables, views, restricted MCP login
python -m agent.retrieval --build # Qdrant index from the cached section embeddings
```

## Run

```bash
streamlit run app/streamlit_app.py      # control room (opens in Replay mode)
python -m app.demo_script               # all 200 merchants, D0 → G0+5, saves each day (Replay: 0 API calls)
python -m agent.run_day M_DEMO_RAVI     # one merchant, today; --estimate for a dry run, --live to allow API calls
pytest -q                               # tests (reset the database)
python -m eval.scoring_eval             # scoring vs hidden labels
```

In the app, the **Overview** tab has a click-by-click demo guide.

## Files

- `AGENT_FLOW.md`: how the agent works, on one page
- `CLAUDE.md`: full project context, decisions and scope
- `DATA_PREPARATION.md`: dataset spec
- `docs/`: the four illustrative policy documents the agent cites

All data is synthetic. Illustrative numbers are labelled as such. Policy documents are illustrative, not Paytm policy.
