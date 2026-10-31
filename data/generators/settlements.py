"""settlements (T+1) and deductions (rent, misc, planted unnotified charges, upi_fee from G0)."""
from __future__ import annotations

import numpy as np
import pandas as pd

from data.generators.context import Calendar, rng_for
from data.generators.fees import cap_saving
from data.generators.merchants import Plan


def generate_settlements(plan: Plan, txn: pd.DataFrame, very_large: list[dict], fee_affected: bool,
                         cal: Calendar, cfg: dict, seed: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    sim = cfg["sim_config"]
    st, dd, rules = sim["settlements"], sim["deductions"], sim["fee_rules"]
    r = rng_for(seed, plan.merchant_id, "settlement")
    n = cal.n

    upi = txn["upi_value"].to_numpy()
    card = txn["card_value"].to_numpy()
    gross = upi + card
    # txn day i settles on day i+1; the last day settles outside the window.
    sid = {i + 1: f"S-{plan.merchant_id}-{cal.date(i + 1):%Y%m%d}" for i in range(n - 1) if gross[i] > 0}
    settle_days = sorted(sid)

    def settlement_on_or_after(day: int) -> int | None:
        k = np.searchsorted(settle_days, day)
        return settle_days[k] if k < len(settle_days) else None

    deductions: list[dict] = []

    def add(day: int | None, dtype: str, amount: float, notified: bool) -> None:
        if day is None or amount <= 0:
            return
        deductions.append({"settle_day": day, "type": dtype, "amount": round(float(amount), 2), "notified": notified})

    if plan.has_soundbox and plan.fixed["soundbox_rent_month"] > 0:
        for key, idx, _ in cal.month_segments():
            if cal.dates[idx[0]].day == 1:
                day = settlement_on_or_after(int(idx[0]))
                if day is not None and day <= idx[-1]:
                    add(day, "soundbox_rent", plan.fixed["soundbox_rent_month"], True)
    if not plan.scripted:
        for key, idx, _ in cal.month_segments():
            if r.random() < dd["other_deduction_prob_per_month"]:
                add(settlement_on_or_after(int(r.choice(idx))), "other", float(r.integers(49, 300)), True)
    for d in plan.deductions:
        add(settlement_on_or_after(d["idx"]), d["type"], d["amount"], d["notified"])
    if dd["post_go_live_upi_fee"] and fee_affected:
        above = txn["upi_value_above_2000"].to_numpy()
        saving = {cal.idx(v["date"]): cap_saving(v["value"], rules) for v in very_large}
        for i in range(cal.i_g0, n - 1):
            if above[i] > 0 and (i + 1) in sid:
                add(i + 1, "upi_fee", rules["rate"] * above[i] - saving.get(i, 0.0), True)

    ded_by_day: dict[int, float] = {}
    for d in deductions:
        ded_by_day[d["settle_day"]] = ded_by_day.get(d["settle_day"], 0.0) + d["amount"]

    rows = []
    for day in settle_days:
        i = day - 1
        u = r.random()
        if u < st["held_prob"]:
            status, delay = "held", 0
        elif u < st["held_prob"] + st["delayed_prob"]:
            status, delay = "delayed", int(r.integers(st["delay_hours"][0], st["delay_hours"][1] + 1))
        else:
            status, delay = "settled", 0
        # Simplification: a deduction larger than the day's takings floors net at 0 (no carry-over).
        net = max(gross[i] - st["card_mdr"] * card[i] - ded_by_day.get(day, 0.0), 0.0)
        rows.append({"settlement_id": sid[day], "merchant_id": plan.merchant_id, "date": cal.date(day),
                     "gross_amount": round(float(gross[i]), 2), "net_amount": round(float(net), 2),
                     "delay_hours": delay, "status": status})

    ded_rows = [{"merchant_id": plan.merchant_id, "settlement_id": sid[d["settle_day"]],
                 "date": cal.date(d["settle_day"]), "type": d["type"], "amount": d["amount"],
                 "notified": d["notified"]} for d in sorted(deductions, key=lambda d: (d["settle_day"], d["type"]))]
    return pd.DataFrame(rows), pd.DataFrame(ded_rows)
