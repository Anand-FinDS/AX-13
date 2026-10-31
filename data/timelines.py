"""Day-by-day timelines for the scripted demo merchants (Phase 0 acceptance check).

    python -m data.timelines                 # writes data/out/demo_timelines.md and prints it
    python -m data.timelines M_DEMO_RAVI     # one merchant

Reads the generated parquet files, not Postgres.
"""
from __future__ import annotations

import argparse
import sys
from datetime import timedelta

import pandas as pd

from common.settings import get_settings
from data.generators.context import Calendar, load_configs

DEMO_IDS = ["M_DEMO_SMALL", "M_DEMO_RAVI", "M_DEMO_MEENA"]


def timeline(mid: str, out, cal: Calendar) -> pd.DataFrame:
    read = lambda t: pd.read_parquet(out / f"{t}.parquet")  # noqa: E731
    txn = pd.concat([read("txn_daily"), read("txn_daily_future")])
    txn = txn[txn["merchant_id"] == mid].assign(date=lambda d: pd.to_datetime(d["date"])).set_index("date")
    dev = read("device_health_daily")
    sb = dev[(dev["merchant_id"] == mid) & (dev["device_type"] == "soundbox")]
    sb = sb.assign(date=pd.to_datetime(sb["date"])).set_index("date")
    tk = read("tickets").query("merchant_id == @mid")
    ded = read("deductions").query("merchant_id == @mid")
    offers = read("offers_history").query("merchant_id == @mid")

    notes: dict[pd.Timestamp, list[str]] = {}
    for _, r in tk.iterrows():
        notes.setdefault(pd.Timestamp(r["opened_at"]).normalize(), []).append(
            f"ticket {r['ticket_id']} {r['category']} ({r['status']}): {r['summary']}")
        if pd.notna(r["resolved_at"]):
            notes.setdefault(pd.Timestamp(r["resolved_at"]).normalize(), []).append(f"ticket {r['ticket_id']} resolved")
    for _, r in ded.iterrows():
        notes.setdefault(pd.Timestamp(r["date"]), []).append(
            f"{r['type']} ₹{r['amount']:,.0f}" + ("" if r["notified"] else " UNNOTIFIED"))
    for _, r in offers.iterrows():
        notes.setdefault(pd.Timestamp(r["date"]), []).append(f"offer {r['product']} via {r['channel']}: {r['response']}")
    marks = {pd.Timestamp(cal.d0): "D0", pd.Timestamp(cal.g0): "G0 fee go-live",
             pd.Timestamp(cal.festive_dates[1]): "Diwali"}

    rows = []
    mtd, month = 0.0, None
    for d, r in txn.iterrows():
        if d.month != month:
            mtd, month = 0.0, d.month
        mtd += r["upi_value"]
        i = cal.idx(d.date())
        rows.append({
            "date": d.strftime("%a %d %b"),
            "D": f"{i - cal.i_d0:+d}",
            "G": f"{i - cal.i_g0:+d}",
            "mark": marks.get(d, ""),
            "upi_n": int(r["upi_count"]),
            "upi_value": f"{r['upi_value']:,.0f}",
            ">2k_n": int(r["upi_count_above_2000"]),
            "month_to_date": f"{mtd:,.0f}",
            "soundbox": ("—" if d not in sb.index else
                         f"{sb.loc[d, 'uptime_pct']:.0f}%" + (" FAULT" if sb.loc[d, "hardware_fault_flag"] else "")),
            "events": "; ".join(notes.get(d, [])),
        })
    return pd.DataFrame(rows)


def _md(df: pd.DataFrame) -> str:
    lines = ["| " + " | ".join(df.columns) + " |", "|" + "---|" * len(df.columns)]
    lines += ["| " + " | ".join(str(v) for v in row) + " |" for row in df.itertuples(index=False)]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("merchants", nargs="*", default=DEMO_IDS)
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    s = get_settings()
    cal = Calendar(load_configs()["sim_config"])
    parts = [f"# Demo merchant timelines\n\nD0 {cal.d0} · G0 {cal.g0} · {cal.start} → "
             f"{cal.start + timedelta(cal.n - 1)}. Rows after D0 are in txn_daily_future (released by the clock).\n"]
    for mid in args.merchants:
        parts.append(f"## {mid}\n\n{_md(timeline(mid, s.data_out_dir, cal))}\n")
    text = "\n".join(parts)
    path = s.data_out_dir / "demo_timelines.md"
    path.write_text(text, encoding="utf-8")
    print(text)
    print(f"\n→ {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
