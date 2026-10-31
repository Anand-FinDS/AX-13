# AX-13: How the Agent Works

A short guide to the overall flow, the LangGraph loop, where the LLM is called, and where the agent hands work to people instead of acting.
All data is synthetic. Numbers are from one Live run of all 200 merchants, from D0 to G0+5 (31 Oct to 18 Nov 2026).

## 1. Overall flow

```
Config YAML ──► Generator (seed 13) ──► Validator (31 checks) ──► Postgres: ax13 + ax13_eval (hidden labels)
Policy docs ×4 ──► Doc index (62 sections) ──► Qdrant
                                │
                     6 MCP servers · 32 tools  (restricted login; views cut at the simulation date)
                                │
              Sentinel 2.0 scoring  +  AlignForce 2.0 retrieval
                                │
                 LangGraph agent (one thread per merchant)  ◄──► LiteLLM (cache-first)
                                │
   Messages · technicians · visits · offers · RM queue · human queue · audit log
                                ▲
             Streamlit + app/admin.py (RM decisions, clock, saved days)
```

- **Data:** the generator builds 120 days of history and 30 future days for 200 merchants. The agent sees nothing after the simulation date.
- **MCP boundary:** the agent reaches merchant data only through the 6 MCP servers.

  | Server | Tools |
  |---|---|
  | merchant_data | 10 |
  | ticketing | 4 |
  | field_ops | 4 |
  | crm | 7 |
  | offers_messaging | 6 |
  | sim_clock | 1 |

  The MCP login cannot read the hidden labels, decide approvals or move the clock. Every write must cite evidence and a real policy section, or it is rejected. Swap the mock servers for real ones and the agent does not change.
- **Human-only actions:** approving or rejecting, moving the clock and resetting live in `app/admin.py` and are never exposed to the agent.
- **Hidden labels:** only `eval/scoring_eval.py` reads them, to measure precision and recall.

## 2. LangGraph flow

One graph run per merchant per simulated day. State is checkpointed in Postgres (`ax13_graph`), so a paused run resumes later and handled cases are not repeated.

```
score → route → map → escalation gate ─┬─► escalate (human only) ────────────────► finish
                                       └─► draft ⇄ compliance gate → act ─┬───────► finish
                                                                          └─► await approval → execute approved → finish
```

| Step | What it does | LLM |
|---|---|---|
| score | Churn and growth scores, the line (Save / Grow / none), the cause and evidence, all in code | No |
| route | Save before Grow; skips cases already handled; at fee go-live, one fee message per merchant | No |
| map | Picks the policy section to cite and plans the actions in code (tools + the merchant's own numbers) | Embedding, demo merchants only |
| escalation gate | Fixed rules (Autonomy policy §2, §4): angry or disputed case, or low confidence → human; high-value merchant → RM approval | No |
| draft | Writes the message in the merchant's language, with an English version | Yes, demo merchants only |
| compliance gate | Rules in code, then the LLM judge on drafts that passed | Judge, see below |
| act | Runs agent-alone actions through MCP; sends the rest to `request_approval` | No |
| await approval | The graph pauses until an RM decides in the app | No |
| execute approved | Reads the decision back through MCP, then acts or records the rejection | No |
| finish | Marks the cases handled | No |

## 3. LLM calls

| Where | Model | Who it runs for |
|---|---|---|
| draft (write or translate a message) | `AX13_MODEL_REASON` (gpt-4o) | The 3 demo merchants. The other 197 get code-filled templates in English, no LLM. An RM can request an LLM draft from the app. |
| compliance judge on loan offers | `AX13_MODEL_REASON` (gpt-4o) | Every loan draft |
| compliance judge on other messages | `AX13_MODEL_FALLBACK` (gpt-4o-mini) | Demo merchants only; others get rules only |
| retrieval embedding | `AX13_EMBED_MODEL` | Demo merchants only; others use the section mapped to their cause |

- **Never an LLM:** scoring, routing, the escalation gate and the action plan are code and rules.
- **Cache-first:**
  - Every LLM and embedding call is cached on disk by model + prompt.
  - **Replay** (default) never calls the API. A cache miss runs as a template, marked "not cached".
  - **Live** calls the API only on a miss.
  - The full four-day Live run of 200 merchants made **9 API calls**. Its Replay made **0**, with identical results.

## 4. Decisions: act or hand over

| Point | Who decides | Outcome in the run |
|---|---|---|
| Escalation gate | Rules | **23** cases to a human, no action: 10 disputed charges, 9 confidence < 0.6, 4 angry tickets |
| Escalation gate (high-value merchant) | Rules | **35** action bundles prepared for RM approval |
| Compliance gate | Rules, then LLM judge | **180** checks. **1** blocked: Meena's injected over-promising loan fixture, which was then redrafted and passed. |
| Loan offer, waiver above cap | Rules (Compliance §5, Autonomy §3) | **5** loans and **2** waivers waiting for the RM; **1** loan approved and sent (Meena, scripted RM) |
| Everything else | Agent alone | **168** messages, **36** offers, **6** technicians, **53** visits |

The compliance judge is the only point where an LLM's verdict can stop the agent. A blocked draft is redrafted once; a second block or an "unsure" verdict goes to a human (Compliance rules §7.3).
