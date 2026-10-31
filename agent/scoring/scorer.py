"""Sentinel 2.0: churn and growth scores per merchant, computed in code from MCP tool reads.

    python -m agent.scoring.scorer M_DEMO_RAVI M_DEMO_MEENA     # score at the current sim date

Features come only from MCP tools (get_merchant_view, get_peer_change, get_sim_state). Weights
and thresholds live in weights.yaml. Every score carries plain-language evidence with numbers.
Line rule: Save before Grow (Retention playbook §1.1); closed shops are 'none' pending
remote verification (§9.1).
"""
from __future__ import annotations

import asyncio
import math
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

import yaml

from agent.tools import ToolRejected, call
from mcp_servers.client import agent_tools

CFG = yaml.safe_load((Path(__file__).parent / "weights.yaml").read_text(encoding="utf-8"))
W_CHURN, W_GROW, TH, F, SCALE = CFG["churn"], CFG["growth"], CFG["thresholds"], CFG["features"], CFG["scale"]
SAVE_CAUSES = ("device", "network", "charge", "fee", "competitor")   # priority order (Retention playbook §2.2)


def _clip(x: float) -> float:
    return max(0.0, min(1.0, x))


def _mean(xs) -> float:
    xs = list(xs)
    return sum(xs) / len(xs) if xs else 0.0


def _display(raw: float, k: float) -> float:
    """Saturating 0-100 display score: strong cases land 80-95 and never hit 100."""
    return 100 * (1 - math.exp(-raw / k))


def _pct(x: float) -> str:
    return f"{x:+.0%}".replace("+-", "-")


async def _peer(tools, merchant_id: str, base: tuple[date, date], comp: tuple[date, date], exclude: bool) -> dict | None:
    """Same-category peers in the zone; all merchants in the zone if there are too few."""
    args = {"merchant_id": merchant_id, "base_start": base[0].isoformat(), "base_end": base[1].isoformat(),
            "comp_start": comp[0].isoformat(), "comp_end": comp[1].isoformat(), "exclude_fee_affected": exclude}
    for scope in ("category_zone", "zone"):
        try:
            return await call(tools, "get_peer_change", scope=scope, **args)
        except ToolRejected:
            continue
    return None


def _holdback_months(daily: list[dict], today: date) -> tuple[list[dict], list[int]]:
    """(completed months where daily UPI slowed after month-to-date passed ~holdback_mtd,
    totals of all completed months)."""
    months: dict[str, list[dict]] = {}
    for r in daily:
        months.setdefault(r["date"][:7], []).append(r)
    out, totals = [], []
    for key, rows in months.items():
        first = date.fromisoformat(rows[0]["date"])
        last_day = (first.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
        if first.day != 1 or last_day > today or len(rows) != last_day.day:
            continue
        total = sum(r["upi_value"] for r in rows)
        totals.append(total)
        mtd, k = 0.0, None
        for i, r in enumerate(rows):
            mtd += r["upi_value"]
            if mtd >= F["holdback_mtd"]:
                k = i + 1
                break
        # skip the day after crossing: the holder's own trigger can sit a little above holdback_mtd
        if k is None or k + 1 >= len(rows) - 1 or total >= F["holdback_max_month"]:
            continue
        ratio = _mean(r["upi_value"] for r in rows[k + 1:]) / max(_mean(r["upi_value"] for r in rows[:k]), 1)
        if ratio <= F["holdback_max_ratio"]:
            out.append({"month": key, "total": round(total), "crossed_day": k, "ratio": round(ratio, 2)})
    return out, totals


async def score_merchant(tools, merchant_id: str, state: dict | None = None) -> dict:
    state = state or await call(tools, "get_sim_state")
    today, g0 = date.fromisoformat(state["sim_date"]), date.fromisoformat(state["fee_go_live_date"])
    view = await call(tools, "get_merchant_view", merchant_id=merchant_id, days=120)
    prof, daily = view["profile"], view["volume"]["daily"]
    counts = [r["upi_count"] for r in daily]
    ev: list[str] = []
    feat: dict = {}
    churn: dict[str, float] = {}
    grow: dict[str, float] = {}
    causes: set[str] = set()

    # --- volume vs own baseline, and vs peers (Retention playbook §2.3) ---
    d = [date.fromisoformat(r["date"]) for r in daily]
    B, L0, L1, LC = F["baseline_days"], F["long_base_start"], F["long_base_end"], F["long_recent_days"]
    closed = len(counts) >= F["closed_zero_days"] and not any(counts[-F["closed_zero_days"]:])
    holds, month_totals = _holdback_months(daily, today)
    # Holder: holdback in >= holdback_min_months, and every completed month kept inside the band.
    holder = len(holds) >= F["holdback_min_months"] and bool(month_totals) and \
        all(F["holdback_mtd"] <= t < F["holdback_max_month"] for t in month_totals)
    low_volume = _mean(counts[-7 - B:-7]) < F["min_daily_for_change"]   # too few payments to read % changes
    windows = {  # name: (comp counts, base counts, comp dates, base dates)
        f"last 7 days vs {B}-day baseline": (counts[-7:], counts[-7 - B:-7], (d[-7], d[-1]), (d[-7 - B], d[-8])),
        f"last {LC} days vs days {L1}-{L0} back": (counts[-LC:], counts[-L0:-L1 + 1], (d[-LC], d[-1]), (d[-L0], d[-L1])),
    }
    feat["upi_change"], feat["peer"] = {}, {}
    drop_sig, excess_sig, up_sig = 0.0, 0.0, 0.0
    volume_ev_at = len(ev)
    for name, (comp, base, cd, bd) in windows.items():
        if len(daily) < L0 or not _mean(base):
            continue
        chg = _mean(comp) / _mean(base) - 1
        feat["upi_change"][name] = round(chg, 4)
        pr = None if closed else await _peer(tools, merchant_id, bd, cd, today >= g0)
        feat["peer"][name] = pr
        if closed or holder or low_volume or not pr:
            continue
        excess = pr["peer_median"] - pr["merchant_change"]
        below = pr["below_bottom_quarter"] and excess >= F["min_excess_drop"]
        drop_sig = max(drop_sig, _clip(-chg / F["volume_drop_full"]))
        if below:
            excess_sig = max(excess_sig, _clip(excess / F["excess_full"]))
        if chg <= -0.10 or below:
            ev.append(f"UPI count {_pct(chg)} ({name}: {_mean(comp):.1f} vs {_mean(base):.1f} a day); peers "
                      f"({pr['peer_group']}, n={pr['peer_n']}) {_pct(pr['peer_median'])} median"
                      + ("; below the peers' bottom quarter" if below else ""))
        if name.startswith(f"last {LC}") and chg >= F["festive_min_growth"] and -excess >= F["festive_min_excess"]:
            up_sig = _clip(-excess / F["festive_excess_full"])
            ev.append(f"UPI count {_pct(chg)} ({name}) vs {_pct(pr['peer_median'])} for peers "
                      f"({pr['peer_group']}, n={pr['peer_n']}): growing faster than peers")
    # Headline trend: last 3 days (D-2..D0) vs the same 3-day stretch three weeks earlier (D-27..D-21).
    now3, then3 = counts[-3:], counts[-28:-21]
    if len(counts) >= 28 and _mean(then3):
        feat["upi_change_3w"] = chg3 = _mean(now3) / _mean(then3) - 1
        if chg3 <= -0.20 and not (closed or holder or low_volume):
            ev.insert(volume_ev_at, f"UPI down {-chg3:.0%} over 3 weeks ({_mean(now3):.1f} a day in the last 3 days "
                                    f"vs {_mean(then3):.1f} a day three weeks earlier)")
    if drop_sig:
        churn["volume_drop"] = W_CHURN["volume_drop"] * drop_sig
    if excess_sig:
        churn["excess_vs_peers"] = W_CHURN["excess_vs_peers"] * excess_sig
    this_month = [r for r in daily if r["date"][:7] == today.isoformat()[:7]]
    if holder and sum(r["upi_value"] for r in this_month) >= F["holdback_mtd"]:
        ev.append(f"Recent dip matches the month-end holdback: month-to-date UPI "
                  f"₹{sum(r['upi_value'] for r in this_month):,.0f} is past ₹{F['holdback_mtd']:,}")

    # --- devices: act on the current state (last device_current_days), cite the 7-day picture ---
    recent = (await call(tools, "get_device_health", merchant_id=merchant_id, days=F["device_current_days"])
              if view["devices"] and not closed else [])
    for dv in view["devices"]:
        now = [r for r in recent if r["device_type"] == dv["device_type"]]
        if any(r["hardware_fault_flag"] or r["uptime_pct"] < 50 for r in now):
            causes.add("device")
            churn["device_fault"] = W_CHURN["device_fault"]
            ev.append(f"{dv['device_type'].title()} offline {dv['offline_days_7d']} of last 7 days, still down"
                      + (f"; hardware fault flagged {dv['fault_days_7d']} days" if dv["fault_days_7d"] else ""))
        pk, off = dv["peak_errors_avg_7d"], dv["offpeak_errors_avg_7d"]
        if not dv["fault_days_7d"] and pk >= F["network_min_peak_errors"] and pk >= F["network_ratio"] * max(off, 0.5):
            causes.add("network")
            churn["network_peak_errors"] = W_CHURN["network_peak_errors"]
            ev.append(f"{dv['device_type'].title()} network errors {pk:.1f}/day at peak vs {off:.1f} off-peak; no hardware fault")
    feat["devices"] = view["devices"]

    # --- tickets and charges ---
    open_t = view["open_tickets"]
    feat["open_tickets"] = dict(Counter(t["category"] for t in open_t))
    complaints = [t for t in open_t if t["category"] != "other"]
    if complaints:
        churn["open_complaint"] = W_CHURN["open_complaint"]
        for t in complaints:
            ev.append(f"Open {t['category']} ticket {t['ticket_id']} since {t['opened_at'][:10]} ({t['sentiment']}): "
                      f"\"{t['summary']}\"")
    if any(t["sentiment"] == "angry" for t in open_t):
        churn["angry_sentiment"] = W_CHURN["angry_sentiment"]
    unnotified = [x for x in view["deductions_30d"] if not x["notified"]]
    feat["unnotified_deductions"] = len(unnotified)
    if unnotified:
        causes.add("charge")
        churn["unnotified_charge"] = W_CHURN["unnotified_charge"]
        for x in unnotified:
            ev.append(f"Unnotified {x['type'].replace('_', ' ')} ₹{x['amount']:,.0f} deducted on {x['date']}")
    if any(t["category"] == "failed_txn" for t in complaints) and "network" not in causes:
        failed = sum(r["failed_debited_count"] for r in daily[-7:])
        if failed:
            ev.append(f"{failed} failed-but-debited payments in the last 7 days")

    # --- fee (from go-live, vs non-affected peers) ---
    fee = view["fee"]
    lm = fee["last_full_month"] or {}
    feat["fee_affected"] = bool(lm.get("is_fee_affected"))
    if today >= g0 + timedelta(days=F["fee_min_post_days"]) and not (closed or holder or low_volume):
        pre = (g0 - timedelta(days=F["fee_pre_window_days"]), g0 - timedelta(days=1))
        post = (today - timedelta(days=F["fee_post_window_days"] - 1), today)
        fp = await _peer(tools, merchant_id, pre, post, True)
        feat["fee_peer"] = fp
        if fp and fp["below_bottom_quarter"] and fp["merchant_change"] <= -F["fee_min_drop"] \
                and fp["peer_median"] - fp["merchant_change"] >= F["fee_min_excess"]:
            # Level vs the merchant's own pre-festive normal, relative to non-affected peers: a festive
            # grower or a healthy merchant coming off the Diwali peak is not below normal.
            normal = (g0 - timedelta(days=F["fee_normal_start"]), g0 - timedelta(days=F["fee_normal_end"]))
            nv = await _peer(tools, merchant_id, normal, post, True)
            below_normal = bool(nv and nv["peer_median"] - nv["merchant_change"] >= F["fee_normal_min_excess"])
            # A decline already under way before go-live is not a fee reaction (cause stays competitor).
            gp = await _peer(tools, merchant_id, normal, (g0 - timedelta(days=10), g0 - timedelta(days=4)), True)
            predates = bool(gp and gp["below_bottom_quarter"]
                            and gp["peer_median"] - gp["merchant_change"] >= F["min_excess_drop"])
            feat.update(fee_below_normal=below_normal, fee_drop_predates_go_live=predates)
            if below_normal and not predates:
                causes.add("fee")
                churn["fee_drop_since_go_live"] = W_CHURN["fee_drop_since_go_live"]
                ev.append(f"UPI count {_pct(fp['merchant_change'])} since fee go-live (last {F['fee_post_window_days']} "
                          f"days vs {F['fee_pre_window_days']} days before) vs {_pct(fp['peer_median'])} for "
                          f"non-affected peers (n={fp['peer_n']})")
                ev.append(f"Below its pre-festive normal: {_pct(nv['merchant_change'])} vs {_pct(nv['peer_median'])} "
                          f"for the same peers")
                ev.append("Fee status: " + (f"affected, est. ₹{lm['est_monthly_fee']:,.0f}/month (last full month)"
                                            if feat["fee_affected"] else
                                            f"not affected ({fee['not_affected_variant']}): drop looks like confusion"))

    # --- growth signals ---
    avg28 = _mean(counts[-7 * F["ready_weeks"]:])
    last90 = counts[-F["steady_days"]:]
    # Steady history = regular recorded sales in value terms: active almost every day for 90 days, and the
    # last completed months within a band of each other (Product catalogue §1.2). Not a count threshold:
    # a tailor taking 2-3 large payments a day can have a real history.
    months = [r["monthly_upi_value"] for r in view["fee"]["completed_months"][-F["steady_months"]:]]
    m_avg = _mean(months)
    stable = len(months) >= F["steady_months"] and m_avg >= F["steady_min_monthly_upi"]         and all(abs(v / m_avg - 1) <= F["steady_max_month_dev"] for v in months)
    steady = len(last90) >= F["steady_days"] and sum(c > 0 for c in last90) >= F["steady_min_active_days"] and stable
    stage = "steady_90d" if prof["has_soundbox"] and steady else ("soundbox" if prof["has_soundbox"] else "qr")
    feat.update(activation_stage=stage, avg_daily_upi_28d=round(avg28, 1))
    feat["holdback_months"] = holds
    if not closed:
        if holder:
            grow["threshold_holdback"] = W_GROW["threshold_holdback"]
            ev.append("Month-end UPI holdback in " + ", ".join(
                f"{h['month']} (₹{h['total']:,} total; after passing ₹{F['holdback_mtd']:,} on day {h['crossed_day']}, "
                f"daily UPI fell to {h['ratio']:.0%} of the earlier pace)" for h in holds))
        if avg28 < F["ready_daily_upi"] and "threshold_holdback" not in grow:
            grow["low_active"] = W_GROW["low_active"]
            ev.append(f"Low activity: {avg28:.1f} UPI payments a day over 4 weeks (stage: {stage})")
        elif not prof["has_soundbox"]:
            grow["soundbox_ready"] = W_GROW["soundbox_ready"]
            ev.append(f"QR only, {avg28:.1f} UPI payments a day over 4 weeks: ready for a Soundbox (Product catalogue §1.2)")
        if stage == "steady_90d":
            grow["loan_path"] = W_GROW["loan_path"]
        if up_sig:
            grow["festive_growth"] = W_GROW["festive_growth"] * up_sig

    # --- line and cause ---
    churn_raw, growth_raw = sum(churn.values()), sum(grow.values())
    churn_score = _display(churn_raw, SCALE["churn"])
    growth_score = _display(growth_raw, SCALE["growth"])
    if closed:
        line, cause = "none", "closed"
        ev.insert(0, f"No UPI payments for {F['closed_zero_days']}+ days; verify remotely before any visit (§9.1)")
    elif churn_score >= TH["save"]:
        line = "save"
        cause = next((c for c in SAVE_CAUSES if c in causes), "competitor")
    elif growth_score >= TH["grow"]:
        line = "grow"
        cause = "threshold" if "threshold_holdback" in grow else "activation"
    else:
        line, cause = "none", None
    return {
        "merchant_id": merchant_id, "name": prof["name"], "sim_date": today.isoformat(),
        "churn": round(churn_score), "growth": round(growth_score), "line": line, "cause": cause,
        "raw": {"churn": round(churn_raw, 1), "growth": round(growth_raw, 1)},
        "growth_paused": line == "save" and growth_score >= TH["grow"],
        "evidence": ev, "components": {"churn": {k: round(v, 1) for k, v in churn.items()},
                                       "growth": {k: round(v, 1) for k, v in grow.items()}},
        "features": feat,
    }


async def score_all(tools, merchant_ids: list[str] | None = None, concurrency: int = 8) -> list[dict]:
    state = await call(tools, "get_sim_state")
    if merchant_ids is None:
        merchant_ids = [m["merchant_id"] for m in await call(tools, "list_merchants")]
    sem = asyncio.Semaphore(concurrency)

    async def one(mid):
        async with sem:
            return await score_merchant(tools, mid, state)
    return await asyncio.gather(*(one(m) for m in merchant_ids))


async def main(ids: list[str]) -> None:
    async with agent_tools() as tools:
        for s in await score_all(tools, ids or None):
            print(f"\n{s['merchant_id']} {s['name']} @ {s['sim_date']}: churn {s['churn']} · growth {s['growth']} · "
                  f"{s['line']} / {s['cause']}" + (" · growth paused" if s["growth_paused"] else ""))
            for e in s["evidence"]:
                print(f"  - {e}")

