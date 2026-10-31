"""AX-13 control room: simulation clock, run the agent, RM approvals, human queue, field plan,
merchant timeline and the actions log.

    streamlit run app/streamlit_app.py

Human-only actions (approval decisions, the clock, closing escalations) go through app/admin.py.
The agent itself runs through agent.run_day and reaches data only through the MCP servers.
"""
from __future__ import annotations

import asyncio
import json
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import altair as alt
import pandas as pd
import streamlit as st

from agent.run_day import run_day
from app import admin

st.set_page_config(page_title="AX-13", page_icon="🛰️", layout="wide")
DEMO = {"M_DEMO_RAVI": "Ravi · kirana (Save)", "M_DEMO_MEENA": "Meena · tailor (Grow)",
        "M_DEMO_SMALL": "Lakshmi · fruit stall (no action)"}
TYPE_LABEL = {"technician_booking": "Technician booked", "message": "Message sent", "visit_planned": "Visit planned",
              "offer": "Offer made", "loan_offer": "Loan for RM approval", "waiver": "Waiver / refund",
              "high_value_action": "High-value action for approval", "compliance_check": "Compliance gate",
              "escalation": "Escalated to a human", "crm_update": "CRM note", "ticket_update": "Ticket update",
              "visit_outcome": "Visit outcome"}


# ---------------------------------------------------------------- helpers

def run_agent(ids: list[str] | None = None, resume_only: bool = False) -> None:
    label = "Resuming after the RM decision" if resume_only else \
        f"Running the agent for {'the demo merchants' if ids else 'all merchants'}"
    with st.spinner(f"{label}… (first runs call the LLM; repeats come from the cache)"):
        res = asyncio.run(run_day(ids, inject_fixture=True, resume_only=resume_only))
    st.session_state["last_run"] = {"sim_date": str(admin.get_sim_state()["sim_date"]), "results": res}


def day_label(d: date) -> str:
    n = (d - admin.D0).days
    g = (d - admin.G0).days
    return f"D{n:+d}".replace("+0", "0") + f" · G{g:+d}".replace("+0", "0")


def payload(a: dict) -> dict:
    p = a["payload_json"]
    return p if isinstance(p, dict) else json.loads(p or "{}")


def gate_badge(result: str) -> str:
    return {"pass": "🟢 PASS", "block": "🔴 BLOCK", "unsure": "🟡 UNSURE"}.get(result, result)


# ---------------------------------------------------------------- sidebar: simulation clock

state = admin.get_sim_state()
today = state["sim_date"]
with st.sidebar:
    st.title("AX-13")
    st.caption("Autonomous Merchant Growth & Retention Agent · synthetic data")
    st.metric("Simulation date", today.strftime("%a %d %b %Y"), day_label(today), delta_color="off")
    st.write("Fee " + ("**live**" if state["fee_live"] else f"goes live {admin.G0:%d %b} (G0)"))

    st.subheader("Agent")
    c1, c2 = st.columns(2)
    if c1.button("Run: demo 3", width="stretch", help="Ravi, Meena and the small vendor"):
        run_agent(list(DEMO))
        st.rerun()
    if c2.button("Run: all 200", width="stretch"):
        run_agent()
        st.rerun()

    st.subheader("Clock")
    c1, c2 = st.columns(2)
    if c1.button("Advance 1 day", width="stretch", disabled=today >= admin.LAST_DAY):
        admin.advance_day(1)
        st.rerun()
    if c2.button("Jump to G0", width="stretch", disabled=today >= admin.G0):
        admin.jump_to_go_live()
        st.rerun()
    with st.expander("Reset to D0"):
        st.caption("Reloads all data, clears agent actions and LangGraph threads.")
        if st.button("Reset now", type="secondary", width="stretch"):
            with st.spinner("Reloading…"):
                admin.reset()
            st.session_state.pop("last_run", None)
            st.rerun()

    if run := st.session_state.get("last_run"):
        st.subheader("Last run")
        res = run["results"]
        counts = pd.Series([r["status"] for r in res]).value_counts()
        st.caption(f"{run['sim_date']} · {len(res)} merchants")
        st.dataframe(counts.rename("merchants"), width="stretch")
        errors = [r for r in res if r["status"] == "error"]
        for r in errors:
            st.error(f"{r['merchant_id']}: {r.get('error')}")

pending = admin.list_pending_approvals()
queue = admin.human_queue()
tabs = st.tabs(["Overview", f"RM approvals ({len(pending)})", f"Human queue ({len(queue)})", "Field plan",
                "Merchant timeline", "Actions log"])

# ---------------------------------------------------------------- overview

with tabs[0]:
    counts = pd.DataFrame(admin.day_counts())
    todays = counts[counts["day"] == today] if not counts.empty else counts
    m = st.columns(5)
    acted = todays[(todays["autonomy_tier"] == "agent_alone") & ~todays["action_type"].isin(["compliance_check"])] \
        if not todays.empty else todays
    m[0].metric("Agent acted alone today", int(acted["n"].sum()) if not acted.empty else 0)
    m[1].metric("Waiting for RM", len(pending))
    m[2].metric("With a human", len(queue))
    gate = todays[todays["action_type"] == "compliance_check"] if not todays.empty else todays
    m[3].metric("Gate checks today", int(gate["n"].sum()) if not gate.empty else 0)
    blocked = gate[gate["status"] == "block"] if not gate.empty else gate
    m[4].metric("Drafts blocked today", int(blocked["n"].sum()) if not blocked.empty else 0)

    st.subheader("Demo merchants")
    cols = st.columns(3)
    for col, (mid, label) in zip(cols, DEMO.items()):
        acts = admin.actions_log([mid])
        with col.container(border=True):
            st.markdown(f"**{label}**")
            if not acts:
                st.caption("No actions yet.")
            for a in acts[-6:]:
                st.caption(f"{a['ts']:%d %b} · {TYPE_LABEL.get(a['action_type'], a['action_type'])} · {a['status']}")

    if not counts.empty:
        st.subheader("Actions by day")
        pivot = counts[counts["action_type"] != "compliance_check"].pivot_table(
            index="day", columns="action_type", values="n", aggfunc="sum", fill_value=0)
        pivot.columns = [TYPE_LABEL.get(c, c) for c in pivot.columns]
        st.dataframe(pivot, width="stretch")

# ---------------------------------------------------------------- RM approvals

with tabs[1]:
    st.caption("Loan offers, waivers above the cap and every action on a high-value merchant wait here "
               "(Compliance rules §5, Autonomy policy §1). The agent's graph is paused until you decide.")
    if not pending:
        st.info("Nothing waiting for a decision.")
    rm = st.text_input("Deciding as", value="RM (demo)", key="rm_name")
    order = sorted(pending, key=lambda p: (p["merchant_id"] not in DEMO, p["requested_at"]))
    for p in order:
        draft = p["draft"] if isinstance(p["draft"], dict) else json.loads(p["draft"])
        title = f"{p['name']} ({p['merchant_id']}) · {p['action_type'].replace('_', ' ')} · {p['zone_id']}"
        with st.expander(title, expanded=p["merchant_id"] in DEMO):
            st.markdown(f"**Why approval:** {draft.get('why_approval') or '—'}  \n**Section:** {p['playbook_ref']}")
            st.markdown("**Evidence**\n" + "\n".join(f"- {e}" for e in p["evidence"]))
            if q := draft.get("quote"):
                st.markdown("**Lender quote (mock lender, illustrative terms)**")
                st.dataframe(pd.DataFrame([{k: q[k] for k in ("lender", "amount", "tenure_months", "apr_pct",
                                                               "processing_fee", "kfs_link", "valid_until")}]),
                             hide_index=True, width="stretch")
            for gh_i, gh in enumerate(draft.get("gate_history") or [], 1):
                st.markdown(f"**Draft {gh_i}** · {gate_badge(gh['result'])} · source: {gh['source']}"
                            + (f" · rules: {', '.join(sorted(set(gh['rules'])))}" if gh["rules"] else ""))
                st.markdown(f"> {gh['english']}")
            for a in draft["actions"]:
                g = a.get("gate") or {}
                st.markdown(f"**To send ({a['kind']}, via {a['tool']})**"
                            + (f" · gate {gate_badge(g['result'])}" if g else ""))
                if a.get("english"):
                    st.markdown(f"> {a['english']}")
                if a["args"].get("text") and a["args"]["text"] != a.get("english"):
                    st.caption("As the merchant will read it:")
                    st.markdown(f"> {a['args']['text']}")
                if g.get("judge"):
                    st.caption(f"Judge: {g['judge'].get('verdict')} — {g['judge'].get('reason')}")
                if not a.get("english"):
                    st.json({k: v for k, v in a["args"].items() if k != "text"}, expanded=False)
            c1, c2, _ = st.columns([1, 1, 4])
            if c1.button("Approve", key=f"ap_{p['approval_id']}", type="primary"):
                admin.decide_approval(p["approval_id"], "approved", rm)
                run_agent([p["merchant_id"]], resume_only=True)
                st.rerun()
            if c2.button("Reject", key=f"rj_{p['approval_id']}"):
                admin.decide_approval(p["approval_id"], "rejected", rm)
                run_agent([p["merchant_id"]], resume_only=True)
                st.rerun()

# ---------------------------------------------------------------- human queue

with tabs[2]:
    st.caption("Angry or disputed cases, low-confidence decisions and drafts the gate blocked twice. "
               "The agent took no action on these (Autonomy policy §4).")
    if not queue:
        st.info("Queue is empty.")
    for e in queue:
        p = payload(e)
        with st.expander(f"{e['name']} ({e['merchant_id']}) · {e['zone_id']} · {e['ts']:%d %b}"):
            st.markdown("**Reasons**\n" + "\n".join(f"- {r}" for r in p.get("reasons", [])))
            st.markdown(f"**Prepared:** {p.get('case_summary', '')}")
            st.markdown("**Evidence**\n" + "\n".join(f"- {x}" for x in e["evidence"]))
            note = st.text_input("Note for the CRM", key=f"note_{e['action_id']}")
            if st.button("Mark handled", key=f"h_{e['action_id']}"):
                admin.close_escalation(e["action_id"], st.session_state.get("rm_name", "Human (demo)"), note)
                st.rerun()

# ---------------------------------------------------------------- field plan

with tabs[3]:
    c1, c2, c3 = st.columns(3)
    pdate = c1.date_input("Visit date", value=today + timedelta(days=1), min_value=admin.D0, max_value=admin.LAST_DAY)
    zone = c2.selectbox("Zone", ["Z_A", "Z_B"], format_func=lambda z: {"Z_A": "Z_A · Electronic City",
                                                                       "Z_B": "Z_B · Jayanagar"}[z])
    agents = admin.field_agents(zone)
    agent = c3.selectbox("Field agent", [None] + [a["agent_id"] for a in agents],
                         format_func=lambda a: "All agents" if a is None else
                         next(f"{x['agent_id']} · {x['name']}" for x in agents if x["agent_id"] == a))
    plan = admin.field_plan(pdate, agent, zone)
    st.caption("Save stops (retain) first, then Grow stops (pitch). One visit can retain and sell.")
    if not plan:
        st.info("No visits planned for this day.")
    else:
        rows, n = [], {}
        for v in plan:
            n[v["agent_id"]] = n.get(v["agent_id"], 0) + 1
            rows.append({"agent": v["agent_name"], "stop": n[v["agent_id"]],
                         "type": "🛟 retain" if v["stop_type"] == "retain" else "📈 pitch",
                         "merchant": f"{v['name']} ({v['merchant_id']})", "language": v["language_pref"],
                         "pin": v["pin_code"], "brief": v["brief"], "outcome": v["outcome"] or ""})
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")

# ---------------------------------------------------------------- merchant timeline

with tabs[4]:
    ms = admin.merchants()
    mid = st.selectbox("Merchant", [m["merchant_id"] for m in ms],
                       format_func=lambda i: DEMO.get(i) or next(f"{m['merchant_id']} · {m['name']}" for m in ms
                                                                 if m["merchant_id"] == i))
    t = admin.merchant_timeline(mid)
    pr = t["profile"]
    st.markdown(f"**{pr['name']}** · {pr['owner_name']} · {pr['category']} · {pr['zone_name']} · language "
                f"`{pr['language_pref']}` · value tier `{pr['merchant_value_tier']}` · field agent {pr['agent_name']}")

    tx = pd.DataFrame(t["txn"])
    acts = [a for a in t["actions"] if a["action_type"] != "compliance_check"]
    if not tx.empty:
        tx["date"] = pd.to_datetime(tx["date"])
        base = alt.Chart(tx).encode(x=alt.X("date:T", title=None, axis=alt.Axis(format="%d %b", grid=False)))
        line = base.mark_line(strokeWidth=2, color="#3b6fd4").encode(
            y=alt.Y("upi_count:Q", title="UPI payments a day", axis=alt.Axis(gridOpacity=0.25)))
        hover = alt.selection_point(fields=["date"], nearest=True, on="pointerover", empty=False)
        pts = base.mark_point(size=60, filled=True, color="#3b6fd4").encode(
            y="upi_count:Q", opacity=alt.condition(hover, alt.value(1), alt.value(0)),
            tooltip=[alt.Tooltip("date:T", format="%a %d %b"), alt.Tooltip("upi_count:Q", title="UPI payments"),
                     alt.Tooltip("upi_value:Q", title="UPI value ₹", format=",.0f")]).add_params(hover)
        layers = [line, pts]
        if acts:
            ev = pd.DataFrame([{"date": pd.Timestamp(a["ts"].date()), "what": TYPE_LABEL.get(a["action_type"], a["action_type"])}
                               for a in acts]).groupby("date")["what"].apply(lambda s: ", ".join(sorted(set(s)))).reset_index()
            layers.append(alt.Chart(ev).mark_rule(color="#8a8f98", strokeDash=[3, 3]).encode(
                x="date:T", tooltip=[alt.Tooltip("date:T", format="%a %d %b"), alt.Tooltip("what:N", title="Agent")]))
        if tx["date"].max() >= pd.Timestamp(admin.G0):
            g0 = pd.DataFrame([{"date": pd.Timestamp(admin.G0), "label": "fee go-live"}])
            layers.append(alt.Chart(g0).mark_rule(color="#c0504d", strokeWidth=1.5).encode(x="date:T"))
            layers.append(alt.Chart(g0).mark_text(align="left", dx=4, dy=-4, color="#c0504d").encode(
                x="date:T", y=alt.value(8), text="label:N"))
        st.altair_chart(alt.layer(*layers).properties(height=240), width="stretch")
        st.caption("Daily UPI payments up to the simulation date. Dashed lines: days the agent acted.")

    c1, c2 = st.columns([3, 2])
    with c1:
        st.subheader("What the agent did")
        if not t["actions"]:
            st.caption("No actions yet.")
        for a in t["actions"]:
            p = payload(a)
            head = f"{a['ts']:%d %b} · {TYPE_LABEL.get(a['action_type'], a['action_type'])} · {a['autonomy_tier']} · {a['status']}"
            if a["action_type"] == "compliance_check":
                head = f"{a['ts']:%d %b} · Compliance gate on {p['draft_kind']} · {gate_badge(a['status'])}"
            with st.expander(head):
                st.caption(f"Section: {a['playbook_ref']}" + (f" · approved by {a['approved_by']}" if a["approved_by"] else ""))
                if a["action_type"] == "compliance_check":
                    for h in p.get("rule_hits", []):
                        st.markdown(f"- **{h['rule']}** {h['check']}: {h.get('detail', '')}")
                    if p.get("judge"):
                        st.markdown(f"- Judge: **{p['judge'].get('verdict')}** — {p['judge'].get('reason')}")
                    st.markdown(f"> {p['draft'].get('english')}")
                    st.caption(f"Source: {p['draft'].get('source')}")
                elif p.get("text"):
                    if p.get("english") and p["english"] != p["text"]:
                        st.markdown(f"> {p['english']}")
                        st.caption("As sent:")
                    st.markdown(f"> {p['text']}")
                elif p.get("actions"):
                    for x in p["actions"]:
                        st.markdown(f"- {x['kind']} via {x['tool']}" + (f": {x['english']}" if x.get("english") else ""))
                else:
                    st.json(p, expanded=False)
                st.markdown("**Evidence**\n" + "\n".join(f"- {e}" for e in a["evidence"]))
    with c2:
        st.subheader("Visits")
        for v in t["visits"]:
            st.caption(f"{v['planned_date']:%d %b} · {v['stop_type']} · {v['brief']}")
        if not t["visits"]:
            st.caption("None planned.")
        st.subheader("Open tickets")
        open_t = [x for x in t["tickets"] if x["status"] != "resolved"]
        for x in open_t:
            st.caption(f"{x['ticket_id']} · {x['category']} · {x['sentiment']} · since {x['opened_at']:%d %b}: {x['summary']}")
        if not open_t:
            st.caption("None.")
        st.subheader("CRM notes")
        for n in t["notes"][:8]:
            st.caption(f"{n['ts']:%d %b} · {n['source']}: {n['text']}")
        if not t["notes"]:
            st.caption("None.")

# ---------------------------------------------------------------- actions log

with tabs[5]:
    st.caption("Every action, whoever took it, with evidence, section, tier and approver (Autonomy policy §5).")
    c1, c2 = st.columns(2)
    sel_m = c1.multiselect("Merchants", [m["merchant_id"] for m in admin.merchants()], default=list(DEMO))
    types = sorted(TYPE_LABEL)
    sel_t = c2.multiselect("Action types", types, format_func=lambda x: TYPE_LABEL.get(x, x))
    log = admin.actions_log(sel_m or None, sel_t or None)
    if log:
        df = pd.DataFrame([{"date": a["ts"].date(), "day": day_label(a["ts"].date()), "merchant": a["merchant_id"],
                            "action": TYPE_LABEL.get(a["action_type"], a["action_type"]), "line": a["line"],
                            "tier": a["autonomy_tier"], "status": a["status"], "section": a["playbook_ref"],
                            "approved_by": a["approved_by"] or "", "evidence": " · ".join(a["evidence"][:2])}
                           for a in log])
        st.dataframe(df, hide_index=True, width="stretch")
    else:
        st.info("No actions match.")
