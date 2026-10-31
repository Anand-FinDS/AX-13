"""Plans for the three scripted demo merchants, built from demo_merchants.yaml."""
from __future__ import annotations

import numpy as np

from data.generators.context import Calendar, ramp
from data.generators.merchants import LARGE_MEAN_FACTOR, Plan

SCRIPTED_NOISE = 0.04


def build_demo_plans(cfg: dict, cal: Calendar) -> list[Plan]:
    return [_plan(mid, spec, cal) for mid, spec in cfg["demo_merchants"]["merchants"].items()]


def _plan(mid: str, spec: dict, cal: Calendar) -> Plan:
    prof = spec["profile"]
    above = prof.get("monthly_value_above_2000", 0)
    small_ticket = prof.get("avg_ticket_small", prof.get("avg_ticket"))
    small_count = (prof["monthly_upi_value"] - above) / 30 / small_ticket
    large_count = above / 30 / prof["avg_ticket_above_2000"] if above else 0.0
    total = small_count + large_count
    products = spec["products"]
    plan = Plan(
        merchant_id=mid, scripted=True, archetype="scripted", category=spec["category"], zone_id=spec["zone_id"],
        has_soundbox=products["soundbox"], has_pos=products["pos"],
        base_count=total, avg_small=small_ticket,
        large_share=large_count / total,
        large_median=prof["avg_ticket_above_2000"] / LARGE_MEAN_FACTOR if above else 0.0,
        very_large_prob=0.0, noise_sigma=SCRIPTED_NOISE, mult=np.ones(cal.n),
        offline=np.zeros(cal.n, bool), hw_fault=np.zeros(cal.n, bool),
        label=dict(spec["label"]),
        fixed={
            "name": spec["name"], "owner_name": spec["owner_name"], "pin_code": spec["pin_code"],
            "language_pref": spec["language_pref"], "onboard_months": spec["onboard_months_before_start"],
            "soundbox_rent_month": float(spec.get("soundbox_rent_month", 0)),
        },
    )
    starts = []
    for ev in spec["events"]:
        t = ev["type"]
        if t in ("volume_ramp", "device_offline"):
            starts.append(cal.at(ev["anchor"], ev["from"]))
        if t == "volume_ramp":
            ramp(plan.mult, cal.at(ev["anchor"], ev["from"]), cal.at(ev["anchor"], ev["to_day"]), ev["to"])
        elif t == "volume_spike":
            plan.mult[cal.at(ev["anchor"], ev["from"]): cal.at(ev["anchor"], ev["to_day"]) + 1] *= ev["mult"]
        elif t == "device_offline":
            sl = slice(cal.at(ev["anchor"], ev["from"]), cal.at(ev["anchor"], ev["to_day"]) + 1)
            plan.offline[sl] = True
            plan.hw_fault[sl] = ev.get("hardware_fault", False)
        elif t == "ticket":
            plan.tickets.append({"idx": cal.at(ev["anchor"], ev["day"]), "category": ev["category"],
                                 "channel": ev["channel"], "status": ev["status"], "summary": ev["summary"],
                                 "sentiment": ev["sentiment"]})
        elif t == "offer":
            plan.offers.append({"idx": cal.at(ev["anchor"], ev["day"]), "product": ev["product"],
                                "channel": ev["channel"], "response": ev["response"]})
        elif t == "threshold_hold":
            plan.hold = {"months": ev["months"], "trigger": float(ev["hold_trigger"]),
                         "drop": [ev["drop_after_trigger"]] * 2, "band": list(ev["monthly_band"])}
            plan.params = {"hold_trigger": float(ev["hold_trigger"])}
        else:
            raise ValueError(f"{mid}: unknown event type {t}")
    plan.onset_idx = min(starts) if starts else None
    return plan
