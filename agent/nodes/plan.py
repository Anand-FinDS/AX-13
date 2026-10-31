"""Map: for each case, retrieve the cited section and plan the actions in code.

The plan is deterministic: which tools, with which of the merchant's own numbers. The LLM only
writes message text later (agent/nodes/draft.py). Every action carries its evidence and section,
which the write tools require (Retention playbook §1.4).
"""
from __future__ import annotations

import asyncio
import calendar
import re
from datetime import date, timedelta
from functools import lru_cache

import yaml

from agent import retrieval
from agent.tools import call
from common.settings import REPO_ROOT

POLICY = yaml.safe_load((REPO_ROOT / "agent" / "policy.yaml").read_text(encoding="utf-8"))
ACT = POLICY["actions"]
# Evidence line that best describes each cause, used as the retrieval query.
CAUSE_HINT = {"device": r"offline|hardware", "network": r"network errors", "charge": r"unnotified", "fee": r"fee",
              "competitor": r"peers", "closed": r"no upi payments", "threshold": r"holdback",
              "activation": r"soundbox|low activity|faster than peers"}
LANG = {"en": "English", "hi": "Hindi", "kn": "Kannada", "ta": "Tamil"}


def inr(x: float) -> str:
    """₹ with Indian digit grouping, whole rupees: 134302.57 -> ₹1,34,303."""
    n = round(x)
    s = str(abs(n))
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        groups = []
        while head:
            groups.insert(0, head[-2:])
            head = head[:-2]
        s = ",".join(groups) + "," + tail
    return ("-₹" if n < 0 else "₹") + s


def nice_date(d: date) -> str:
    return f"{d:%a} {d.day} {d:%b}"


@lru_cache(maxsize=1)
def fee_templates() -> dict[str, str]:
    """The three §6.2 message templates, read from the playbook itself (estimate, variant A, variant B)."""
    text = retrieval.section("Retention playbook §6.2")["text"]
    quotes = [ln[2:].strip() for ln in text.splitlines() if ln.startswith("> ")]
    return dict(zip(("estimate", "A_exempt", "B_no_payments_above_2000"), quotes))


def fill(template: str, **values: str) -> str:
    out = template
    for k, v in values.items():
        out = out.replace("{" + k + "}", v)
    if re.search(r"\{\w+\}", out):
        raise ValueError(f"Unfilled placeholder in template: {out}")
    return out


async def _retrieve(case: dict, query: str) -> dict:
    hits = await asyncio.to_thread(retrieval.retrieve, query, None, 3, case["cause"])
    return {"ref": hits[0]["ref"], "title": hits[0]["title"], "text": hits[0]["text"],
            "safety_net": bool(hits[0].get("safety_net"))}


def _action(case: dict, kind: str, tool: str | None, args: dict, evidence: list[str], ref: str, **extra) -> dict:
    return {"case": case["key"], "kind": kind, "tool": tool, "line": case["line"], "playbook_ref": ref,
            "evidence": evidence, "args": args, "tier": "agent_alone", "status": "planned", **extra}


def _message(case, kind, purpose, facts, evidence, ref, section, gate=None, template=None, tool="send_message",
             max_words=None, **extra) -> dict:
    return _action(case, kind, tool, {"channel": ACT["channel"]}, evidence, ref, gate=gate,
                   draft_spec={"purpose": purpose, "facts": facts, "section": section, "template": template,
                               "max_words": max_words or ACT["message_max_words"]}, **extra)


async def plan_case(tools, st: dict, case: dict) -> tuple[dict, list[dict]]:
    """Returns (section used, actions) for one case."""
    s, prof, view = st["score"], st["profile"], st["view"]
    today = date.fromisoformat(st["sim_date"])
    nxt = today + timedelta(days=ACT["days_ahead"])
    owner = prof["owner_name"].split()[0]
    base = {"merchant": prof["name"], "owner_first_name": owner, "language": LANG.get(prof["language_pref"], "English")}
    ev = s["evidence"][:4] or [f"{case['line']} / {case['cause']} (score {s['churn']}/{s['growth']})"]
    cause = case["cause"]
    lm = (view["fee"].get("last_full_month") or {})
    month = date.fromisoformat(lm["month"]).strftime("%B %Y") if lm else None

    # ---- fee notice for merchants the fee does not touch (at go-live, once) ----
    if case["key"] == "notice:fee":
        sec = {**retrieval.section("Retention playbook §6.2"), "safety_net": False}
        return sec, [_notice(case, base, view, lm, month)]

    hint = next((e for e in s["evidence"] if re.search(CAUSE_HINT.get(cause, "^$"), e, re.IGNORECASE)), ev[0])
    sec = await _retrieve(case, f"{cause}: {hint}")
    ref = sec["ref"]

    if cause == "device":
        devs = s["features"].get("devices") or []
        dev = next((d for d in devs if d["fault_days_7d"] or d["offline_days_7d"]), devs[0] if devs else None)
        dtype = dev["device_type"] if dev else "soundbox"
        book = _action(case, "technician", "book_technician",
                       {"device_type": dtype, "preferred_date": nxt.isoformat(), "time_window": ACT["technician_window"]},
                       ev, ref)
        facts = {**base, "device": "Soundbox" if dtype == "soundbox" else "card machine (POS)",
                 "visit_date": nice_date(nxt), "time_window": ACT["technician_window"].replace("-", " to ")}
        msg = _message(case, "message", "The device has a fault. Tell the owner a technician visit is booked to fix or "
                       "swap it on the visit date in the time window. Until then customers can pay by scanning the "
                       "printed QR. Apologise briefly for the trouble.", facts, ev, ref, sec)
        return sec, [book, msg]

    if cause == "network":
        dev = next(iter(s["features"].get("devices") or []), {})
        facts = {**base, "peak_errors_per_day": dev.get("peak_errors_avg_7d"), "offpeak_errors_per_day": dev.get("offpeak_errors_avg_7d")}
        return sec, [_message(case, "message", "Payments fail at rush hours because of the mobile network, not the device. "
                              "Give the peak-hour guidance from the policy section. Say no technician is needed because "
                              "the device itself is fine.", facts, ev, ref, sec)]

    if cause == "charge":
        unnot = [x for x in view["deductions_30d"] if not x["notified"]]
        amount = round(sum(x["amount"] for x in unnot), 2)
        budget = await call(tools, "get_waiver_budget", zone_id=prof["zone_id"])
        within = amount <= budget["cap_per_merchant"] and amount <= budget["remaining"]
        what = ", ".join(f"{x['type'].replace('_', ' ')} of {inr(x['amount'])} on {nice_date(date.fromisoformat(x['date']))}"
                         for x in unnot)
        tier = "agent_alone" if within else "human_approves"
        waiver = _action(case, "refund", "grant_waiver", {"amount": amount, "reason": f"Refund of unnotified {what}"},
                         ev, ref, tier=tier, approval_type=None if within else "waiver",
                         tier_reason=None if within else f"Refund {inr(amount)} is above the per-merchant cap "
                         f"{inr(budget['cap_per_merchant'])} (Autonomy policy §3)")
        facts = {**base, "deduction": what, "refund_amount": inr(amount)}
        msg = _message(case, "message", "Explain the deduction plainly: what it was, the amount and the date. Say they "
                       "were not told in advance, and that the amount will be refunded.", facts, ev, ref, sec,
                       tier=tier, approval_type=waiver["approval_type"], tier_reason=waiver["tier_reason"])
        return sec, [waiver, msg]

    if cause == "fee" and not lm.get("is_fee_affected"):
        # Not affected, but dropping since go-live: confusion. The not-affected message (once) and a visit.
        variant = view["fee"]["not_affected_variant"]
        visit = _visit(prof, case, nxt, "retain", f"Reassure: {_short(hint)}. The fee does not apply to them "
                       f"({variant}); clear up the confusion", ev, ref)
        notice = [] if "notice:fee" in st["handled_cases"] else [_notice(case, base, view, lm, month)]
        return sec, notice + [visit]

    if cause == "fee":
        tpl = fill(fee_templates()["estimate"], value_above_2000=inr(lm["value_above_2000"])[1:],
                   est_monthly_fee=inr(lm["est_monthly_fee"])[1:])
        facts = {**base, "month": month, "value_above_2000": inr(lm["value_above_2000"]),
                 "est_monthly_fee": inr(lm["est_monthly_fee"])}
        msg = _message(case, "fee_estimate", "Explain the merchant's own estimated UPI fee.", facts, ev, ref, sec,
                       gate="fee_message", template=tpl)
        visit = _visit(prof, case, nxt, "retain", f"Retain: {_short(hint)}. Walk through est. fee "
                       f"{inr(lm['est_monthly_fee'])}/month on {inr(lm['value_above_2000'])} above ₹2,000 ({month})", ev, ref)
        return sec, [msg, visit]

    if cause == "competitor":
        return sec, [_visit(prof, case, nxt, "retain", f"Retain: {_short(hint)}. Ask what changed and what a rival "
                            "offered; compare honestly with their own charges", ev, ref)]

    if cause == "closed":
        return sec, [_message(case, "message", "There have been no payments for over two weeks. Ask kindly whether the "
                              "shop is open, closed for a while, or having trouble with payments, and ask them to reply "
                              "with a reopening date if they are away. No offers.", base, ev, ref, sec)]

    if cause == "threshold":
        comp = {**retrieval.section("Compliance rules §3.2"), "safety_net": False}
        holds = s["features"].get("holdback_months") or []
        h = holds[-1] if holds else None
        cash = None
        if h:
            y, m = map(int, h["month"].split("-"))
            days = calendar.monthrange(y, m)[1]
            k, r = h["crossed_day"], h["ratio"]
            pace = h["total"] / (k + r * (days - k))
            cash = round(pace * (days - k) * (1 - r) / 500) * 500
        hyp_fee = 0.004 * lm["value_above_2000"]
        facts = {**base, "month": month, "monthly_upi_value": inr(lm["monthly_upi_value"]),
                 "value_above_2000": inr(lm["value_above_2000"]), "fee_if_above_1_lakh": inr(hyp_fee),
                 "month_end_cash_estimate": inr(cash) if cash else None,
                 "fee_rule": "0.4% only on UPI payments above ₹2,000, capped at ₹300 per payment; payments of ₹2,000 "
                             "or less are never charged; monthly UPI under ₹1 lakh is exempt (proposed framework)",
                 "required_line": "Payments you take digitally are recorded. Recorded sales are the history lenders look "
                                  "at when they assess an application."}
        actions = [_message(case, "cost_comparison",
                            "Honest cost comparison for a merchant who stops taking UPI at month-end to stay under ₹1 "
                            "lakh. Cover these points in order, with the numbers from the facts: (1) last month's UPI "
                            "and that it is under ₹1 lakh, so no fee applies now; (2) if monthly UPI went above ₹1 lakh, "
                            "the fee would be about fee_if_above_1_lakh a month (use the words 'under the proposed framework'), because it "
                            "applies only to the value_above_2000 of payments above ₹2,000 and payments of ₹2,000 or less "
                            "are never charged; (3) our estimate of the sales taken in cash at month-end instead of UPI; "
                            "(4) the required line, exactly. Do not copy the message templates in the policy section; "
                            "they are for other cases. Do not tell them what to do. Promise nothing about loans.",
                            facts, ev, ref, sec, gate="cost_comparison", compliance_ref=comp["ref"])]
        loan = await _loan_action(tools, st, case, base, ev)
        return sec, actions + ([loan] if loan else [])

    # activation (Grow): Soundbox offer for QR-only merchants, otherwise a pitch visit
    if not prof["has_soundbox"]:
        cat = {**retrieval.section("Product catalogue §2"), "safety_net": False}
        facts = {**base, "avg_daily_upi_4w": s["features"].get("avg_daily_upi_28d"), "rental": "₹99–199 a month (illus.)"}
        offer = _message(case, "offer", "Offer a Soundbox: payments are announced out loud, so the owner does not need to "
                         "check the phone for each one. State the monthly rental. Do not say free.", facts, ev,
                         ref, cat, tool="make_offer")
        offer["args"].update(product="soundbox")
        return sec, [offer]
    stage = s["features"].get("activation_stage")
    brief = (f"Pitch: {_short(hint)}. Loan path reached; listen for growth plans (Product catalogue §4)"
             if stage == "steady_90d" else f"Pitch: {_short(hint)}. Next step on the activation ladder")
    return sec, [_visit(prof, case, nxt, "pitch", brief, ev, ref)]


def _notice(case, base, view, lm, month) -> dict:
    """§6.2 not-affected message, variant A (exempt) or B (no payments above ₹2,000), from last month's data."""
    sec = {**retrieval.section("Retention playbook §6.2"), "safety_net": False}
    variant = view["fee"]["not_affected_variant"]
    tpl = fee_templates()[variant]
    msg = fill(tpl, monthly_upi_value=inr(lm["monthly_upi_value"])[1:]) if variant == "A_exempt" else tpl
    ev = [(f"Fee status for {month}: not affected ({variant}); monthly UPI {inr(lm['monthly_upi_value'])}, "
          f"above ₹2,000: {inr(lm['value_above_2000'])}")]
    facts = {**base, "variant": variant, "monthly_upi_value": inr(lm["monthly_upi_value"])}
    return _message(case, "fee_notice", "Tell the merchant the new UPI fee does not apply to them.", facts, ev,
                    sec["ref"], sec, gate="fee_message", template=msg)


def _short(e: str, n: int = 70) -> str:
    e = e.split(" (")[0]
    return e if len(e) <= n else e[:n - 1] + "…"


def _visit(prof, case, d: date, stop_type: str, brief: str, ev, ref) -> dict:
    return _action(case, "visit", "plan_visit", {"agent_id": prof["assigned_agent_id"], "planned_date": d.isoformat(),
                                                 "stop_type": stop_type, "brief": brief[:200]}, ev, ref)


async def _loan_action(tools, st, case, base, ev) -> dict | None:
    """A loan draft for a merchant on the loan path (Product catalogue §1.2, §4), from the lender's own quote.
    Always needs RM approval (Compliance rules §5)."""
    if st["score"]["features"].get("activation_stage") != "steady_90d":
        return None
    today = date.fromisoformat(st["sim_date"])
    offers = await call(tools, "get_offers", merchant_id=st["merchant_id"])
    if any(o["product"] == "loan" and (today - date.fromisoformat(o["date"])).days < ACT["loan_cooldown_days"]
           for o in offers):
        return None
    q = await call(tools, "get_loan_quote", merchant_id=st["merchant_id"])
    if not q["offer"]:
        return None
    sec = {**retrieval.section("Product catalogue §4"), "safety_net": False}
    lender = q["lender"].split(" (")[0]
    facts = {**base, "lender": lender, "amount": inr(q["amount"]), "tenure": f"{q['tenure_months']} months",
             "apr": f"{q['apr_pct']:g}% APR", "processing_fee": q["processing_fee"], "prepayment": q["prepayment"],
             "late_payment": q["late_payment"], "kfs_link": q["kfs_link"],
             "valid_until": nice_date(date.fromisoformat(q["valid_until"])), "lender_basis": q["basis"]}
    ev = ev + [(f"Loan path: Soundbox in place, 90 days of steady recorded UPI; lender quote {q['quote_id']} "
               f"({lender}, {inr(q['amount'])})")]
    act = _message(case, "loan_offer", "Share the lender's in-principle loan offer (an offer, not an approval: never say approved or approval). Name the lender, amount, tenure, APR, "
                   "processing fee, prepayment terms, late-payment charge and the Key Fact Statement link, all exactly as "
                   "in the facts, and the date the offer is valid until. Say they can apply if they wish and the lender "
                   "makes the final decision. Do not mention the UPI fee or the ₹1 lakh limit. No pressure.",
                   facts, ev, sec["ref"], sec, gate="loan_offer", tool="make_offer", max_words=ACT["loan_max_words"],
                   tier="human_approves", approval_type="loan_offer", tier_reason="Loan offer: RM approves (Compliance rules §5)",
                   quote=q, compliance_ref="Compliance rules §1")
    act["args"].update(product="loan")
    return act
