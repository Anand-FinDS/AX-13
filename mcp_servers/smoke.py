"""End-to-end smoke test through the agent's MCP client (real stdio servers).

    python -m mcp_servers.smoke

Resets to D0, reads Ravi's merchant view, performs every write tool once (plus two calls that
must be rejected), plays the RM and the clock through app/admin.py, then resets again.
"""
from __future__ import annotations

import asyncio
import json
import sys
import time

from app import admin
from mcp_servers.client import SERVERS, agent_tools, get_client

RESULTS: list[tuple[str, str, bool, str]] = []
CITE = {"evidence": ["UPI down 36% vs prior 3 weeks", "Soundbox fault 25-31 Oct", "ticket T00098 open"],
        "playbook_ref": "Retention playbook §3.1"}


class ToolRejected(Exception):
    pass


# Tools whose result is a list of rows (the adapter returns one content block per row).
LIST_TOOLS = {"list_merchants", "list_field_agents", "list_tickets", "list_visits", "get_txn_daily", "get_device_health",
              "get_settlements", "get_deductions", "get_notes", "get_actions", "get_offers"}


def _parse(out, as_list: bool):
    """Content blocks → Python. A rejected call comes back as one error text the model can read."""
    texts = [b.get("text", "") if isinstance(b, dict) else str(b) for b in out] if isinstance(out, list) else [out]
    if len(texts) == 1 and texts[0].startswith("Error executing tool"):
        raise ToolRejected(texts[0].split(": ", 1)[-1])
    items = [json.loads(t) for t in texts]
    return items if as_list else items[0]


async def call(tools: dict, step: str, name: str, check=lambda r: True, detail=lambda r: "", expect_error=False, **args):
    try:
        r = _parse(await tools[name].ainvoke(args), name in LIST_TOOLS)
        ok = (not expect_error) and bool(check(r))
        RESULTS.append((step, name, ok, "UNEXPECTEDLY ACCEPTED" if expect_error else detail(r)))
        return r
    except ToolRejected as e:  # the server refused the call (error text the model would see)
        msg = str(e).split("\n")[0][:110]
        RESULTS.append((step, name, expect_error, f"rejected: {msg}"))
        return None


def human(step: str, what: str, fn, *a, **k):
    r = fn(*a, **k)
    RESULTS.append((step, f"admin.{what}", True, str({x: r[x] for x in list(r)[:3]})))
    return r


async def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    t0 = time.time()
    admin.reset()
    client = get_client()
    per_server = {s: len(await client.get_tools(server_name=s)) for s in SERVERS}
    async with agent_tools() as tool_list:
        await run_steps({t.name: t for t in tool_list}, per_server)
    return report(t0, {t.name for t in tool_list})


async def run_steps(tools: dict, per_server: dict) -> None:
    RESULTS.append(("0", "list tools", True, f"{len(tools)} tools: " + ", ".join(f"{s} {n}" for s, n in per_server.items())))

    await call(tools, "1", "get_sim_state", lambda r: r["day"] == "D+0", lambda r: f"{r['sim_date']} {r['day']}, fee_live={r['fee_live']}")
    v = await call(tools, "2", "get_merchant_view", lambda r: r["volume"]["daily"][-1]["date"] == "2026-10-31",
                   lambda r: (f"last day {r['volume']['daily'][-1]['date']}, UPI {r['volume']['change_vs_prior']:+.0%} vs prior 3 wks, "
                              f"soundbox fault {r['devices'][0]['fault_days_7d']}/7 days, open ticket: "
                              f"{r['open_tickets'][0]['ticket_id']}, Oct fee est ₹{r['fee']['last_full_month']['est_monthly_fee']:.0f}"),
                   merchant_id="M_DEMO_RAVI")
    await call(tools, "3", "get_peer_change", lambda r: r["below_bottom_quarter"],
               lambda r: f"Ravi {r['merchant_change']:+.0%} vs peers median {r['peer_median']:+.0%} (n={r['peer_n']}), below bottom quarter",
               merchant_id="M_DEMO_RAVI", base_start="2026-10-04", base_end="2026-10-10", comp_start="2026-10-25", comp_end="2026-10-31")
    await call(tools, "4", "get_fee_summary", lambda r: r["not_affected_variant"] == "A_exempt",
               lambda r: f"Small: Oct UPI ₹{r['last_full_month']['monthly_upi_value']:,.0f}, variant {r['not_affected_variant']}",
               merchant_id="M_DEMO_SMALL")
    await call(tools, "5", "book_technician", lambda r: r["date"] == "2026-11-01", lambda r: f"{r['booking_id']} on {r['date']} {r['time_window']}",
               merchant_id="M_DEMO_RAVI", device_type="soundbox", preferred_date="2026-11-01", **CITE)
    await call(tools, "6", "send_message", lambda r: r["status"] == "sent", lambda r: f"{r['action_id']} via {r['channel']}",
               merchant_id="M_DEMO_RAVI", channel="whatsapp", text="Technician booked for 1 Nov, 10am-1pm. Device will be swapped.",
               **CITE)
    ticket_id = v["open_tickets"][0]["ticket_id"] if v else "T00098"
    await call(tools, "7", "chase_ticket", lambda r: r["chased"], lambda r: r["action_id"], ticket_id=ticket_id,
               note="Technician booked, keep open until uptime holds 2 days", **CITE)
    await call(tools, "8", "send_message", expect_error=True, merchant_id="M_DEMO_RAVI", channel="sms", text="hi",
               evidence=[], playbook_ref="Retention playbook §3.1")

    human("9", "advance_day", admin.advance_day, 2)
    agents = await call(tools, "10", "list_field_agents", lambda r: len(r) == 3, lambda r: ", ".join(a["agent_id"] for a in r), zone_id="Z_A")
    agent = next(a["agent_id"] for a in agents) if agents else "FA_A1"
    visit = await call(tools, "11", "plan_visit", lambda r: r["planned_date"] == "2026-11-03", lambda r: f"{r['visit_id']} on {r['planned_date']}",
                       agent_id=agent, merchant_id="M_DEMO_RAVI", planned_date="2026-11-03", stop_type="retain",
                       brief="Device follow-up after swap; check announcements; listen for competitor or credit needs",
                       evidence=["Soundbox swapped 1 Nov", "festive stock-up week"], playbook_ref="Retention playbook §1.5")
    await call(tools, "12", "list_visits", lambda r: len(r) == 1, lambda r: f"{len(r)} visit(s) for {agent}", agent_id=agent)
    await call(tools, "13", "record_visit_outcome", lambda r: r["visit_id"], lambda r: r["action_id"],
               visit_id=visit["visit_id"] if visit else "x", outcome="Device working; loan interest; rival offer; come after Diwali",
               transcript_id="clip1_ravi", evidence=["clip1_ravi turns 3, 5, 9, 11"], playbook_ref="Retention playbook §8.4")
    await call(tools, "14", "add_note", lambda r: r["note_id"], lambda r: r["note_id"], merchant_id="M_DEMO_RAVI", source="voice",
               text="Asked about loan for festive stock and prepayment; rival offering lower fee; follow up after Diwali.",
               structured={"intents": ["loan_interest"], "objections": ["competitor_lower_fee"]},
               evidence=["clip1_ravi"], playbook_ref="Retention playbook §8.4", line="grow")
    await call(tools, "15", "update_ticket", lambda r: r["status"] == "resolved", lambda r: f"{ticket_id} → resolved",
               ticket_id=ticket_id, status="resolved", note="Uptime held 1-2 Nov after swap", **CITE)

    loan = {"evidence": ["clip1_ravi turn 5: loan for festive stock", "90+ days steady history"], "playbook_ref": "Product catalogue §4",
            "line": "grow"}
    await call(tools, "16", "make_offer", expect_error=True, merchant_id="M_DEMO_RAVI", product="loan", channel="field",
               text="Loan details", **loan)
    req = await call(tools, "17", "request_approval", lambda r: r["status"] == "pending_approval", lambda r: r["approval_id"],
                     merchant_id="M_DEMO_RAVI", action_type="loan_offer",
                     draft={"lender": "Partner NBFC (illustrative)", "amount": 200000, "tenure_months": 12, "apr": "per lender KFS"},
                     **loan)
    human("18", "decide_approval", admin.decide_approval, req["approval_id"], "approved", "RM (demo)")
    await call(tools, "19", "get_approval", lambda r: r["decision"] == "approved", lambda r: f"{r['decision']} by {r['decided_by']}",
               approval_id=req["approval_id"])
    await call(tools, "20", "make_offer", lambda r: r["response"] == "pending", lambda r: f"{r['offer_id']} loan, response {r['response']}",
               merchant_id="M_DEMO_RAVI", product="loan", channel="field", approval_id=req["approval_id"],
               text="Partner NBFC: amount, tenure, APR, all charges and KFS link attached", **loan)
    await call(tools, "21", "get_offers", lambda r: r[-1]["product"] == "loan", lambda r: f"{len(r)} offers, latest {r[-1]['product']} ({r[-1]['response']})",
               merchant_id="M_DEMO_RAVI")
    await call(tools, "22", "grant_waiver", lambda r: r["tier"] == "agent_alone", lambda r: f"₹{r['amount']:.0f}, {r['tier']}",
               merchant_id="M_DEMO_RAVI", amount=149, reason="November Soundbox rent",
               evidence=["400 x 12 x 0.10 = 480 > 149"], playbook_ref="Retention playbook §7.1")
    await call(tools, "23", "get_waiver_budget", lambda r: r["spent"] == 149, lambda r: f"spent ₹{r['spent']:.0f} of ₹{r['budget']:.0f}", zone_id="Z_A")
    await call(tools, "24", "create_ticket", lambda r: r["status"] == "open", lambda r: r["ticket_id"], merchant_id="M_DEMO_SMALL",
                   category="other", summary="Asked whether the new UPI fee applies", channel="app",
                   evidence=["Oct UPI ₹38k, under ₹1 lakh"], playbook_ref="Retention playbook §6.2", line="none")
    await call(tools, "25", "list_tickets", lambda r: len(r) == 1, lambda r: f"{len(r)} ticket(s) for Small", merchant_id="M_DEMO_SMALL")
    await call(tools, "26", "get_notes", lambda r: len(r) >= 2, lambda r: f"{len(r)} notes", merchant_id="M_DEMO_RAVI")
    await call(tools, "27", "get_actions", lambda r: all(a["evidence"] and a["playbook_ref"] for a in r),
               lambda r: f"{len(r)} actions, all with evidence + section", merchant_id="M_DEMO_RAVI")
    # remaining read tools
    for step, name, args in (("28", "list_merchants", {"zone_id": "Z_B", "category": "tailor"}),
                             ("29", "get_merchant_profile", {"merchant_id": "M_DEMO_MEENA"}),
                             ("30", "get_txn_daily", {"merchant_id": "M_DEMO_RAVI", "days": 7}),
                             ("31", "get_device_health", {"merchant_id": "M_DEMO_RAVI", "days": 3}),
                             ("32", "get_settlements", {"merchant_id": "M_DEMO_RAVI", "days": 3}),
                             ("33", "get_deductions", {"merchant_id": "M_DEMO_RAVI", "days": 3})):
        await call(tools, step, name, lambda r: r, lambda r: f"{len(r)} rows" if isinstance(r, list) else "ok", **args)
    await call(tools, "33a", "get_loan_quote", lambda r: r["offer"] and r["kfs_link"],
               lambda r: f"{r['lender']}: {r['amount']:,} for {r['tenure_months']} months at {r['apr_pct']}% APR",
               merchant_id="M_DEMO_MEENA")
    await call(tools, "33b", "log_gate_result", lambda r: r["result"] == "block", lambda r: r["action_id"],
               merchant_id="M_DEMO_MEENA", draft_kind="loan_offer", draft={"english": "Cross ₹1 lakh and get a loan"},
               result="block", rule_hits=[{"rule": "Compliance rules §3.2", "check": "credit linked to threshold"}],
               judge=None, evidence=["eval fixture"], playbook_ref="Compliance rules §3.2", line="grow")
    await call(tools, "33c", "escalate_to_human", lambda r: r["status"] == "queued", lambda r: r["action_id"],
               merchant_id="M_DEMO_SMALL", reasons=["Angry sentiment in a call (Autonomy policy §4)"],
               case_summary="smoke", evidence=["smoke"], playbook_ref="Autonomy policy §4", line="none")
    await call(tools, "34", "get_txn_daily", lambda r: r[-1]["date"] == "2026-11-02",
               lambda r: f"after advance_day(2): last day {r[-1]['date']}", merchant_id="M_DEMO_RAVI", days=3)
    human("35", "reset", admin.reset)


def report(t0: float, tools: set[str]) -> int:
    used = {r[1] for r in RESULTS}
    missing = sorted(tools - used)
    print(f"{'step':<5}{'tool':<24}{'result':<7}detail")
    print("-" * 120)
    for step, name, ok, det in RESULTS:
        print(f"{step:<5}{name:<24}{'PASS' if ok else 'FAIL':<7}{det}")
    failed = [r for r in RESULTS if not r[2]]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} steps passed · {len(tools) - len(missing)}/{len(tools)} agent tools "
          f"exercised{' (missing: ' + ', '.join(missing) + ')' if missing else ''} · {time.time() - t0:.0f}s")
    return 1 if failed or missing else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
