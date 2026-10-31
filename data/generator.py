"""Phase 0 synthetic dataset generator.

    python -m data.generator                 # writes data/out/*.parquet + *.csv
    python -m data.generator --out <dir>     # e.g. for the determinism check

Order follows DATA_PREPARATION.md §9. All data is synthetic; same seed -> identical files.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import pandas as pd
from faker import Faker

from common.settings import get_settings
from data.generators.context import Calendar, load_configs, resolve_seed
from data.generators.demo import build_demo_plans
from data.generators.devices import generate_devices
from data.generators.fees import fee_affected_ids, fee_monthly_table
from data.generators.labels import labels_table
from data.generators.merchants import build_generic_plans, merchants_table
from data.generators.offers import generate_offers
from data.generators.settlements import generate_settlements
from data.generators.tickets import generate_tickets
from data.generators.txn import generate_txn
from data.generators.zones import field_agents_table, zones_table

# Tables AX-13 writes to at runtime: created empty by load_postgres, not generated here.
RUNTIME_TABLES = ("actions", "visits", "crm_notes", "approvals")


def generate(out_dir: Path) -> dict[str, pd.DataFrame]:
    cfg = load_configs()
    seed = resolve_seed(cfg)
    sim = cfg["sim_config"]
    cal = Calendar(sim)
    fake = Faker("en_IN")
    fake.seed_instance(seed)

    zones = zones_table(sim)
    agents = field_agents_table(sim, seed, fake)
    plans = build_generic_plans(cfg, cal, seed, fake) + build_demo_plans(cfg, cal)

    txn_parts, very_large, hold_info = [], [], {}
    for p in plans:
        df, vl, hi = generate_txn(p, cal, cfg, seed)
        txn_parts.append(df)
        very_large += vl
        if hi:
            hold_info[p.merchant_id] = hi
    txn = pd.concat(txn_parts, ignore_index=True)
    very_large_df = pd.DataFrame(very_large, columns=["merchant_id", "date", "value"])
    hist_mask = txn["date"] <= cal.d0

    fee_monthly = fee_monthly_table(txn[hist_mask], very_large_df, cal, sim["fee_rules"])
    affected = fee_affected_ids(fee_monthly, sim["fee_rules"]["reference_month"])
    merchants = merchants_table(plans, txn[hist_mask], agents, cfg, cal, seed)
    devices = pd.concat([generate_devices(p, cal, cfg, seed) for p in plans], ignore_index=True)

    txn_by_m = dict(tuple(txn.groupby("merchant_id", sort=False)))
    vl_by_m = {m: g.to_dict("records") for m, g in very_large_df.groupby("merchant_id")} if len(very_large_df) else {}
    s_parts, d_parts = [], []
    for p in plans:
        s, d = generate_settlements(p, txn_by_m[p.merchant_id].reset_index(drop=True), vl_by_m.get(p.merchant_id, []),
                                    p.merchant_id in affected, cal, cfg, seed)
        s_parts.append(s)
        d_parts.append(d)
    settlements = pd.concat(s_parts, ignore_index=True)
    deductions = pd.concat(d_parts, ignore_index=True).sort_values(["date", "merchant_id", "type"], kind="stable")
    deductions.insert(0, "deduction_id", [f"D{i + 1:06d}" for i in range(len(deductions))])

    tickets = pd.DataFrame([t for p in plans for t in generate_tickets(p, cal, cfg, seed)])
    tickets = tickets.sort_values(["opened_at", "merchant_id"], kind="stable").reset_index(drop=True)
    tickets.insert(0, "ticket_id", [f"T{i + 1:05d}" for i in range(len(tickets))])

    offers = pd.DataFrame([o for p in plans for o in generate_offers(p, cal, cfg, seed)])
    offers = offers.sort_values(["date", "merchant_id"], kind="stable").reset_index(drop=True)
    offers.insert(0, "offer_id", [f"O{i + 1:05d}" for i in range(len(offers))])

    labels = labels_table(plans, merchants, affected, cal)
    sim_state = pd.DataFrame([{"sim_date": cal.d0, "fee_live": cal.d0 >= cal.g0}])

    tables = {
        "zones": zones,
        "field_agents": agents,
        "merchants": merchants,
        "txn_daily": txn[hist_mask].reset_index(drop=True),
        "txn_daily_future": txn[~hist_mask].reset_index(drop=True),
        "device_health_daily": devices,
        "settlements": settlements,
        "deductions": deductions.reset_index(drop=True),
        "tickets": tickets,
        "offers_history": offers,
        "merchant_fee_monthly": fee_monthly,
        "merchant_labels": labels,
        "sim_state": sim_state,
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, df in tables.items():
        df.to_parquet(out_dir / f"{name}.parquet", index=False)
        df.to_csv(out_dir / f"{name}.csv", index=False)

    # Hidden generation parameters, read only by validate.py (never loaded to Postgres).
    gen_params = {
        "seed": seed,
        "merchants": {p.merchant_id: {"archetype": p.archetype, "onset_idx": p.onset_idx,
                                      "params": p.params, "hold": hold_info.get(p.merchant_id)} for p in plans},
        "very_large_payments": [{**v, "date": v["date"].isoformat()} for v in very_large],
    }
    (out_dir / "gen_params.json").write_text(json.dumps(gen_params, indent=1, sort_keys=True, default=str),
                                             encoding="utf-8")
    return tables


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=get_settings().data_out_dir)
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    t0 = time.time()
    tables = generate(args.out)
    print(f"Generated {len(tables)} tables in {time.time() - t0:.1f}s → {args.out}")
    for name, df in tables.items():
        print(f"  {name:<22}{len(df):>8,} rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
