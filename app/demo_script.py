"""End-to-end demo: Reset -> all 200 merchants on D0, D+1, G0 and G0+5, saving each day.

    python -m app.demo_script            # replay: every LLM call from the cache, no API calls
    python -m app.demo_script --live     # live: cache misses call the API (a few calls; see --estimate)

On D0 the RM decision on Meena's loan is scripted (decided_by "RM (scripted demo)") so the resume
-> make_offer step is part of the run; every other approval stays pending for the RM queue.
After each day the full state is saved (app/admin.py save_snapshot) under data/out/snapshots/<mode>/,
so any day can be loaded in the app and explored, or continued with the clock.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from collections import Counter
from collections.abc import Callable
from datetime import datetime

from agent.run_day import run_day_with_stats
from app import admin
from common import llm
from common.settings import get_settings

STEPS = [("D0", None), ("D+1", ("advance", 1)), ("G0", ("go_live",)), ("G0+5", ("advance", 5))]
SCRIPTED_RM = "RM (scripted demo)"


def _add(a: dict, b: dict) -> dict:
    return {k: a.get(k, 0) + b.get(k, 0) for k in set(a) | set(b)}


def run_full_demo(mode: str = "replay", progress: Callable[[str], None] = print) -> list[dict]:
    prev = llm.get_mode()
    llm.set_mode(mode)
    summary = []
    try:
        progress("Reset to D0")
        admin.reset()
        for label, move in STEPS:
            if move and move[0] == "advance":
                admin.advance_day(move[1])
            elif move:
                admin.jump_to_go_live()
            t0 = time.time()
            progress(f"{label}: running the agent for all 200 merchants ({mode})…")
            res, stats = asyncio.run(run_day_with_stats(None))
            if label == "D0":
                for p in admin.list_pending_approvals():
                    if p["merchant_id"] == "M_DEMO_MEENA":
                        admin.decide_approval(p["approval_id"], "approved", SCRIPTED_RM)
                        progress(f"{label}: {SCRIPTED_RM} approved Meena's loan; resuming her thread")
                        _, st2 = asyncio.run(run_day_with_stats(["M_DEMO_MEENA"], resume_only=True))
                        stats = _add(stats, st2)
            row = {"label": label, "sim_date": str(admin.get_sim_state()["sim_date"]), "seconds": round(time.time() - t0),
                   "statuses": dict(Counter(r["status"] for r in res)),
                   "actions": dict(Counter(f"{a['kind']}:{a['status']}" for r in res for a in r["actions"])),
                   "rm_queue": len(admin.list_pending_approvals()), "human_queue": len(admin.human_queue()),
                   "errors": [f"{r['merchant_id']}: {r.get('error')}" for r in res if r["status"] == "error"],
                   "llm": {k: stats[k] for k in ("hits", "misses", "api_calls", "embed_hits", "embed_misses",
                                                 "embed_api_calls")}, "saved_at": datetime.now().isoformat(timespec="seconds")}
            admin.save_snapshot(mode, label, row)
            summary.append(row)
            progress(f"{label}: saved · RM queue {row['rm_queue']} · human queue {row['human_queue']} · "
                     f"LLM {stats['hits']} cached, {stats['misses']} not cached, {stats['api_calls']} API calls")
    finally:
        llm.set_mode(prev)
    out = get_settings().data_out_dir / "snapshots" / mode / "run_log.json"
    out.write_text(json.dumps(summary, indent=1), encoding="utf-8")
    return summary


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true")
    args = ap.parse_args()
    t0 = time.time()
    rows = run_full_demo("live" if args.live else "replay")
    print(f"\n{'day':<6}{'date':<12}{'RM':>4}{'human':>7}{'hits':>6}{'miss':>6}{'API':>5}  actions")
    for r in rows:
        acts = ", ".join(f"{k}={v}" for k, v in sorted(r["actions"].items()))
        print(f"{r['label']:<6}{r['sim_date']:<12}{r['rm_queue']:>4}{r['human_queue']:>7}{r['llm']['hits']:>6}"
              f"{r['llm']['misses']:>6}{r['llm']['api_calls']:>5}  {acts}")
        for e in r["errors"]:
            print(f"      ERROR {e}")
    print(f"\n{time.time() - t0:.0f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
