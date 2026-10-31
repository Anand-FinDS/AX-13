"""AX-13 control room: simulation clock, run the agent, RM approvals, human queue, field plan,
merchant timeline and the actions log.

    streamlit run app/streamlit_app.py

Starts in Replay mode: every LLM call is served from data/out/llm_cache and a miss makes no API call.
Live mode needs a toggle and a confirm click. Human-only actions (approval decisions, the clock,
closing escalations) go through app/admin.py; the agent reaches data only through the MCP servers.
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

from agent.nodes import draft as drafting
from agent.nodes.gates import gate
from agent.run_day import estimate, run_day_with_stats
from app import admin
from common import llm

st.set_page_config(page_title="AX-13", page_icon="🛰️", layout="wide")
DEMO = {"M_DEMO_RAVI": "Ravi · kirana (Save)", "M_DEMO_MEENA": "Meena · tailor (Grow)",
        "M_DEMO_SMALL": "Lakshmi · fruit stall (no action)"}
LANG_NAME = {"hi": "Hindi", "kn": "Kannada", "ta": "Tamil", "en": "English"}
TYPE_LABEL = {"technician_booking": "Technician booked", "message": "Message sent", "visit_planned": "Visit planned",
              "offer": "Offer made", "loan_offer": "Loan for RM approval", "waiver": "Waiver / refund",
              "high_value_action": "High-value action for approval", "compliance_check": "Compliance gate",
              "escalation": "Escalated to a human", "crm_update": "CRM note", "ticket_update": "Ticket update",
              "visit_outcome": "Visit outcome"}
TIER = {"agent_alone": ":green-badge[agent alone]", "human_approves": ":orange-badge[RM approves]",
        "human_only": ":red-badge[human only]"}


# ---------------------------------------------------------------- session + helpers

if "llm_base" not in st.session_state:
    st.session_state.llm_base = dict(llm.STATS)       # counters for this browser session
    st.session_state.runs = 0


def session_stats() -> dict:
    return {k: llm.STATS[k] - st.session_state.llm_base[k] for k in llm.STATS}


def run_agent(ids: list[str] | None = None, resume_only: bool = False) -> None:
    label = "Resuming after the RM decision" if resume_only else \
        f"Running the agent for {'the demo merchants' if ids else 'all merchants'} ({llm.get_mode()} mode)"
    with st.spinner(f"{label}…"):
        res, stats = asyncio.run(run_day_with_stats(ids, inject_fixture=True, resume_only=resume_only))
    st.session_state.runs += 1
    st.session_state.last_run = {"sim_date": str(admin.get_sim_state()["sim_date"]), "results": res, "llm": stats}


def get_estimate(scope: str, ids: list[str] | None) -> dict:
    key = (scope, str(today), st.session_state.runs)
    cache = st.session_state.setdefault("estimates", {})
    if key not in cache:
        with st.spinner(f"Estimating LLM calls for {scope} (dry run, nothing written)…"):
            cache[key] = asyncio.run(estimate(ids))
    return cache[key]


def known_estimate(scope: str) -> dict | None:
    return st.session_state.get("estimates", {}).get((scope, str(today), st.session_state.runs))


def day_label(d: date) -> str:
    n, g = (d - admin.D0).days, (d - admin.G0).days
    return ("D0" if n == 0 else f"D{n:+d}") + " · " + ("G0" if g == 0 else f"G{g:+d}")


def payload(a: dict) -> dict:
    p = a["payload_json"]
    return p if isinstance(p, dict) else json.loads(p or "{}")


def gate_badge(result: str | None, judge: str | None = None) -> str:
    if not result:
        return ""
    b = {"pass": ":green-badge[gate pass]", "block": ":red-badge[gate block]",
         "unsure": ":orange-badge[gate unsure]"}.get(result, f":gray-badge[gate {result}]")
    if judge == "not_cached":
        b += " :orange-badge[judge not cached]"
    elif result == "pass" and judge is None:
        b += " :gray-badge[rules only]"
    return b


def made_by(source: str | None) -> str:
    """template / LLM translation / LLM / not cached / fixture, from a draft's source label."""
    if not source:
        return ""
    if "not cached" in source:
        return "template (LLM not cached)"
    if source.startswith("eval fixture"):
        return "eval fixture"
    if source.startswith("template"):
        return "LLM translation of template" if "LLM" in source else "template"
    return "LLM"


def source_badge(source: str | None) -> str:
    return {"": "", "template": ":gray-badge[template]", "template (LLM not cached)": ":orange-badge[template · LLM not cached]",
            "eval fixture": ":violet-badge[eval fixture]", "LLM translation of template": ":blue-badge[template · LLM translated]",
            "LLM": ":blue-badge[LLM]"}[made_by(source)]


def action_badges(a: dict) -> str:
    """Tier, section cited, gate result and template vs LLM, for one action row."""
    p = payload(a)
    parts = [TIER.get(a["autonomy_tier"], a["autonomy_tier"]), f":gray-badge[{a['playbook_ref']}]"]
    if a["action_type"] == "compliance_check":
        parts += [gate_badge(a["status"], (p.get("judge") or {}).get("verdict")),
                  source_badge((p.get("draft") or {}).get("source"))]
    elif p.get("audit"):
        au = p["audit"]
        parts += [gate_badge(au.get("gate"), au.get("judge")), source_badge(au.get("source"))]
    return " ".join(x for x in parts if x)


def show_text(text: str | None, english: str | None, language: str) -> None:
    """Local-language message with its English version next to it."""
    if not text:
        return
    if english and english != text:
        c1, c2 = st.columns(2)
        c1.caption(f"As sent ({language})")
        c1.markdown(f"> {text}")
        c2.caption("English")
        c2.markdown(f"> {english}")
    else:
        st.markdown(f"> {text}")


def explain(what: str, why: str) -> None:
    st.caption(f"**What this shows:** {what}  \n**Why it matters:** {why}")


# ---------------------------------------------------------------- header bar

state = admin.get_sim_state()
today = state["sim_date"]
mode = llm.get_mode()
ss = session_stats()
with st.container(border=True):
    h = st.columns([2, 1.3, 1.5, 1.6, 1.6])
    h[0].markdown(f"**{today:%a %d %b %Y}** · {day_label(today)}")
    h[1].markdown("Fee live: **yes**" if state["fee_live"] else "Fee live: **no**")
    h[2].markdown("Mode: :red-badge[Live · uses API]" if mode == "live" else "Mode: :green-badge[Replay · no API]")
    h[3].markdown(f"LLM calls this session: **{ss['api_calls']}**")
    h[4].markdown(f"Cache hits: **{ss['hits']}**" + (f" · not cached: **{ss['misses']}**" if ss["misses"] else ""))

# ---------------------------------------------------------------- sidebar

with st.sidebar:
    st.title("AX-13")
    st.caption("Autonomous Merchant Growth & Retention Agent · synthetic data")

    st.subheader("LLM mode")
    want_live = st.toggle("Live mode (uses API)", value=mode == "live", key="want_live")
    if want_live and mode != "live":
        st.warning("Live mode calls the API on every cache miss. That costs money.")
        if st.button("Confirm: allow API calls", type="primary", width="stretch"):
            llm.set_mode("live")
            st.rerun()
    elif not want_live and mode == "live":
        llm.set_mode("replay")
        st.rerun()
    st.caption("Replay: every LLM call comes from the cache; a miss is shown as *not cached* and the step "
               "continues with the template. No API calls.")

    st.subheader("Agent")
    est = get_estimate("demo", list(DEMO))
    if st.button(f"Run agent: 3 demo merchants (~{est['llm_calls_needed']} LLM calls, {est['llm_cached']} cached)",
                 width="stretch", type="primary"):
        run_agent(list(DEMO))
        st.rerun()
    if est["llm_calls_needed"] and mode != "live":
        st.caption(f"{est['llm_calls_needed']} call(s) not cached: in Replay they run as templates, marked not cached.")
    full = known_estimate("full")
    label = (f"Run full workflow: all 200 merchants (~{full['llm_calls_needed']} LLM calls, {full['llm_cached']} cached)"
             if full else "Run full workflow: all 200 merchants (estimate first)")
    if not full and st.button("Estimate full run (dry run, ~1 min)", width="stretch"):
        get_estimate("full", None)
        st.rerun()
    if mode != "live":
        if st.button(label, width="stretch", help="Replay: free; uncached steps run as templates"):
            run_agent()
            st.rerun()
    elif full:
        ok = st.checkbox(f"I confirm: up to ~{full['llm_calls_needed']} API calls")
        if st.button(label, width="stretch", disabled=not ok):
            run_agent()
            st.rerun()

    st.subheader("End-to-end demo")
    st.caption("One click: Reset → all 200 merchants on D0, D+1, G0 and G0+5. Meena's loan is approved by a "
               "scripted RM; all other approvals stay in the queue. Each day is saved below. Takes ~10 min.")
    ok_e2e = mode != "live" or st.checkbox("I confirm: Live end-to-end run (a few API calls per day)")
    if st.button(f"Run full demo D0 → G0+5 ({'Live' if mode == 'live' else 'Replay, free'})", width="stretch",
                 disabled=not ok_e2e):
        from app.demo_script import run_full_demo
        with st.status("Running the end-to-end demo…", expanded=True) as box:
            rows = run_full_demo(mode, progress=box.write)
            box.update(label=f"Done: {len(rows)} days saved ({mode})", state="complete")
        st.session_state.runs += 1
        st.rerun()

    st.subheader("Saved days")
    snaps = admin.list_snapshots()
    if not snaps:
        st.caption("None yet. Run the end-to-end demo to save D0, D+1, G0 and G0+5.")
    else:
        pick = st.selectbox("Load a saved day", range(len(snaps)), format_func=lambda i: (
            f"{snaps[i]['mode'].title()} · {snaps[i]['label']} · RM {snaps[i]['rm_queue']} · human {snaps[i]['human_queue']}"))
        s = snaps[pick]
        st.caption(f"Saved {s['saved_at'][:16].replace('T', ' ')} · LLM {s['llm']['hits']} cached, "
                   f"{s['llm']['api_calls']} API calls that day")
        if st.button("Load this day", width="stretch"):
            admin.load_snapshot(s["mode"], s["label"])
            st.session_state.pop("last_run", None)
            st.session_state.runs += 1
            st.rerun()
        st.caption("After loading, look around the tabs, or move the clock and press Run to continue from there.")

    st.subheader("Clock")
    c1, c2 = st.columns(2)
    if c1.button("Advance 1 day", width="stretch", disabled=today >= admin.LAST_DAY):
        admin.advance_day(1)
        st.rerun()
    if c2.button("Jump to G0", width="stretch", disabled=today >= admin.G0):
        admin.jump_to_go_live()
        st.rerun()
    if st.button("Advance 5 days", width="stretch", disabled=today >= admin.LAST_DAY):
        admin.advance_day(5)
        st.rerun()
    with st.expander("Reset to D0"):
        st.caption("Reloads all data, clears agent actions and LangGraph threads. The LLM cache is kept.")
        if st.button("Reset now", width="stretch"):
            with st.spinner("Reloading…"):
                admin.reset()
            st.session_state.pop("last_run", None)
            st.session_state.runs += 1
            st.rerun()

    if run := st.session_state.get("last_run"):
        st.subheader("Last run")
        res, s = run["results"], run["llm"]
        st.caption(f"{run['sim_date']} · {len(res)} merchants · LLM {s['hits']} cached, {s['misses']} not cached, "
                   f"{s['api_calls']} API calls")
        st.dataframe(pd.Series([r["status"] for r in res]).value_counts().rename("merchants"), width="stretch")
        for r in [r for r in res if r["status"] == "error"]:
            st.error(f"{r['merchant_id']}: {r.get('error')}")

    with st.expander("Glossary"):
        st.markdown("""
- **D0**: the first simulation day, 31 Oct 2026. D+1 is the next day.
- **G0**: fee go-live, 13 Nov 2026 (the proposed UPI merchant fee starts). G+5 is five days after.
- **Churn score** (0–100): how strongly the data says this merchant is slipping away. Computed in code.
- **Growth score** (0–100): how ready the merchant is for the next step (Soundbox, loan path).
- **Line**: *Save* (fix a problem), *Grow* (next product step) or *none*. Save always comes before Grow.
- **Cause**: why the line was chosen: device, network, charge, fee, competitor, closed, threshold, activation.
- **Evidence**: the numbers behind a decision, in plain language. No evidence, no action.
- **Cited section**: the policy paragraph the action follows, e.g. *Retention playbook §3.1*.
- **Autonomy tier**: *agent alone* (acts now) · *RM approves* (prepared, waits for a relationship manager) ·
  *human only* (agent takes no action, hands over the case).
- **Compliance gate**: checks every fee message, cost comparison and loan draft. Rules in code first, then
  an LLM judge (loans and demo merchants). Blocked drafts are redrafted once, then go to a human.
- **Replay vs Live mode**: Replay serves every LLM call from the cache and never calls the API; Live calls
  the API on a cache miss (costs money).
- **Template vs LLM**: outside the 3 demo merchants, messages are the policy templates filled with the
  merchant's own numbers, in English. Demo merchants get LLM drafts in their language.
""")

pending = admin.list_pending_approvals()
queue = admin.human_queue()
tabs = st.tabs(["Overview", f"RM approvals ({len(pending)})", f"Human queue ({len(queue)})", "Field plan",
                "Merchant timeline", "Actions log"])

# ---------------------------------------------------------------- overview

with tabs[0]:
    explain("Today's work at a glance, the three demo merchants, and how to walk through the demo.",
            "Shows how much the agent resolved on its own, and how much it handed to people.")
    with st.container(border=True):
        st.markdown("**Demo guide** · three merchants, one fee change")
        st.markdown("""
| # | Click | What to look for |
|---|---|---|
| 1 | **Reset to D0** (sidebar) | Clean data, empty queues, simulation date 31 Oct. |
| 2 | **Run agent: 3 demo merchants** | Ravi: Soundbox fault → technician booked + Hindi message, no human. Meena: cost comparison sent; her loan draft is **blocked** by the gate (the over-promising fixture), redrafted, and waits in **RM approvals**. Approve it there → the offer goes out. |
| 3 | **Advance 1 day** → **Run** | Nothing new: cases already handled are skipped. |
| 4 | **Jump to G0** → **Run** | Fee go-live. Ravi gets his own fee estimate (₹537/month) in Hindi. Lakshmi gets one *not affected* message, nothing else. |
| 5 | **Advance 5 days** → **Run** | Ravi's UPI dropped after go-live: the Save case opens and a field visit is planned (see **Field plan**, 19 Nov). |
| 6 | **Merchant timeline** | Each merchant's UPI chart with the days the agent acted, and every action with its evidence, section and gate result. |

**All 200 merchants:** *Run full demo D0 → G0+5* does the whole sequence for everyone in one click and saves each day.
Or pick a day under **Saved days** (e.g. *Live · G0+5*) and press *Load this day* to see the full RM queue, human
queue, field plan and actions log at once; then move the clock and press Run to continue from there.
""")
    counts = pd.DataFrame(admin.day_counts())
    todays = counts[counts["day"] == today] if not counts.empty else counts
    m = st.columns(5)
    acted = todays[(todays["autonomy_tier"] == "agent_alone") & (todays["action_type"] != "compliance_check")] \
        if not todays.empty else todays
    gate_today = todays[todays["action_type"] == "compliance_check"] if not todays.empty else todays
    m[0].metric("Agent acted alone today", int(acted["n"].sum()) if not acted.empty else 0)
    m[1].metric("Waiting for RM", len(pending))
    m[2].metric("With a human", len(queue))
    m[3].metric("Gate checks today", int(gate_today["n"].sum()) if not gate_today.empty else 0)
    blocked = gate_today[gate_today["status"] == "block"] if not gate_today.empty else gate_today
    m[4].metric("Drafts blocked today", int(blocked["n"].sum()) if not blocked.empty else 0)

    st.subheader("Demo merchants")
    cols = st.columns(3)
    for col, (mid, label) in zip(cols, DEMO.items()):
        acts = [a for a in admin.actions_log([mid]) if a["action_type"] != "compliance_check"]
        with col.container(border=True):
            st.markdown(f"**{label}**")
            if not acts:
                st.caption("No actions yet.")
            for a in acts[-5:]:
                st.markdown(f"{a['ts']:%d %b} · {TYPE_LABEL.get(a['action_type'], a['action_type'])}  \n"
                            + action_badges(a))

# ---------------------------------------------------------------- RM approvals

with tabs[1]:
    explain("Actions the agent prepared but may not take alone: loan offers, refunds above the cap, and every "
            "action on a high-value merchant. Each shows its evidence, gate history and the exact text.",
            "Credit and large money decisions stay with a person (Compliance rules §5). The agent's graph is "
            "paused until you decide, then it continues by itself.")
    if not pending:
        st.info("Nothing waiting for a decision.")
    rm = st.text_input("Deciding as", value="RM (demo)", key="rm_name")
    for p in sorted(pending, key=lambda p: (p["merchant_id"] not in DEMO, p["requested_at"])):
        d = p["draft"] if isinstance(p["draft"], dict) else json.loads(p["draft"])
        localized = d.get("localized") or {}
        with st.expander(f"{p['name']} ({p['merchant_id']}) · {p['action_type'].replace('_', ' ')} · {p['zone_id']}",
                         expanded=p["merchant_id"] in DEMO):
            st.markdown(f"**Why approval:** {d.get('why_approval') or '—'}  \n**Section:** {p['playbook_ref']}")
            st.markdown("**Evidence**\n" + "\n".join(f"- {e}" for e in p["evidence"]))
            if q := d.get("quote"):
                st.markdown("**Lender quote** (mock lender, illustrative terms)")
                st.dataframe(pd.DataFrame([{k: q[k] for k in ("lender", "amount", "tenure_months", "apr_pct",
                                                               "processing_fee", "kfs_link", "valid_until")}]),
                             hide_index=True, width="stretch")
            for i, gh in enumerate(d.get("gate_history") or [], 1):
                st.markdown(f"**Draft {i}** {gate_badge(gh['result'])} {source_badge(gh['source'])}"
                            + (f" · rules broken: {', '.join(sorted(set(gh['rules'])))}" if gh["rules"] else ""))
                st.markdown(f"> {gh['english']}")
            for a in d["actions"]:
                g = a.get("gate") or {}
                st.markdown(f"**To send: {a['kind']}** via `{a['tool']}` "
                            f"{gate_badge(g.get('result'), (g.get('judge') or {}).get('verdict'))} {source_badge(a.get('source'))}")
                loc = localized.get(a["id"])
                use_loc = bool(loc and loc.get("gate") == "pass")
                show_text(loc["text"] if use_loc else a["args"].get("text"),
                          loc["english"] if use_loc else a.get("english"), a.get("language", ""))
                if not a["args"].get("text"):
                    st.json({k: v for k, v in a["args"].items() if k not in ("text", "english", "audit")}, expanded=False)
                lang = a.get("language", "English")
                if a.get("spec") and lang != "English" and p["merchant_id"] not in DEMO and not loc:
                    hint = "" if llm.get_mode() == "live" else ", replay: from cache only"
                    if st.button(f"Draft in {lang} with LLM (1–2 calls{hint})", key=f"loc_{p['approval_id']}_{a['id']}"):
                        try:
                            dr = drafting.write(a["spec"], lang)
                            gr = gate(a["gate_kind"], dr, a["spec"]["facts"], True) if a.get("gate_kind") else \
                                {"result": "pass", "rule_hits": []}
                            admin.set_localized_draft(p["approval_id"], a["id"], {
                                "text": dr["text"], "english": dr["english"], "source": "LLM (drafted for the RM)",
                                "gate": gr["result"], "rule_hits": gr["rule_hits"]})
                            st.rerun()
                        except llm.LLMNotCached:
                            st.warning("Not cached. Switch to Live mode to draft this message with the LLM.")
                elif loc:
                    st.caption(f"{lang} draft by the LLM for the RM: {gate_badge(loc['gate'])}. "
                               + ("It is sent instead of the English template if you approve." if use_loc else
                                  "Blocked by the gate, so the English template is sent."))
                if g.get("judge"):
                    st.caption(f"Judge: {g['judge'].get('verdict')} — {g['judge'].get('reason')}")
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
    explain("Cases the agent handed to a person and did not act on: angry or disputed merchants, decisions with "
            "low confidence, and drafts the gate blocked twice.",
            "An autonomous agent is only safe if it knows when to stop (Autonomy policy §4). Each case comes "
            "prepared, with reasons and evidence.")
    if not queue:
        st.info("Queue is empty.")
    for e in queue:
        p = payload(e)
        with st.expander(f"{e['name']} ({e['merchant_id']}) · {e['zone_id']} · {e['ts']:%d %b}"):
            st.markdown(f":red-badge[human only] :gray-badge[{e['playbook_ref']}]")
            st.markdown("**Reasons**\n" + "\n".join(f"- {r}" for r in p.get("reasons", [])))
            st.markdown(f"**Prepared:** {p.get('case_summary', '')}")
            st.markdown("**Evidence**\n" + "\n".join(f"- {x}" for x in e["evidence"]))
            note = st.text_input("Note for the CRM", key=f"note_{e['action_id']}")
            if st.button("Mark handled", key=f"h_{e['action_id']}"):
                admin.close_escalation(e["action_id"], st.session_state.get("rm_name", "Human (demo)"), note)
                st.rerun()

# ---------------------------------------------------------------- field plan

with tabs[3]:
    explain("Each field agent's stops for a day: Save visits (retain) first, then Grow visits (pitch), each with "
            "a one-line brief.",
            "Field time is the costliest resource. Visits go only where a conversation is needed; everything "
            "else is resolved remotely (Retention playbook §1.5).")
    c1, c2, c3 = st.columns(3)
    pdate = c1.date_input("Visit date", value=min(today + timedelta(days=1), admin.LAST_DAY),
                          min_value=admin.D0, max_value=admin.LAST_DAY)
    zone = c2.selectbox("Zone", ["Z_A", "Z_B"], format_func=lambda z: {"Z_A": "Z_A · Electronic City",
                                                                       "Z_B": "Z_B · Jayanagar"}[z])
    agents = admin.field_agents(zone)
    agent = c3.selectbox("Field agent", [None] + [a["agent_id"] for a in agents],
                         format_func=lambda a: "All agents" if a is None else
                         next(f"{x['agent_id']} · {x['name']}" for x in agents if x["agent_id"] == a))
    plan = admin.field_plan(pdate, agent, zone)
    if not plan:
        st.info("No visits planned for this day.")
    else:
        rows, n = [], {}
        for v in plan:
            n[v["agent_id"]] = n.get(v["agent_id"], 0) + 1
            rows.append({"agent": v["agent_name"], "stop": n[v["agent_id"]],
                         "type": "retain (Save)" if v["stop_type"] == "retain" else "pitch (Grow)",
                         "merchant": f"{v['name']} ({v['merchant_id']})", "language": LANG_NAME.get(v["language_pref"]),
                         "pin": v["pin_code"], "brief": v["brief"], "outcome": v["outcome"] or ""})
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")

# ---------------------------------------------------------------- merchant timeline

MARKERS = {"Agent action": "#8a8f98", "Technician": "#e08a2c", "Message": "#2e9e6b", "Fee go-live": "#c0504d"}


def marker(action_type: str) -> str:
    return {"technician_booking": "Technician", "message": "Message", "offer": "Message"}.get(action_type, "Agent action")


with tabs[4]:
    explain("One merchant's daily UPI payments up to the simulation date, the days the agent acted, and every "
            "action with its evidence, cited section and gate result.",
            "Any action can be traced back to the numbers that triggered it (Autonomy policy §5).")
    ms = admin.merchants()
    mid = st.selectbox("Merchant", [m["merchant_id"] for m in ms],
                       format_func=lambda i: DEMO.get(i) or next(f"{m['merchant_id']} · {m['name']}" for m in ms
                                                                 if m["merchant_id"] == i))
    t = admin.merchant_timeline(mid)
    pr = t["profile"]
    lang = LANG_NAME.get(pr["language_pref"], pr["language_pref"])
    st.markdown(f"**{pr['name']}** · {pr['owner_name']} · {pr['category']} · {pr['zone_name']} · {lang} · "
                f"value tier {pr['merchant_value_tier']} · field agent {pr['agent_name']}")

    tx = pd.DataFrame(t["txn"])
    acts = [a for a in t["actions"] if a["action_type"] != "compliance_check"]
    if not tx.empty:
        tx["date"] = pd.to_datetime(tx["date"])
        x = alt.X("date:T", title=None, axis=alt.Axis(format="%d %b", grid=False))
        hover = alt.selection_point(fields=["date"], nearest=True, on="pointerover", empty=False)
        line = alt.Chart(tx).mark_line(strokeWidth=2, color="#3b6fd4").encode(
            x=x, y=alt.Y("upi_count:Q", title="UPI payments a day", axis=alt.Axis(gridOpacity=0.25)))
        pts = alt.Chart(tx).mark_point(size=60, filled=True, color="#3b6fd4").encode(
            x=x, y="upi_count:Q", opacity=alt.condition(hover, alt.value(1), alt.value(0)),
            tooltip=[alt.Tooltip("date:T", format="%a %d %b"), alt.Tooltip("upi_count:Q", title="UPI payments"),
                     alt.Tooltip("upi_value:Q", title="UPI value ₹", format=",.0f")]).add_params(hover)
        ev = [{"date": pd.Timestamp(a["ts"].date()), "marker": marker(a["action_type"]),
               "what": TYPE_LABEL.get(a["action_type"], a["action_type"])} for a in acts]
        if tx["date"].max() >= pd.Timestamp(admin.G0):
            ev.append({"date": pd.Timestamp(admin.G0), "marker": "Fee go-live", "what": "Proposed UPI fee starts"})
        layers = [line, pts]
        if ev:
            evdf = pd.DataFrame(ev).groupby(["date", "marker"])["what"].apply(
                lambda s: ", ".join(sorted(set(s)))).reset_index()
            layers.append(alt.Chart(evdf).mark_rule(strokeWidth=2, strokeDash=[4, 3]).encode(
                x="date:T",
                color=alt.Color("marker:N", title=None, legend=alt.Legend(orient="top"),
                                scale=alt.Scale(domain=list(MARKERS), range=list(MARKERS.values()))),
                tooltip=[alt.Tooltip("date:T", format="%a %d %b"), alt.Tooltip("what:N", title="Event")]))
        st.altair_chart(alt.layer(*layers).properties(height=250), width="stretch")
        st.caption("Blue line: UPI payments a day. Dashed lines: days the agent acted (colour = kind of action) and "
                   "fee go-live. Hover for details.")

    c1, c2 = st.columns([3, 2])
    with c1:
        st.subheader("What the agent did")
        if not t["actions"]:
            st.caption("No actions yet.")
        for a in t["actions"]:
            p = payload(a)
            what = (f"Compliance gate on {p['draft_kind']}" if a["action_type"] == "compliance_check"
                    else TYPE_LABEL.get(a["action_type"], a["action_type"]))
            with st.expander(f"{a['ts']:%d %b} · {what} · {a['status']}"):
                st.markdown(action_badges(a) + (f" :gray-badge[approved by {a['approved_by']}]" if a["approved_by"] else ""))
                if a["action_type"] == "compliance_check":
                    for hh in p.get("rule_hits", []):
                        st.markdown(f"- **{hh['rule']}** {hh['check']}: {hh.get('detail', '')}")
                    if p.get("judge"):
                        st.markdown(f"- Judge: **{p['judge'].get('verdict')}** — {p['judge'].get('reason')}")
                    show_text(p["draft"].get("text"), p["draft"].get("english"), lang)
                elif p.get("text"):
                    show_text(p["text"], p.get("english"), lang)
                elif p.get("actions"):
                    for xa in p["actions"]:
                        st.markdown(f"- {xa['kind']} via `{xa['tool']}`")
                        show_text(xa["args"].get("text"), xa.get("english"), lang)
                else:
                    st.json({k: v for k, v in p.items() if k != "audit"}, expanded=False)
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
    explain("Every action, whoever took it: evidence, cited section, autonomy tier, gate result, how the text was "
            "made (template or LLM), and the approver.",
            "This is the audit trail a compliance team would ask for (Autonomy policy §5).")
    c1, c2 = st.columns(2)
    sel_m = c1.multiselect("Merchants", [m["merchant_id"] for m in admin.merchants()], default=list(DEMO))
    sel_t = c2.multiselect("Action types", sorted(TYPE_LABEL), format_func=lambda v: TYPE_LABEL.get(v, v))
    log = admin.actions_log(sel_m or None, sel_t or None)
    if log:
        rows = []
        for a in log:
            p = payload(a)
            au = p.get("audit") or {}
            src = au.get("source") or ((p.get("draft") or {}).get("source") if a["action_type"] == "compliance_check" else None)
            rows.append({"date": a["ts"].date(), "day": day_label(a["ts"].date()), "merchant": a["merchant_id"],
                         "action": TYPE_LABEL.get(a["action_type"], a["action_type"]), "line": a["line"],
                         "tier": a["autonomy_tier"].replace("_", " "), "status": a["status"],
                         "section": a["playbook_ref"],
                         "gate": (a["status"] if a["action_type"] == "compliance_check" else au.get("gate")) or "",
                         "text made by": made_by(src), "approved by": a["approved_by"] or "",
                         "evidence": " · ".join(a["evidence"][:2])})
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    else:
        st.info("No actions match.")
