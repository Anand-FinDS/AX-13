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

## Stack

LangGraph · MCP (mock Paytm systems) · LiteLLM · hybrid RAG · WhisperX / Indic ASR · AgntID governance · OpenTelemetry → Grafana · RAGAS / Promptfoo · Streamlit

## Phases

0. Synthetic dataset (current)
1. Mock MCP tools
2. Scoring and RAG
3. Actions and gates
4. VoiceCore
5. UI
6. Observability and evals
7. Rehearsal

## Setup

```bash
cp .env.example .env            # add API keys
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
docker compose --env-file .env -f infra/docker-compose.yml up -d
python scripts/check_connections.py --llm-call
```

## Files

- `CLAUDE.md`: full project context, decisions and scope
- `DATA_PREPARATION.md`: Phase 0 dataset spec

All data is synthetic. Illustrative numbers are labelled as such.