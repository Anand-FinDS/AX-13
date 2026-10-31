"""AX-13 LangGraph: one thread per merchant, run once per simulation day.

    score -> route -> map -> escalation_gate -+-> escalate (human only) ------------------------+-> finish
                                              +-> draft <-> compliance -> act -+----------------+
                                                                               +-> await_approval (interrupt)
                                                                                   -> execute_approved -> finish

- score: Sentinel 2.0 (agent/scoring) through MCP.
- route: Save before Grow (the scorer gives one line); a case already handled on this thread is
  skipped; at fee go-live, merchants the fee does not touch get one not-affected notice.
- map: retrieve the cited section, plan actions in code (agent/nodes/plan.py).
- escalation_gate: Autonomy policy §2, §4. Human-only cases are queued with no action.
- draft / compliance: the LLM writes; the gate checks fee messages, cost comparisons and loan drafts
  (rules, then judge); one redraft on a block, then a human (Compliance rules §7.3).
- act: agent-alone actions run now; the rest go to request_approval and the graph pauses until an RM
  decides in app/admin.py. The decision is read back through MCP (get_approval) before executing.
"""
from __future__ import annotations

import asyncio
import copy
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from agent.nodes import draft as drafting
from agent.nodes.gates import confidence, escalation, gate
from agent.nodes.plan import LANG, plan_case
from agent.scoring import score_merchant
from agent.scoring.scorer import TH
from agent.tools import ToolRejected, call

GATE_REF = {"fee_message": "Compliance rules §6", "cost_comparison": "Compliance rules §3.2",
            "loan_offer": "Compliance rules §1"}


class AgentState(TypedDict, total=False):
    merchant_id: str
    inject_fixture: bool
    sim_date: str
    fee_go_live_date: str
    score: dict
    profile: dict
    view: dict
    cases: list[dict]
    sections: list[dict]
    actions: list[dict]
    escalation: dict
    approvals: list[dict]
    decisions: dict
    log: list[str]
    handled_cases: list[str]
    status: str


def build_graph(tools, checkpointer=None):
    async def run_tool(a: dict, mid: str, **extra) -> None:
        args = {"merchant_id": mid, **a["args"], "evidence": a["evidence"], "playbook_ref": a["playbook_ref"],
                "line": a["line"], **extra}
        try:
            a["result"] = await call(tools, a["tool"], **args)
            a["status"] = "done"
        except ToolRejected as e:
            a["result"], a["status"] = {"error": str(e)}, "rejected_by_tool"

    # ------------------------------------------------------------ nodes

    async def score(st: AgentState) -> dict:
        mid = st["merchant_id"]
        sim = await call(tools, "get_sim_state")
        s = await score_merchant(tools, mid, sim)
        view = await call(tools, "get_merchant_view", merchant_id=mid, days=1)
        conf = confidence(s, s["line"], TH) if s["line"] in TH else None
        return {"sim_date": sim["sim_date"], "fee_go_live_date": sim["fee_go_live_date"], "score": s,
                "profile": view["profile"],
                "view": {k: view[k] for k in ("open_tickets", "deductions_30d", "fee", "devices")},
                "cases": [], "sections": [], "actions": [], "escalation": {}, "approvals": [], "decisions": {},
                "handled_cases": st.get("handled_cases") or [], "status": "scored",
                "log": [f"score: churn {s['churn']} · growth {s['growth']} -> {s['line']}/{s['cause']}"
                        + (f" (confidence {conf:.2f})" if conf is not None else "")]}

    async def route(st: AgentState) -> dict:
        s, handled = st["score"], set(st["handled_cases"])
        lm = st["view"]["fee"].get("last_full_month") or {}
        not_affected = bool(lm) and not lm["is_fee_affected"]
        cases = []
        if s["line"] in ("save", "grow"):
            key = f"{s['line']}:{s['cause']}"
            case = {"key": key, "line": s["line"], "cause": s["cause"], "confidence": confidence(s, s["line"], TH),
                    "also": []}
            if (s["cause"] == "fee" and not_affected) or s["cause"] == "threshold":
                # fee/not affected: the not-affected message goes with this case, once; threshold: the cost
                # comparison already tells the merchant where they stand, so no separate notice
                case["also"] = ["notice:fee"]
            if key not in handled:
                cases.append(case)
        fee_live = st["sim_date"] >= st["fee_go_live_date"]
        if fee_live and not_affected and "notice:fee" not in handled and not any(c["also"] for c in cases):
            cases.append({"key": "notice:fee", "line": "none", "cause": "fee_notice", "confidence": None, "also": []})
        if cases:
            status, msg = "case", "route: " + ", ".join(c["key"] for c in cases)
        elif s["line"] in ("save", "grow"):
            status, msg = "already_handled", f"route: {s['line']}:{s['cause']} already handled, skipped"
        else:
            status, msg = "no_case", "route: no case"
        return {"cases": cases, "status": status, "log": st["log"] + [msg]}

    async def map_(st: AgentState) -> dict:
        sections, actions = [], []
        for case in st["cases"]:
            sec, acts = await plan_case(tools, st, case)
            sections.append({"case": case["key"], "ref": sec["ref"], "safety_net": sec.get("safety_net", False)})
            actions += acts
        for i, a in enumerate(actions, 1):
            a["id"] = f"a{i}"
            if "draft_spec" not in a:
                a["status"] = "ready"
        return {"sections": sections, "actions": actions,
                "log": st["log"] + [f"map: {s['case']} -> {s['ref']}" + (" (safety net)" if s["safety_net"] else "")
                                    for s in sections]
                + [f"plan: {a['id']} {a['kind']} via {a['tool']} [{a['tier']}]" for a in actions]}

    async def escalation_gate(st: AgentState) -> dict:
        esc = escalation(st)
        actions = copy.deepcopy(st["actions"])
        log = []
        if esc["human_only"]:
            for a in actions:
                a["tier"], a["status"] = "human_only", "held"
            log.append("escalation gate: human only -> " + "; ".join(esc["human_only"]))
        elif esc["human_approves"]:
            for a in actions:
                if a["tier"] == "agent_alone":
                    a.update(tier="human_approves", approval_type="high_value_action", tier_reason=esc["human_approves"][0])
            log.append("escalation gate: " + "; ".join(esc["human_approves"]))
        else:
            log.append("escalation gate: agent may act" + ("" if all(a["tier"] == "agent_alone" for a in actions)
                                                          else " (some actions need approval)"))
        return {"escalation": esc, "actions": actions, "log": st["log"] + log}

    async def escalate(st: AgentState) -> dict:
        esc, s = st["escalation"], st["score"]
        line = next((c["line"] for c in st["cases"]), "none")
        ref = "Autonomy policy §2" if all("§2.1" in r for r in esc["human_only"]) else "Autonomy policy §4"
        summary = (f"Cases {', '.join(c['key'] for c in st['cases'])}. Prepared, not sent: "
                   + ", ".join(f"{a['kind']} ({a['playbook_ref']})" for a in st["actions"]))
        res = await call(tools, "escalate_to_human", merchant_id=st["merchant_id"], reasons=esc["human_only"],
                         case_summary=summary, evidence=s["evidence"][:6] or esc["human_only"], playbook_ref=ref, line=line)
        return {"status": "escalated", "log": st["log"] + [f"escalate: queued for a human ({res['action_id']})"]}

    async def draft(st: AgentState) -> dict:
        actions = copy.deepcopy(st["actions"])
        language = LANG.get(st["profile"]["language_pref"], "English")
        todo = [a for a in actions if a.get("draft_spec") and a["status"] in ("planned", "redraft")]

        async def one(a):
            fx = drafting.fixture_for(st["merchant_id"], a["kind"]) if st.get("inject_fixture") else None
            if fx and not a.get("drafts"):
                d = {"text": fx["text"], "english": fx["english"], "source": f"eval fixture {fx['id']} (injected for the demo)"}
            else:
                d = await asyncio.to_thread(drafting.write, a["draft_spec"], language, a.get("feedback"))
            a.setdefault("drafts", []).append(d)
            a["draft"], a["status"] = d, ("drafted" if a.get("gate") else "ready")
        await asyncio.gather(*(one(a) for a in todo))
        return {"actions": actions,
                "log": st["log"] + [f"draft: {a['id']} {a['kind']} #{len(a['drafts'])} ({a['draft']['source']})" for a in todo]}

    async def compliance(st: AgentState) -> dict:
        actions = copy.deepcopy(st["actions"])
        log = []
        for a in [a for a in actions if a["status"] == "drafted"]:
            facts = a["draft_spec"]["facts"]
            g = await asyncio.to_thread(gate, a["gate"], a["draft"], facts)
            a.setdefault("gate_results", []).append(g)
            ref = g["refs"][0] if g["refs"] else GATE_REF[a["gate"]]
            res = await call(tools, "log_gate_result", merchant_id=st["merchant_id"], draft_kind=a["gate"],
                             draft={**a["draft"], "attempt": len(a["drafts"])}, result=g["result"],
                             rule_hits=g["rule_hits"], judge=g["judge"],
                             evidence=a["evidence"], playbook_ref=ref, line=a["line"])
            why = "; ".join(f"{h['rule']} {h['check']}" for h in g["rule_hits"]) or \
                (f"judge: {g['judge']['reason']}" if g["judge"] else "")
            log.append(f"compliance gate: {a['id']} {a['kind']} draft #{len(a['drafts'])} -> {g['result'].upper()}"
                       + (f" ({why})" if g["result"] != "pass" else " (rules ok, judge pass)") + f" [{res['action_id']}]")
            fixable = not a["draft"]["source"].startswith("template (")
            if g["result"] == "pass":
                a["status"] = "ready"
            elif g["result"] == "block" and len(a["drafts"]) < 2 and fixable:
                a["status"] = "redraft"
                a["feedback"] = {"english": a["draft"]["english"],
                                 "reasons": [f"{h['rule']}: {h['check']} ({h['detail']})" for h in g["rule_hits"]]
                                 + ([f"{g['judge']['rule']}: {g['judge']['reason']}"] if g["judge"] else [])}
            else:
                a["status"] = "gate_blocked" if g["result"] == "block" else "gate_unsure"
        return {"actions": actions, "log": st["log"] + log}

    async def act(st: AgentState) -> dict:
        mid, actions = st["merchant_id"], copy.deepcopy(st["actions"])
        log, approvals = [], []
        for a in actions:
            if a.get("draft"):
                a["args"]["text"] = a["draft"]["text"]
                a["args"]["english"] = a["draft"]["english"]
        for a in actions:
            if a["status"] == "ready" and a["tier"] == "agent_alone":
                await run_tool(a, mid)
                log.append(f"act: {a['tool']} -> {a['status']}" + (f" ({a['result'].get('error')})"
                                                                    if a["status"] != "done" else ""))
            elif a["status"] in ("gate_blocked", "gate_unsure"):
                reasons = [(f"Compliance gate {a['status'].split('_')[1]} on {a['kind']} after {len(a['drafts'])} drafts "
                           f"(Compliance rules §7.3)")] + a.get("feedback", {}).get("reasons", [])
                res = await call(tools, "escalate_to_human", merchant_id=mid, reasons=reasons,
                                 case_summary="Drafts: " + " || ".join(d["english"] for d in a["drafts"]),
                                 evidence=a["evidence"], playbook_ref="Compliance rules §7", line=a["line"])
                a["status"] = "escalated"
                log.append(f"act: {a['kind']} escalated to a human ({res['action_id']})")
        groups: dict[str, list[dict]] = {}
        for a in actions:
            if a["status"] == "ready" and a["tier"] == "human_approves":
                groups.setdefault(a["approval_type"], []).append(a)
        for typ, acts in groups.items():
            draft = {"actions": [{"id": a["id"], "kind": a["kind"], "tool": a["tool"], "args": a["args"],
                                  "english": (a.get("draft") or {}).get("english"),
                                  "gate": (a.get("gate_results") or [None])[-1]} for a in acts],
                     "why_approval": acts[0].get("tier_reason")}
            if typ == "waiver":
                draft["amount"] = sum(a["args"]["amount"] for a in acts if a["tool"] == "grant_waiver")
            if typ == "loan_offer":
                draft["quote"] = acts[0].get("quote")
                draft["gate_history"] = [{"source": d["source"], "english": d["english"], "result": g["result"],
                                          "rules": [h["rule"] for h in g["rule_hits"]]}
                                         for d, g in zip(acts[0]["drafts"], acts[0]["gate_results"])]
            res = await call(tools, "request_approval", merchant_id=mid, action_type=typ, draft=draft,
                             evidence=acts[0]["evidence"], playbook_ref=acts[0]["playbook_ref"], line=acts[0]["line"])
            for a in acts:
                a["status"], a["approval_id"] = "pending_approval", res["approval_id"]
            approvals.append({"approval_id": res["approval_id"], "type": typ, "actions": [a["id"] for a in acts]})
            log.append(f"act: request_approval {typ} -> {res['approval_id']} (graph pauses for the RM)")
        return {"actions": actions, "approvals": approvals, "log": st["log"] + log}

    async def await_approval(st: AgentState) -> dict:
        decisions = interrupt({"merchant_id": st["merchant_id"],
                               "approval_ids": [a["approval_id"] for a in st["approvals"]]})
        return {"decisions": decisions}

    async def execute_approved(st: AgentState) -> dict:
        mid, actions = st["merchant_id"], copy.deepcopy(st["actions"])
        by_id = {a["id"]: a for a in actions}
        log = []
        for ap in st["approvals"]:
            got = await call(tools, "get_approval", approval_id=ap["approval_id"])   # read back through MCP
            log.append(f"approval {ap['approval_id']}: {got['decision']} by {got.get('decided_by')}")
            for aid in ap["actions"]:
                a = by_id[aid]
                if got["decision"] != "approved":
                    a["status"] = got["decision"] if got["decision"] == "rejected" else "pending_approval"
                    continue
                extra = {"approval_id": ap["approval_id"]} if ap["type"] in ("loan_offer", "waiver") else {}
                await run_tool(a, mid, **extra)
                log.append(f"act: {a['tool']}" + (f" ({a['args'].get('product')})" if a["tool"] == "make_offer" else "")
                           + f" -> {a['status']}" + (f" ({a['result'].get('error')})" if a["status"] != "done" else ""))
        return {"actions": actions, "log": st["log"] + log}

    async def finish(st: AgentState) -> dict:
        if not st.get("cases"):
            return {}
        keys = [c["key"] for c in st["cases"]] + [k for c in st["cases"] for k in c.get("also", [])]
        handled = list(dict.fromkeys(st["handled_cases"] + keys))
        status = st["status"] if st["status"] == "escalated" else "done"
        return {"handled_cases": handled, "status": status}

    # ------------------------------------------------------------ wiring

    g = StateGraph(AgentState)
    for name, fn in (("score", score), ("route", route), ("map", map_), ("escalation_gate", escalation_gate),
                     ("escalate", escalate), ("draft", draft), ("compliance", compliance), ("act", act),
                     ("await_approval", await_approval), ("execute_approved", execute_approved), ("finish", finish)):
        g.add_node(name, fn)
    g.add_edge(START, "score")
    g.add_edge("score", "route")
    g.add_conditional_edges("route", lambda st: "map" if st["cases"] else "finish", ["map", "finish"])
    g.add_edge("map", "escalation_gate")
    g.add_conditional_edges("escalation_gate", lambda st: "escalate" if st["escalation"]["human_only"] else "draft",
                            ["escalate", "draft"])
    g.add_edge("escalate", "finish")
    g.add_edge("draft", "compliance")
    g.add_conditional_edges("compliance", lambda st: "draft" if any(a["status"] == "redraft" for a in st["actions"])
                            else "act", ["draft", "act"])
    g.add_conditional_edges("act", lambda st: "await_approval" if st["approvals"] else "finish",
                            ["await_approval", "finish"])
    g.add_edge("await_approval", "execute_approved")
    g.add_edge("execute_approved", "finish")
    g.add_edge("finish", END)
    return g.compile(checkpointer=checkpointer)


def summary(values: dict[str, Any]) -> dict:
    return {"merchant_id": values.get("merchant_id"), "sim_date": values.get("sim_date"), "status": values.get("status"),
            "cases": [c["key"] for c in values.get("cases") or []],
            "actions": [{"id": a["id"], "kind": a["kind"], "tool": a["tool"], "tier": a["tier"], "status": a["status"]}
                        for a in values.get("actions") or []],
            "log": values.get("log") or []}
