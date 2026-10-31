"""Scoring precision/recall vs hidden labels, at D0 and at G0+5.

    python -m eval.scoring_eval

The only code that reads ax13_eval (main login). The scorer itself reads through MCP only.
Moves the simulation clock with app/admin.py and resets to D0 when done.

Ground truth at a date T is derived from the hidden archetype and onset, using the rules in
TRUTH below: a Save case counts from the day its signal can first be seen in the data.
"""
from __future__ import annotations

import asyncio
import json
import math
import sys
import time
from datetime import date, timedelta

import psycopg
from psycopg.rows import dict_row

from agent.scoring import score_all
from app import admin
from common.settings import get_settings
from mcp_servers.client import agent_tools

CAUSE = {"device_fault": "device", "network_not_device": "network", "charge_complainer": "charge",
         "post_fee_dropper": "fee", "slow_decliner": "competitor", "closed_shop": "closed",
         "threshold_holder": "threshold", "low_active": "activation", "festive_grower": "activation"}
# Days after onset before a Save signal is visible in the data (ramps take time to show).
VISIBLE_AFTER = {"slow_decliner": 7, "network_not_device": 3, "post_fee_dropper": 2,
                 "device_fault": 0, "charge_complainer": 0}
DEMO = {"M_DEMO_SMALL": lambda T: ("none", None),
        "M_DEMO_RAVI": lambda T: ("save", "device") if T < admin.G0 else ("save", "fee"),
        "M_DEMO_MEENA": lambda T: ("grow", "threshold")}


def load_labels() -> tuple[dict, dict]:
    s = get_settings()
    with psycopg.connect(s.postgres_dsn, row_factory=dict_row) as conn:
        rows = conn.execute(f"SELECT * FROM {s.postgres_eval_schema}.merchant_labels").fetchall()
    gen = json.loads((s.data_out_dir / "gen_params.json").read_text(encoding="utf-8"))["merchants"]
    return {r["merchant_id"]: r for r in rows}, gen


def truth_at(lab: dict, gen: dict, T: date) -> tuple[str, str | None]:
    mid, arch, onset = lab["merchant_id"], lab["archetype"], lab["onset_date"]
    if mid in DEMO:
        return DEMO[mid](T)
    if arch == "healthy_steady":
        return "none", None
    if arch == "closed_shop":
        return "none", "closed"
    if arch in ("threshold_holder", "low_active", "festive_grower"):
        return "grow", CAUSE[arch]
    if onset + timedelta(days=VISIBLE_AFTER[arch]) > T:
        return "none", None
    if arch == "charge_complainer" and T > onset + timedelta(days=29):
        return "none", None                # outside the 30-day window the scorer reads deductions from
    if arch == "device_fault":
        end = onset + timedelta(days=gen[mid]["params"]["offline_days"] - 1)
        if T > end:
            return "none", None            # fault over and volume back: nothing to save
    return "save", CAUSE[arch]


def prf(pairs: list[tuple], cls: str) -> tuple[int, float, float]:
    tp = sum(p == cls and t == cls for t, p in pairs)
    pred = sum(p == cls for _, p in pairs)
    true = sum(t == cls for t, _ in pairs)
    return true, (tp / pred if pred else float("nan")), (tp / true if true else float("nan"))


def table(title: str, pairs: list[tuple], classes: list[str]) -> list[str]:
    acc = sum(t == p for t, p in pairs) / len(pairs)
    out = [f"\n{title}  (n={len(pairs)}, accuracy {acc:.0%})", f"  {'class':<12}{'support':>8}{'precision':>11}{'recall':>8}"]
    for c in classes:
        n, p, r = prf(pairs, c)
        out.append(f"  {c:<12}{n:>8}{p:>11.0%}{r:>8.0%}" if n or not math.isnan(p) else f"  {c:<12}{n:>8}{'—':>11}{'—':>8}")
    return out


async def run() -> tuple[dict, list[str]]:
    labels, gen = load_labels()
    lines, demo_rows, misses = [], [], {}
    checkpoints = [("D0", lambda: admin.reset()),
                   ("G0+5", lambda: (admin.jump_to_go_live(), admin.advance_day(5)))]
    async with agent_tools() as tools:
        for name, move in checkpoints:
            move()
            T = admin.get_sim_state()["sim_date"]
            t0 = time.time()
            scores = await score_all(tools)
            secs = time.time() - t0
            truth = {m: truth_at(labels[m], gen, T) for m in labels}
            line_pairs = [(truth[s["merchant_id"]][0], s["line"]) for s in scores]
            cause_pairs = [(truth[s["merchant_id"]][1] or "-", s["cause"] or "-") for s in scores]
            lines += [f"\n=== {name} ({T}) · {len(scores)} merchants scored via MCP in {secs:.0f}s ==="]
            lines += table("Line", line_pairs, ["save", "grow", "none"])
            lines += table("Cause", cause_pairs, ["device", "network", "charge", "fee", "competitor", "closed",
                                                  "threshold", "activation"])
            misses[name] = [(s["merchant_id"], labels[s["merchant_id"]]["archetype"], truth[s["merchant_id"]],
                             (s["line"], s["cause"]), s["churn"], s["growth"])
                            for s in scores if truth[s["merchant_id"]] != (s["line"], s["cause"])]
            for s in scores:
                if s["merchant_id"] in DEMO:
                    demo_rows.append((name, s))
    admin.reset()
    return {"demo": demo_rows, "misses": misses}, lines


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    res, lines = asyncio.run(run())
    print("\n".join(lines))
    print("\nMisclassified (merchant, archetype, truth, predicted, churn, growth):")
    for name, rows in res["misses"].items():
        print(f"  {name}: {len(rows)}")
        for r in rows:
            print(f"    {r[0]:<8} {r[1]:<20} truth {r[2]!s:<26} pred {r[3]!s:<28} churn {r[4]:>3} growth {r[5]:>3}")
    print("\nDemo merchants:")
    for name, s in sorted(res["demo"], key=lambda x: (x[1]["merchant_id"], x[0])):
        print(f"\n  {s['merchant_id']} @ {name} ({s['sim_date']}): churn {s['churn']} · growth {s['growth']} · "
              f"{s['line']} / {s['cause']}" + (" · growth paused" if s["growth_paused"] else ""))
        for e in s["evidence"]:
            print(f"    - {e}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
