"""The two gates.

Escalation gate (rules, Autonomy policy §2 and §4): decides the tier of a case before anything is
drafted. Angry or disputed cases and low-confidence decisions go to a human with no action;
high-value merchants get every action prepared for approval.

Compliance gate (Compliance rules §7): on every fee message, cost comparison and loan draft.
Rules in code first; a draft that passes them is read by the LLM judge (AX13_MODEL_FALLBACK).
"""
from __future__ import annotations

import json
import re

from agent.nodes.plan import POLICY, inr
from common.llm import complete
from common.settings import REPO_ROOT

CONF, ESC = POLICY["confidence"], POLICY["escalation"]
JUDGE_PROMPT = (REPO_ROOT / "agent" / "prompts" / "judge.md").read_text(encoding="utf-8")


# ---------------------------------------------------------------- escalation gate

def confidence(score: dict, line: str, thresholds: dict) -> float:
    """Decision confidence from the margin over the line threshold (Autonomy policy §2.1)."""
    value = score["churn"] if line == "save" else score["growth"]
    margin = value - thresholds[line]
    return round(min(1.0, max(0.0, CONF["base"] + (1 - CONF["base"]) * margin / CONF["full_margin"])), 2)


def escalation(st: dict) -> dict:
    """{'human_only': [reasons], 'human_approves': [reasons]}, each reason citing its section."""
    tickets, prof = st["view"]["open_tickets"], st["profile"]
    only, approves = [], []
    for t in tickets:
        if t["sentiment"] in ESC["angry_sentiments"]:
            only.append(f"Angry sentiment in open ticket {t['ticket_id']}: \"{t['summary']}\" (Autonomy policy §4)")
        elif t["category"] in ESC["dispute_categories"] and t["sentiment"] in ESC["dispute_sentiments"]:
            only.append(f"Merchant disputes a charge: ticket {t['ticket_id']} ({t['sentiment']}) "
                        f"(Autonomy policy §4, Retention playbook §6.3)")
    for c in st["cases"]:
        if c.get("confidence") is not None and c["confidence"] < CONF["min"]:
            only.append(f"Decision confidence {c['confidence']:.2f} for {c['key']} is below {CONF['min']} "
                        f"(Autonomy policy §2.1)")
    if prof["merchant_value_tier"] == ESC["high_value_tier"]:
        approves.append("High-value merchant: every action needs approval (Autonomy policy §1.3, §4)")
    return {"human_only": only, "human_approves": approves}


# ---------------------------------------------------------------- compliance gate: rules

ELIGIBILITY = re.compile(r"\b(?:you are|you're|you'll be|you will be|will be|become|becomes|are now)\s+eligible\b|"
                         r"\beligible for\b|\b(?:pre-?|in-principle )?approv(?:al|ed)\b|\bguarantee[ds]?\b|\byou will (?:get|receive) ₹", re.IGNORECASE)
PRESSURE = re.compile(r"\b(?:offer ends|ends today|last chance|today only|hurry|limited[- ]time|act now|don't miss|"
                      r"grab it)\b", re.IGNORECASE)
CREDIT = re.compile(r"\b(?:loan|credit|lender|borrow)", re.IGNORECASE)
THRESHOLD = re.compile(r"₹\s?1 lakh|1,00,000|\bone lakh\b|\b1 lakh\b|threshold|\bcross(?:es|ing)?\b|UPI fee|\bthe fee\b", re.IGNORECASE)


# "₹1 lakh" in the merchant's script: the number stays in digits, the word "lakh" is translated.
LAKH_LOCAL = re.compile(r"₹\s?1\s?(?:lakh|lac|ಲಕ್ಷ|लाख|லட்சம்)")


def _sentences(text: str) -> list[str]:
    return [x for x in re.split(r"(?<=[.!?।])\s+", text) if x.strip()]


def _need(hits: list, rule: str, check: str, haystack: str, *needles: str) -> None:
    missing = [n for n in needles if n and n.lower() not in haystack.lower()]
    if missing:
        hits.append({"rule": rule, "check": check, "detail": "missing: " + ", ".join(missing)})


def check_rules(kind: str, draft: dict, facts: dict) -> list[dict]:
    """Rule hits for one draft (Compliance rules §7.1). Empty list = rules pass."""
    en, local = draft.get("english") or "", draft.get("text") or ""
    local = LAKH_LOCAL.sub("₹1 lakh", local)
    hits: list[dict] = []
    for name, txt in (("English", en), ("message", local)):
        if m := ELIGIBILITY.search(txt):
            hits.append({"rule": "Compliance rules §2", "check": "eligibility promise", "detail": f"{name}: \"{m.group(0)}\""})
        if m := PRESSURE.search(txt):
            hits.append({"rule": "Compliance rules §3.1", "check": "pressure / false urgency", "detail": f"{name}: \"{m.group(0)}\""})
        for sent in _sentences(txt):
            if CREDIT.search(sent) and THRESHOLD.search(sent):
                hits.append({"rule": "Compliance rules §3.2", "check": "credit linked to the fee or ₹1 lakh threshold",
                             "detail": f"{name}: \"{sent.strip()}\""})
                break

    if kind == "fee_message":
        _need(hits, "Compliance rules §6", "framed as proposed", en, "proposed")
        if "est_monthly_fee" in facts:
            for t in (en, local):
                _need(hits, "Compliance rules §6", "fee figures and own numbers", t, "0.4%", "₹2,000", "₹300", "₹1 lakh",
                      facts["value_above_2000"], facts["est_monthly_fee"])
        elif facts.get("variant") == "A_exempt":
            for t in (en, local):
                _need(hits, "Compliance rules §6", "exemption and own numbers", t, "₹1 lakh", facts["monthly_upi_value"])
        else:
            for t in (en, local):
                _need(hits, "Compliance rules §6", "₹2,000 rule", t, "₹2,000")
    elif kind == "cost_comparison":
        _need(hits, "Compliance rules §6", "framed as proposed", en, "proposed")
        for t in (en, local):
            _need(hits, "Compliance rules §3.3", "comparison backed by own numbers", t, facts["fee_if_above_1_lakh"],
                  facts.get("month_end_cash_estimate"))
    elif kind == "loan_offer":
        _need(hits, "Compliance rules §1", "lender name", en + " " + local, facts["lender"].split(" Ltd")[0])
        for t in (en, local):
            _need(hits, "Compliance rules §1", "amount and KFS link", t, facts["amount"], facts["kfs_link"])
        _need(hits, "Compliance rules §1", "tenure, APR and charges", en, facts["tenure"],
              facts["apr"].split(" ")[0], "APR", "processing", "prepay", "late")
        for name, txt in (("English", en), ("message", local)):
            if m := THRESHOLD.search(txt):
                hits.append({"rule": "Compliance rules §3.2", "check": "loan draft mentions the fee or threshold",
                             "detail": f"{name}: \"{m.group(0)}\""})
    # one hit per rule+check is enough to explain a block
    seen, out = set(), []
    for h in hits:
        if (h["rule"], h["check"]) not in seen:
            seen.add((h["rule"], h["check"]))
            out.append(h)
    return out


# ---------------------------------------------------------------- compliance gate: judge

def judge(kind: str, draft: dict, facts: dict) -> dict:
    """LLM judge for softer problems (Compliance rules §7.2). Returns {verdict, rule, reason}."""
    user = json.dumps({"draft_kind": kind, "english": draft.get("english"), "message": draft.get("text"),
                       "facts": facts}, ensure_ascii=False, indent=1)
    out = complete([{"role": "system", "content": JUDGE_PROMPT}, {"role": "user", "content": user}],
                   role="fallback", json_mode=True)
    verdict = str(out.get("verdict", "unsure")).lower()
    return {"verdict": verdict if verdict in ("pass", "block", "unsure") else "unsure",
            "rule": out.get("rule"), "reason": out.get("reason")}


def gate(kind: str, draft: dict, facts: dict, use_judge: bool = True) -> dict:
    """Full gate: rules first; the judge only reads drafts that pass them."""
    hits = check_rules(kind, draft, facts)
    if hits:
        return {"result": "block", "rule_hits": hits, "judge": None,
                "refs": sorted({h["rule"] for h in hits}, key=_ref_key)}
    j = judge(kind, draft, facts) if use_judge else {"verdict": "pass", "rule": None, "reason": "judge skipped"}
    return {"result": j["verdict"], "rule_hits": [], "judge": j, "refs": [j["rule"]] if j.get("rule") else []}


def _ref_key(ref: str) -> tuple:
    return tuple(int(x) for x in ref.split("§")[1].split("."))


__all__ = ["check_rules", "confidence", "escalation", "gate", "inr", "judge"]
