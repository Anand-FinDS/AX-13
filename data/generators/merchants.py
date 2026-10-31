"""Merchant plans (hidden generation parameters per merchant) and the merchants table.

A Plan holds everything the table generators need: base volume, ticket mix, the daily
archetype multiplier, device outages, planted tickets/deductions/offers and the hidden label.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from faker import Faker

from data.generators.context import Calendar, lognormal_from_range, ramp, rng_for

LARGE_SIGMA = 0.35                      # spread of individual payments above 2,000
LARGE_MEAN_FACTOR = math.exp(LARGE_SIGMA ** 2 / 2)
SMALL_TICKET_CAP = 1500                 # average below-2,000 ticket never exceeds this


@dataclass
class Plan:
    merchant_id: str
    scripted: bool
    archetype: str
    category: str
    zone_id: str
    has_soundbox: bool
    has_pos: bool
    base_count: float
    avg_small: float
    large_share: float
    large_median: float
    very_large_prob: float
    noise_sigma: float
    mult: np.ndarray
    offline: np.ndarray
    hw_fault: np.ndarray
    active_prob: float | None = None
    network_from: int | None = None
    closed_from: int | None = None
    onset_idx: int | None = None
    hold: dict | None = None
    params: dict = field(default_factory=dict)
    tickets: list[dict] = field(default_factory=list)
    deductions: list[dict] = field(default_factory=list)
    offers: list[dict] = field(default_factory=list)
    label: dict = field(default_factory=dict)
    fixed: dict = field(default_factory=dict)   # name, owner, pin, language, onboard, rent ...

    def expected_monthly_value(self) -> float:
        per_txn = self.avg_small * (1 - self.large_share) + self.large_median * LARGE_MEAN_FACTOR * self.large_share
        return 30 * self.base_count * per_txn


def largest_remainder(shares: dict[str, float], n: int) -> dict[str, int]:
    raw = {k: v * n for k, v in shares.items()}
    out = {k: math.floor(x) for k, x in raw.items()}
    for k in sorted(raw, key=lambda k: raw[k] - out[k], reverse=True)[: n - sum(out.values())]:
        out[k] += 1
    return out


def _onset_flags(arch_cfg: dict, n: int, r: np.random.Generator) -> list[str]:
    """Exact split of an archetype's merchants into future / recent / older onsets, shuffled."""
    on = arch_cfg.get("onset")
    if not on:
        return ["none"] * n
    n_future = round(on.get("future_share", 0) * n)
    n_recent = round(on.get("recent_share", 0) * (n - n_future))
    flags = ["future"] * n_future + ["recent"] * n_recent + ["older"] * (n - n_future - n_recent)
    r.shuffle(flags)
    return flags


def _draw_onset(flag: str, on: dict, cal: Calendar, r: np.random.Generator) -> int:
    if flag == "future":
        lo, hi = on["future_range"]
    elif flag == "recent":
        lo, hi = -on["recent_days"], min(on["history_range"][1], 0)
    else:
        lo, hi = on["history_range"]
    return cal.i_d0 + int(r.integers(lo, hi + 1))


def build_generic_plans(cfg: dict, cal: Calendar, seed: int, fake: Faker) -> list[Plan]:
    sim, cats, archs = cfg["sim_config"], cfg["categories"]["categories"], cfg["archetypes"]["archetypes"]
    n = sim["scale"]["merchants"] - len(cfg["demo_merchants"]["merchants"])
    master = rng_for(seed, "assign")

    counts = largest_remainder({k: v["share"] for k, v in archs.items()}, n)
    arch_list = [a for a, c in counts.items() for _ in range(c)]
    master.shuffle(arch_list)
    flags = {a: iter(_onset_flags(archs[a], c, master)) for a, c in counts.items()}
    n_exempt_droppers = round(archs["post_fee_dropper"]["params"]["exempt_share"] * counts["post_fee_dropper"])
    dropper_exempt = iter([True] * n_exempt_droppers + [False] * (counts["post_fee_dropper"] - n_exempt_droppers))

    zone_ids = list(sim["merchants"]["zone_split"])
    zone_p = np.array(list(sim["merchants"]["zone_split"].values()))
    plans = []
    for i, arch in enumerate(arch_list):
        mid = f"M{i + 1:04d}"
        a = archs[arch]
        allowed = a.get("categories") or list(cats)
        p = np.array([cats[c]["share"] for c in allowed])
        category = str(master.choice(allowed, p=p / p.sum()))
        zone_id = str(master.choice(zone_ids, p=zone_p))
        plan = _base_plan(mid, arch, category, zone_id, cfg, cal, seed)
        _apply_archetype(plan, a, next(flags[arch]), cfg, cal, seed,
                         exempt=next(dropper_exempt) if arch == "post_fee_dropper" else False)
        c = cats[category]
        zone = next(z for z in sim["zones"] if z["zone_id"] == zone_id)
        r = rng_for(seed, mid, "profile")
        plan.fixed = {
            "name": f"{fake.last_name()} {r.choice(c['shop_words'])}",
            "owner_name": f"{fake.first_name()} {fake.last_name()}",
            "pin_code": str(r.choice(zone["pin_codes"])),
            "language_pref": str(r.choice(zone["languages"], p=_lang_weights(zone["languages"]))),
            "onboard_months": int(r.integers(*sim["merchants"]["onboard_months_before_start"], endpoint=True)),
            "soundbox_rent_month": float(r.choice(np.arange(99, 200, 10))) if plan.has_soundbox else 0.0,
        }
        plans.append(plan)
    return plans


def _lang_weights(langs: list[str]) -> np.ndarray:
    w = np.array([0.5 if lang == "kn" else 0.5 / (len(langs) - 1) for lang in langs])
    return w / w.sum()


def _base_plan(mid: str, arch: str, category: str, zone_id: str, cfg: dict, cal: Calendar, seed: int) -> Plan:
    sim, c = cfg["sim_config"], cfg["categories"]["categories"][category]
    a = cfg["archetypes"]["archetypes"][arch]
    r = rng_for(seed, mid, "plan")
    req = a.get("requires", {})
    soundbox = r.random() < c["products"]["soundbox"]
    pos = r.random() < c["products"]["pos"]
    soundbox = soundbox or req.get("soundbox", False)
    large = (r.random() < c["large_ticket"]["merchant_prob"]) or req.get("large_ticket", False)
    sigma = a.get("params", {}).get("noise_sigma") or float(r.uniform(*sim["txn"]["daily_noise_sigma"]))
    return Plan(
        merchant_id=mid, scripted=False, archetype=arch, category=category, zone_id=zone_id,
        has_soundbox=bool(soundbox), has_pos=bool(pos),
        base_count=lognormal_from_range(r, *c["daily_upi_count"]),
        avg_small=min(lognormal_from_range(r, *c["ticket"]), SMALL_TICKET_CAP),
        large_share=c["large_ticket"]["txn_share"] if large else 0.0,
        large_median=c["large_ticket"]["median"] if large else 0.0,
        very_large_prob=c.get("very_large_per_day_prob", 0.0) if large else 0.0,
        noise_sigma=sigma, mult=np.ones(cal.n),
        offline=np.zeros(cal.n, bool), hw_fault=np.zeros(cal.n, bool),
    )


def _apply_archetype(plan: Plan, a: dict, flag: str, cfg: dict, cal: Calendar, seed: int, exempt: bool) -> None:
    r = rng_for(seed, plan.merchant_id, "archetype")
    pr = a.get("params", {})
    plan.label = dict(a["label"])
    arch = plan.archetype
    onset = _draw_onset(flag, a["onset"], cal, r) if a.get("onset") else None
    plan.onset_idx = onset

    if arch == "festive_grower":
        extra = float(r.uniform(*pr["extra_lift_at_peak"]))
        fs, fp, fe = (cal.idx(d) for d in cal.festive_dates)
        shape = np.zeros(cal.n)
        for i in range(cal.n):
            if fs <= i <= fp:
                shape[i] = extra * (i - fs) / (fp - fs)
            elif fp < i <= fe:
                shape[i] = extra * (1 - (1 - pr["stays_elevated_share"]) * (i - fp) / (fe - fp))
            elif i > fe:
                shape[i] = extra * pr["stays_elevated_share"]
        plan.mult *= 1 + shape
        plan.params = {"extra_lift": round(extra, 3)}
        if plan.has_soundbox:
            plan.label.update(expected_action="loan_pitch", expected_escalation=True)
        else:
            plan.label.update(expected_action="soundbox_pitch")

    elif arch == "slow_decliner":
        decline = float(r.uniform(*pr["decline"]))
        weeks = int(r.integers(pr["decline_weeks"][0], pr["decline_weeks"][1] + 1))
        ramp(plan.mult, onset, onset + 7 * weeks - 1, 1 - decline)
        plan.params = {"decline": round(decline, 3), "weeks": weeks}
        if r.random() < pr["competitor_note_prob"]:
            plan.tickets.append({"idx": onset + int(r.integers(7, 22)), "category": "other",
                                 "template": "competitor", "sentiment": "calm"})

    elif arch == "device_fault":
        drop = float(r.uniform(*pr["drop"]))
        days = int(r.integers(pr["offline_days"][0], pr["offline_days"][1] + 1))
        end = min(onset + days, cal.n)
        plan.offline[onset:end] = True
        plan.hw_fault[onset:end] = True
        plan.mult[onset:end] *= 1 - drop
        plan.tickets.append({"idx": onset + int(r.integers(pr["ticket_lag_days"][0], pr["ticket_lag_days"][1] + 1)),
                             "category": "device", "template": "device", "resolve_not_before": onset + days,
                             "sentiment": "frustrated" if r.random() < 0.6 else "calm"})
        plan.params = {"drop": round(drop, 3), "offline_days": days}
        if onset + days - 1 < cal.i_d0:
            plan.label.update(expected_line="none", expected_action="watchlist")
            plan.label["notes"] = "Fault recovered before D0."

    elif arch == "network_not_device":
        plan.network_from = onset
        plan.params = {
            "peak_dip": float(r.uniform(*pr["peak_dip"])),
            "peak_errors_lambda": float(r.uniform(*pr["peak_errors_lambda"])),
            "offpeak_errors_lambda": pr["offpeak_errors_lambda"],
            "peak_failed_rate": float(r.uniform(*pr["peak_failed_rate"])),
            "peak_failed_debited_share": float(r.uniform(*pr["peak_failed_debited_share"])),
        }
        if r.random() < 0.6:
            plan.tickets.append({"idx": onset + int(r.integers(2, 7)), "category": "failed_txn",
                                 "template": "network", "sentiment": "frustrated", "ongoing": True})

    elif arch == "threshold_holder":
        plan.very_large_prob = 0.0
        trigger = float(r.uniform(*pr["hold_trigger"]))
        plan.hold = {"months": "all", "trigger": trigger,
                     "drop": list(pr["drop_after_trigger"]), "band": list(pr["monthly_target"])}
        # Un-held pace ~ trigger per month; the hold rescales each month around it.
        plan.base_count *= trigger / plan.expected_monthly_value()
        plan.params = {"hold_trigger": round(trigger, 2)}

    elif arch == "low_active":
        plan.base_count = float(r.uniform(*pr["daily_upi_count"]))
        plan.active_prob = float(r.uniform(*pr["active_day_prob"]))
        plan.has_soundbox = bool(r.random() < pr["soundbox_prob"])
        plan.has_pos = False
        plan.large_share = plan.large_median = plan.very_large_prob = 0.0

    elif arch == "closed_shop":
        closed = cal.i_d0 - int(r.integers(pr["closed_days_before_d0"][0], pr["closed_days_before_d0"][1] + 1))
        plan.closed_from = plan.onset_idx = closed
        plan.mult[closed:] = 0.0
        plan.offline[closed:] = True

    elif arch == "charge_complainer":
        amount = float(r.choice([3999, 4999, 5999, 6999, 7999]))
        lag = int(r.integers(pr["ticket_lag_days"][0], pr["ticket_lag_days"][1] + 1))
        angry = r.random() < pr["angry_prob"]
        plan.deductions.append({"idx": onset, "type": pr["charge_type"], "amount": amount, "notified": False})
        plan.tickets.append({"idx": onset + lag, "category": "charge", "template": "charge", "amount": amount,
                             "sentiment": "angry" if angry else "frustrated"})
        plan.mult[onset + lag:] *= 1 - float(r.uniform(*pr["post_dip"]))
        plan.params = {"amount": amount, "ticket_lag": lag}
        plan.label["expected_escalation"] = bool(angry)

    elif arch == "post_fee_dropper":
        start = cal.i_g0 + int(r.integers(pr["onset_after_go_live_days"][0], pr["onset_after_go_live_days"][1] + 1))
        drop = float(r.uniform(*pr["drop"]))
        ramp(plan.mult, start, start + pr["ramp_days"] - 1, 1 - drop)
        plan.onset_idx = start
        plan.very_large_prob = 0.0 if exempt else plan.very_large_prob
        if exempt:
            plan.base_count *= float(r.uniform(*pr["exempt_monthly_upi"])) / plan.expected_monthly_value()
            plan.label = dict(a["label_exempt"])
            plan.label["notes"] = "Exempt merchant dropping from confusion about the fee."
        else:
            c = cfg["categories"]["categories"][plan.category]
            plan.large_share, plan.large_median = c["large_ticket"]["txn_share"], c["large_ticket"]["median"]
            floor = pr["affected_min_monthly_upi"]
            if plan.expected_monthly_value() < floor:
                plan.base_count *= floor / plan.expected_monthly_value()
        plan.params = {"drop": round(drop, 3), "exempt": exempt}


def merchants_table(plans: list[Plan], txn_hist: pd.DataFrame, agents: pd.DataFrame,
                    cfg: dict, cal: Calendar, seed: int) -> pd.DataFrame:
    sim = cfg["sim_config"]
    tiers = sim["merchants"]["value_tier_monthly_upi"]
    last90 = txn_hist[txn_hist["date"] > cal.date(cal.i_d0 - 90)]
    monthly = last90.groupby("merchant_id")["upi_value"].sum() / 3
    rows = []
    for p in plans:
        zone = next(z for z in sim["zones"] if z["zone_id"] == p.zone_id)
        r = rng_for(seed, p.merchant_id, "location")
        lat = float(r.uniform(*zone["bbox"]["lat"]))
        lng = float(r.uniform(*zone["bbox"]["lng"]))
        za = agents[agents["zone_id"] == p.zone_id]
        nearest = za.iloc[int(np.argmin((za["home_lat"] - lat) ** 2 + (za["home_lng"] - lng) ** 2))]["agent_id"]
        m = float(monthly.get(p.merchant_id, 0.0))
        tier = "low" if m < tiers["low_below"] else ("high" if m >= tiers["high_from"] else "mid")
        onboard = (pd.Timestamp(cal.d0) - pd.DateOffset(months=p.fixed["onboard_months"])).date()
        rows.append({
            "merchant_id": p.merchant_id,
            "name": p.fixed["name"],
            "owner_name": p.fixed["owner_name"],
            "category": p.category,
            "zone_id": p.zone_id,
            "pin_code": p.fixed["pin_code"],
            "lat": round(lat, 6),
            "lng": round(lng, 6),
            "language_pref": p.fixed["language_pref"],
            "onboard_date": onboard,
            "has_qr": True,
            "has_soundbox": p.has_soundbox,
            "has_pos": p.has_pos,
            "soundbox_rent_month": p.fixed["soundbox_rent_month"] if p.has_soundbox else 0.0,
            "assigned_agent_id": nearest,
            "phone_masked": f"+91-XXXXXX-{int(r.integers(1000, 10000))}",
            "merchant_value_tier": tier,
        })
    return pd.DataFrame(rows)
