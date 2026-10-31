"""txn_daily / txn_daily_future: one row per merchant per day.

volume = base x weekly x festive x seasonal x archetype multiplier x noise; counts are Poisson.
Each day's payments split into small (< 2,000) and large (> 2,000); large ones are drawn
individually so value_above_2000 is a true sum.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from data.generators.context import Calendar, rng_for
from data.generators.merchants import LARGE_SIGMA, Plan

LARGE_FLOOR = 2001.0


def _seasonal(cat: dict, cal: Calendar) -> np.ndarray:
    out = np.ones(cal.n)
    for b in cat.get("seasonal_bumps", []):
        out[(cal.dates >= pd.Timestamp(b["start"])) & (cal.dates <= pd.Timestamp(b["end"]))] *= b["mult"]
    return out


def generate_txn(plan: Plan, cal: Calendar, cfg: dict, seed: int) -> tuple[pd.DataFrame, list[dict], dict]:
    """Returns (daily rows, very-large payments, threshold-hold info per month)."""
    sim, cat = cfg["sim_config"], cfg["categories"]["categories"][plan.category]
    tx = sim["txn"]
    r = rng_for(seed, plan.merchant_id, "txn")
    n = cal.n

    weekly = np.asarray(cat["weekly"], float)
    weekly /= weekly.mean()
    lam = plan.base_count * weekly[cal.dow] * (1 + cat["festive_lift"] * cal.festive) * _seasonal(cat, cal) * plan.mult
    lam = lam * np.clip(r.normal(1, plan.noise_sigma, n), 0.3, None)

    peak = np.clip(r.uniform(*tx["peak_hour_share"]) + r.normal(0, 0.02, n), 0.3, 0.75)
    net = np.zeros(n, bool)
    if plan.network_from is not None:
        net = np.arange(n) >= plan.network_from
        dip = plan.params["peak_dip"]
        lam = np.where(net, lam * (1 - dip * peak), lam)
        peak = np.where(net, peak * (1 - dip) / (1 - dip * peak), peak)

    if plan.scripted:
        # Scripted merchants: deterministic counts so the story reads cleanly; fractional
        # large payments carried forward day to day.
        count = np.rint(lam).astype(int)
        c_large = np.minimum(np.diff(np.floor(np.cumsum(count * plan.large_share) + 0.5), prepend=0).astype(int), count)
    else:
        count = r.poisson(lam)
        if plan.active_prob is not None:
            count = count * (r.random(n) < plan.active_prob)
        c_large = r.binomial(count, plan.large_share) if plan.large_share > 0 else np.zeros(n, int)
    c_small = count - c_large
    v_small = c_small * plan.avg_small * np.exp(r.normal(0, 0.5 / np.sqrt(np.maximum(c_small, 1))))
    draws = np.maximum(r.lognormal(math.log(max(plan.large_median, 1)), LARGE_SIGMA, int(c_large.sum())), LARGE_FLOOR)
    v_large = np.bincount(np.repeat(np.arange(n), c_large), weights=draws, minlength=n)

    very_large = []
    if plan.very_large_prob > 0:
        hit = (r.random(n) < plan.very_large_prob) & (count > 0)
        vals = np.round(r.uniform(*cat["very_large_value"], int(hit.sum())), 0)
        c_large = c_large + hit
        v_large[hit] += vals
        very_large = [{"merchant_id": plan.merchant_id, "date": cal.date(i), "value": float(v)}
                      for i, v in zip(np.flatnonzero(hit), vals)]

    a = {"c_small": c_small.astype(float), "c_large": c_large.astype(float), "v_small": v_small, "v_large": v_large}
    hold_info = _apply_hold(a, plan, cal, rng_for(seed, plan.merchant_id, "hold")) if plan.hold else {}
    c_small, c_large = a["c_small"].astype(int), a["c_large"].astype(int)
    upi_count = c_small + c_large

    card_count = np.zeros(n, int)
    card_value = np.zeros(n)
    if plan.has_pos and cat["card_ratio"] > 0:
        card_count = r.poisson(upi_count * cat["card_ratio"])
        card_value = card_count * plan.avg_small * 1.3 * np.exp(r.normal(0, 0.1, n))

    fail_rate = np.full(n, r.uniform(*tx["failed_rate"]))
    debit_share = np.full(n, r.uniform(*tx["failed_debited_share"]))
    if net.any():
        fail_rate = np.where(net, fail_rate + plan.params["peak_failed_rate"] * peak, fail_rate)
        debit_share = np.where(net, plan.params["peak_failed_debited_share"], debit_share)
    failed = r.poisson(upi_count * fail_rate)
    failed_debited = r.binomial(failed, debit_share)

    df = pd.DataFrame({
        "merchant_id": plan.merchant_id,
        "date": cal.day_list,
        "upi_count": upi_count,
        "upi_value": np.round(a["v_small"] + a["v_large"], 2),
        "upi_count_above_2000": c_large,
        "upi_value_above_2000": np.round(a["v_large"], 2),
        "card_count": card_count,
        "card_value": np.round(card_value, 2),
        "failed_count": failed,
        "failed_debited_count": failed_debited,
        "peak_hour_share": np.round(peak, 4),
    })
    return df, very_large, hold_info


def _scale_day(a: dict, i: int, s: float) -> None:
    """Scale one day's payments by s: values exactly, counts rounded, keeping tickets consistent."""
    cs, cl = np.rint(a["c_small"][i] * s), np.rint(a["c_large"][i] * s)
    vs, vl = a["v_small"][i] * s, a["v_large"][i] * s
    if cl > 0 and vl < LARGE_FLOOR * cl:
        cl = np.floor(vl / LARGE_FLOOR)
    if cl == 0:
        vl = 0.0
    if cs == 0:
        cs, vs = (1.0, vs) if vs >= 10 else (0.0, 0.0)
    if cs > 0 and vs / cs >= 2000:
        cs = np.ceil(vs / 1999)
    a["c_small"][i], a["c_large"][i], a["v_small"][i], a["v_large"][i] = cs, cl, vs, vl


def _apply_hold(a: dict, plan: Plan, cal: Calendar, r: np.random.Generator) -> dict:
    """Threshold holder: once month-to-date UPI passes the trigger, daily UPI drops by `drop`,
    and the month is kept under its target (inside the band)."""
    h = plan.hold
    val = lambda i: a["v_small"][i] + a["v_large"][i]
    segs = cal.month_segments()
    scales = {}
    if h.get("trigger_day"):
        for key, idx, complete in segs:
            pre = sum(val(i) for i in idx[: h["trigger_day"]])
            if complete and pre > 0:
                scales[key] = max(h["trigger"] / pre, 1.0)   # only scale up: exact, no lost payments
    default = float(np.mean(list(scales.values()))) if scales else 1.0

    info = {}
    for key, idx, complete in segs:
        if h["months"] != "all" and key not in h["months"]:
            continue
        s0 = scales.get(key, default)
        lo, hi = h["band"]
        target = float(r.uniform(max(lo, h["trigger"] + 2000), hi))
        drop = float(r.uniform(*h["drop"]))
        raw = {i: val(i) for i in idx}
        mtd, pre_vals, crossed = 0.0, [], None
        for j, i in enumerate(idx):
            _scale_day(a, i, s0)
            if mtd + val(i) > target:
                _scale_day(a, i, (target - mtd) / val(i))
            mtd += val(i)
            pre_vals.append(val(i))
            if mtd >= h["trigger"]:
                crossed = j
                break
        if crossed is not None:
            post = idx[crossed + 1:]
            pre_mean = float(np.mean(pre_vals))
            rp = np.array([raw[i] * s0 for i in post])
            m = rp.mean() if len(rp) else 0.0
            for i, x in zip(post, rp):
                want = pre_mean * (1 - drop) * x / m if m > 0 else 0.0
                want = min(want, max(target - mtd, 0.0))
                _scale_day(a, i, want / raw[i] if raw[i] > 0 else 0.0)
                mtd += val(i)
        info[key] = {"crossed_day": None if crossed is None else int(crossed + 1),
                     "target": round(target, 2), "drop": round(drop, 3), "complete": bool(complete)}
    return info
