"""merchant_fee_monthly: completed history months only (proposed framework, as reported).

is_fee_affected = not exempt AND at least one payment above 2,000 in the month.
est_monthly_fee = 0.004 x value_above_2000, less the cap saving on any single payment > 75,000.
"""
from __future__ import annotations

from datetime import date

import pandas as pd

from data.generators.context import Calendar


def cap_saving(value: float, rules: dict) -> float:
    """How much the per-transaction cap takes off 0.4% of one payment."""
    return max(rules["rate"] * value - rules["cap_per_txn"], 0.0)


def fee_monthly_table(txn: pd.DataFrame, very_large: pd.DataFrame, cal: Calendar, rules: dict) -> pd.DataFrame:
    months = [k for k, _ in cal.completed_history_months()]
    t = txn.assign(month=pd.to_datetime(txn["date"]).dt.strftime("%Y-%m"))
    t = t[t["month"].isin(months)]
    g = t.groupby(["merchant_id", "month"], as_index=False).agg(
        monthly_upi_value=("upi_value", "sum"),
        count_above_2000=("upi_count_above_2000", "sum"),
        value_above_2000=("upi_value_above_2000", "sum"),
    )
    if len(very_large):
        vl = very_large.assign(month=pd.to_datetime(very_large["date"]).dt.strftime("%Y-%m"),
                               saving=very_large["value"].map(lambda v: cap_saving(v, rules)))
        g = g.merge(vl.groupby(["merchant_id", "month"], as_index=False)["saving"].sum(), how="left")
    else:
        g["saving"] = 0.0
    g["saving"] = g["saving"].fillna(0.0)
    g["is_fee_affected"] = (g["monthly_upi_value"] >= rules["exempt_below_monthly_upi"]) & (g["count_above_2000"] > 0)
    fee = rules["rate"] * g["value_above_2000"] - g["saving"]
    g["est_monthly_fee"] = fee.where(g["is_fee_affected"], 0.0).round(2)
    g["monthly_upi_value"] = g["monthly_upi_value"].round(2)
    g["value_above_2000"] = g["value_above_2000"].round(2)
    g["month"] = g["month"].map(lambda m: date(int(m[:4]), int(m[5:]), 1))
    return g[["merchant_id", "month", "monthly_upi_value", "value_above_2000", "is_fee_affected", "est_monthly_fee"]]


def fee_affected_ids(fee_monthly: pd.DataFrame, reference_month: str) -> set[str]:
    ref = date(int(reference_month[:4]), int(reference_month[5:]), 1)
    rows = fee_monthly[(fee_monthly["month"] == ref) & fee_monthly["is_fee_affected"]]
    return set(rows["merchant_id"])
