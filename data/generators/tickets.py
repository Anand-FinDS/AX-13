"""tickets: background noise plus tickets planted by archetypes and scripted events.

A ticket whose natural resolution falls after D0, and every future ticket, stays open:
resolving them is AX-13's job. MCP servers filter on opened_at <= current date.
"""
from __future__ import annotations

from datetime import datetime, time, timedelta

import numpy as np

from data.generators.context import Calendar, rng_for
from data.generators.merchants import Plan

TEMPLATES = {
    "device": {
        "en": ["Soundbox not announcing payments since {weekday}",
               "Soundbox showing offline, payments not announced",
               "Soundbox keeps restarting, customers unsure if payment received"],
        "hi": ["Soundbox bol nahi raha, payment ka pata nahi chal raha",
               "{weekday} se soundbox band hai"],
        "kn": ["Soundbox kelsa maadtilla, payment announce aagtilla"],
    },
    "network": {
        "en": ["Payments failing during lunch and evening rush, money debited from customers",
               "Rush-hour payments stuck or failing, customers showing debit SMS"],
        "hi": ["Rush time pe payment fail ho raha, customer ka paisa kat gaya"],
        "kn": ["Rush time alli payment fail aagtide, customer duddu cut aagide"],
    },
    "failed_txn": {
        "en": ["Customer money debited but payment shows failed", "Failed payment not reversed to customer yet"],
        "hi": ["Customer ka paisa kat gaya par payment fail dikha raha"],
        "kn": ["Customer duddu cut aagide, payment fail antha torisuttide"],
    },
    "settlement": {
        "en": ["Settlement not received for yesterday", "Settlement amount less than expected"],
        "hi": ["Kal ka settlement nahi aaya"],
        "kn": ["Ninne settlement banda illa"],
    },
    "charge": {
        "en": ["Unknown deduction of Rs {amount} from settlement, no message received",
               "Rs {amount} maintenance charge cut without informing, want refund"],
        "hi": ["Bina bataye Rs {amount} kaat liya, refund chahiye"],
        "kn": ["Heladene Rs {amount} cut maadidaare, refund beku"],
    },
    "competitor": {
        "en": ["Asked about charges; says other company offered free device",
               "Merchant says another app is giving lower charges"],
        "hi": ["Doosri company free machine de rahi hai bol rahe"],
        "kn": ["Bere company free device kodtini antidaare"],
    },
    "other": {
        "en": ["Need help updating bank account", "Want to change registered mobile number",
               "Query about QR standee replacement"],
        "hi": ["Bank account badalna hai"],
        "kn": ["Bank account change maadbeku"],
    },
}
TICKET_CATEGORY = {"network": "failed_txn", "competitor": "other"}
SENTIMENT_BG = {"calm": 0.6, "frustrated": 0.35, "angry": 0.05}


def _summary(template: str, lang: str, r: np.random.Generator, day, amount: float | None) -> str:
    pool = TEMPLATES[template].get(lang) or TEMPLATES[template]["en"]
    text = str(r.choice(pool))
    return text.format(weekday=day.strftime("%A"), amount=f"{amount:,.0f}" if amount else "")


def generate_tickets(plan: Plan, cal: Calendar, cfg: dict, seed: int) -> list[dict]:
    tk = cfg["sim_config"]["tickets"]
    r = rng_for(seed, plan.merchant_id, "tickets")
    planted = [{**t, "planted": True} for t in plan.tickets]
    if not plan.scripted:
        last = plan.closed_from if plan.closed_from is not None else cal.n
        for _ in range(r.poisson(tk["background_rate_per_180d"] * cal.n / 180)):
            cats = list(tk["background_categories"])
            p = np.array(list(tk["background_categories"].values()))
            planted.append({"idx": int(r.integers(0, max(last, 1))), "category": str(r.choice(cats, p=p / p.sum())),
                            "sentiment": str(r.choice(list(SENTIMENT_BG), p=list(SENTIMENT_BG.values())))})

    out = []
    channels, ch_p = list(tk["channel_mix"]), np.array(list(tk["channel_mix"].values()))
    for t in planted:
        i = t["idx"]
        if i >= cal.n:
            continue
        day = cal.date(i)
        opened = datetime.combine(day, time(9)) + timedelta(minutes=int(r.integers(0, 11 * 60)))
        if "summary" in t:
            summary = t["summary"]
        else:
            lang = "en" if r.random() >= tk["non_english_summary_share"] else (
                "hi" if plan.fixed.get("language_pref") == "hi" or r.random() < 0.4 else "kn")
            summary = _summary(t.get("template", t["category"]), lang, r, day, t.get("amount"))
        if t.get("status"):
            status, resolved = t["status"], None
        else:
            lo, hi = tk["planted_resolve_after_days" if t.get("planted") else "resolve_after_days"]
            res_day = max(i + int(r.integers(lo, hi + 1)), t.get("resolve_not_before", 0))
            if t.get("ongoing"):
                res_day = cal.n
            if i > cal.i_d0:
                status, resolved = "open", None
            elif res_day > cal.i_d0:
                status, resolved = ("in_progress" if r.random() < 0.5 else "open"), None
            else:
                status = "resolved"
                resolved = datetime.combine(cal.date(res_day), time(10)) + timedelta(minutes=int(r.integers(0, 8 * 60)))
        out.append({
            "merchant_id": plan.merchant_id,
            "opened_at": opened,
            "resolved_at": resolved,
            "channel": t.get("channel") or str(r.choice(channels, p=ch_p / ch_p.sum())),
            "category": TICKET_CATEGORY.get(t["category"], t["category"]),
            "status": status,
            "summary": summary,
            "sentiment": t["sentiment"],
        })
    return out
