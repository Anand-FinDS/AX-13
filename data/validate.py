"""Phase 0 validation (DATA_PREPARATION.md §13). Fails loudly; writes data/out/summary.md.

    python -m data.validate              # all checks, including the determinism re-run
    python -m data.validate --skip-determinism
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
from datetime import timedelta
from pathlib import Path

import matplotlib
import numpy as np
import pandas as pd

from common.settings import REPO_ROOT, get_settings
from data.generators.context import Calendar, load_configs

matplotlib.use("Agg")
import matplotlib.pyplot as plt

# Reference palette (dataviz skill, light mode)
SURFACE, INK, INK_2, MUTED, GRID, AXIS = "#fcfcfb", "#0b0b0b", "#52514e", "#898781", "#e1e0d9", "#c3c2b7"
SERIES = ["#2a78d6", "#eb6834"]

TABLES = ["zones", "field_agents", "merchants", "txn_daily", "txn_daily_future", "device_health_daily",
          "settlements", "deductions", "tickets", "offers_history", "merchant_fee_monthly",
          "merchant_labels", "sim_state"]


class Checks:
    def __init__(self):
        self.rows: list[tuple[str, bool, str]] = []

    def add(self, name: str, ok: bool, detail: str = "") -> None:
        self.rows.append((name, bool(ok), detail))

    @property
    def failed(self) -> list[tuple[str, bool, str]]:
        return [r for r in self.rows if not r[1]]


def load(out: Path) -> dict[str, pd.DataFrame]:
    return {t: pd.read_parquet(out / f"{t}.parquet") for t in TABLES}


def _d(x) -> pd.Timestamp:
    return pd.Timestamp(x)


def run_checks(t: dict[str, pd.DataFrame], cfg: dict, cal: Calendar, gp: dict, out: Path,
               determinism: bool) -> Checks:
    c = Checks()
    sim, archs = cfg["sim_config"], cfg["archetypes"]["archetypes"]
    n_m = sim["scale"]["merchants"]
    m, lab = t["merchants"], t["merchant_labels"]
    txn = pd.concat([t["txn_daily"], t["txn_daily_future"]], ignore_index=True)
    txn["date"] = pd.to_datetime(txn["date"])
    dev = t["device_health_daily"].assign(date=lambda d: pd.to_datetime(d["date"]))
    D0, G0 = _d(cal.d0), _d(cal.g0)
    day = lambda anchor, off: (D0 if anchor == "D" else G0) + timedelta(days=off)

    # ---- row counts ----
    c.add("row count: merchants", len(m) == n_m, f"{len(m)}")
    c.add("row count: txn_daily = merchants x history", len(t["txn_daily"]) == n_m * cal.n_hist, f"{len(t['txn_daily']):,}")
    c.add("row count: txn_daily_future = merchants x future", len(t["txn_daily_future"]) == n_m * cal.n_future,
          f"{len(t['txn_daily_future']):,}")
    c.add("history ends at D0, future starts D+1",
          pd.to_datetime(t["txn_daily"]["date"]).max() == D0 and pd.to_datetime(t["txn_daily_future"]["date"]).min() == D0 + timedelta(1))

    # ---- archetype proportions ----
    gen = lab[lab["archetype"] != "scripted"]
    share = gen["archetype"].value_counts(normalize=True)
    worst = max(abs(share.get(a, 0) - v["share"]) for a, v in archs.items())
    c.add("archetype shares within ±2 pp", worst <= 0.02, f"max deviation {worst * 100:.2f} pp")

    # ---- value sanity ----
    num = {"txn": txn.select_dtypes("number"), "settlements": t["settlements"].select_dtypes("number"),
           "deductions": t["deductions"].select_dtypes("number"), "devices": dev.select_dtypes("number")}
    neg = {k: int((v < 0).sum().sum()) for k, v in num.items()}
    c.add("no negative counts or values", sum(neg.values()) == 0, str(neg))
    c.add("uptime within 0-100", dev["uptime_pct"].between(0, 100).all())
    c.add("above-2000 consistent (value >= 2001 x count, count <= upi_count)",
          ((txn["upi_value_above_2000"] >= 2001 * txn["upi_count_above_2000"] - 0.01)
           & (txn["upi_count_above_2000"] <= txn["upi_count"])).all())

    # ---- consistency: device faults and closed shops ----
    ratios = []
    for mid, g in dev[dev["hardware_fault_flag"]].groupby("merchant_id"):
        tm = txn[txn["merchant_id"] == mid].set_index("date")["upi_count"]
        first = g["date"].min()
        base = tm[(tm.index >= first - timedelta(14)) & (tm.index < first)].mean()
        ratios.append(tm.loc[g["date"]].mean() / base if base else 0)
    c.add("Soundbox fault days -> UPI count drop (mean < 0.8 x prior 14 days)",
          len(ratios) > 0 and max(ratios) < 0.8, f"{len(ratios)} merchants, worst ratio {max(ratios):.2f}")
    closed = gen[gen["archetype"] == "closed_shop"]
    bad = []
    for _, r in closed.iterrows():
        tm = txn[(txn["merchant_id"] == r["merchant_id"]) & (txn["date"] >= _d(r["onset_date"]))]
        if tm["upi_count"].sum() or (D0 - _d(r["onset_date"])).days < 14:
            bad.append(r["merchant_id"])
    c.add("closed shops: zero txns from closure (>= 14 days before D0) to end", not bad, f"{len(closed)} shops; bad {bad}")

    # ---- fee ----
    fm = t["merchant_fee_monthly"].assign(month=lambda d: pd.to_datetime(d["month"]))
    ref = _d(sim["fee_rules"]["reference_month"] + "-01")
    affected_share = lab["is_fee_affected"].mean()
    c.add("fee-affected share 25-40%", 0.25 <= affected_share <= 0.40,
          f"{lab['is_fee_affected'].sum()} merchants ({affected_share:.0%})")
    fm_ref = fm[fm["month"] == ref].set_index("merchant_id")
    c.add("labels.is_fee_affected matches merchant_fee_monthly (reference month)",
          (lab.set_index("merchant_id")["is_fee_affected"] == fm_ref["is_fee_affected"].reindex(lab["merchant_id"])).all())
    c.add("upi_fee deductions only from G0, only on fee-affected merchants",
          (pd.to_datetime(t["deductions"].query("type == 'upi_fee'")["date"]) > G0).all()
          and set(t["deductions"].query("type == 'upi_fee'")["merchant_id"]) <= set(lab.query("is_fee_affected")["merchant_id"]))

    holders = [mid for mid, v in gp["merchants"].items() if v["hold"]]
    month_vals = (txn.assign(month=txn["date"].dt.to_period("M"))
                  .groupby(["merchant_id", "month"])["upi_value"].sum())
    out_of_band, drops, worst_drop = [], 0, 1.0
    for mid in holders:
        for key, info in gp["merchants"][mid]["hold"].items():
            if not info["complete"]:
                continue
            v = month_vals[(mid, pd.Period(key))]
            if not 85000 <= v <= 99000:
                out_of_band.append(f"{mid} {key} {v:,.0f}")
            daily = txn[(txn["merchant_id"] == mid) & (txn["date"].dt.to_period("M") == pd.Period(key))]["upi_value"].to_numpy()
            k = info["crossed_day"]
            if k is None or k >= len(daily):
                continue
            ratio = daily[k:].mean() / daily[:k].mean()
            worst_drop = min(worst_drop, 1 - ratio)
            drops += 1
    c.add("threshold holders: complete-month UPI within 85k-99k", not out_of_band,
          f"{len(holders)} merchants" + (f"; out: {out_of_band}" if out_of_band else ""))
    c.add("threshold holders: post-trigger daily UPI >= 30% below pre-trigger", drops > 0 and worst_drop >= 0.30,
          f"{drops} merchant-months, smallest drop {worst_drop:.0%}")

    droppers = gen[gen["archetype"] == "post_fee_dropper"]
    exempt = droppers["expected_action"] == "not_affected_message"
    c.add("#10 droppers: fee-affected ones affected, exempt ones not",
          droppers.loc[~exempt, "is_fee_affected"].all() and not droppers.loc[exempt, "is_fee_affected"].any(),
          f"{(~exempt).sum()} affected, {exempt.sum()} exempt (confusion)")

    # ---- #5 network not device ----
    net = gen[gen["archetype"] == "network_not_device"]
    ok5, worst5 = True, np.inf
    for _, r in net.iterrows():
        g = dev[(dev["merchant_id"] == r["merchant_id"]) & (dev["date"] >= _d(r["onset_date"]))]
        pk, off = g["network_errors_peak"].mean(), g["network_errors_offpeak"].mean()
        worst5 = min(worst5, pk / max(off, 0.1))
        ok5 &= pk >= 5 and pk >= 5 * off and not dev[dev["merchant_id"] == r["merchant_id"]]["hardware_fault_flag"].any()
    c.add("#5 network: peak errors >= 5x off-peak, no hardware fault", ok5 and len(net) > 0,
          f"{len(net)} merchants, smallest peak/off-peak ratio {worst5:.1f}x")

    # ---- #9 charge complainers ----
    ded, tk = t["deductions"], t["tickets"].assign(opened=lambda d: pd.to_datetime(d["opened_at"]).dt.normalize())
    bad9 = []
    for mid in gen.loc[gen["archetype"] == "charge_complainer", "merchant_id"]:
        un = ded[(ded["merchant_id"] == mid) & ~ded["notified"]]
        ct = tk[(tk["merchant_id"] == mid) & (tk["category"] == "charge")]
        if len(un) != 1 or not any(0 <= (o - _d(un["date"].iloc[0])).days <= 5 for o in ct["opened"]):
            bad9.append(mid)
    c.add("#9 charge: one unnotified deduction, charge ticket within 5 days", not bad9,
          f"{(gen['archetype'] == 'charge_complainer').sum()} merchants; bad {bad9}")
    c.add("unnotified deductions only on charge complainers",
          set(ded.loc[~ded["notified"], "merchant_id"]) <= set(gen.loc[gen["archetype"] == "charge_complainer", "merchant_id"]))

    # ---- scripted merchants ----
    def series(mid, col="upi_count"):
        return txn[txn["merchant_id"] == mid].set_index("date")[col]

    def mean_between(s, a, b):
        return s[(s.index >= a) & (s.index <= b)].mean()

    ms = m.set_index("merchant_id")
    small_fm = fm[fm["merchant_id"] == "M_DEMO_SMALL"]
    c.add("SMALL: QR only, monthly UPI 30-40k, no payment above 2,000, not fee-affected, no tickets",
          ms.loc["M_DEMO_SMALL", "has_qr"] and not ms.loc["M_DEMO_SMALL", ["has_soundbox", "has_pos"]].any()
          and small_fm["monthly_upi_value"].between(30000, 40000).all()
          and series("M_DEMO_SMALL", "upi_count_above_2000").sum() == 0
          and not lab.set_index("merchant_id").loc["M_DEMO_SMALL", "is_fee_affected"]
          and not (tk["merchant_id"] == "M_DEMO_SMALL").any(),
          f"monthly {small_fm['monthly_upi_value'].min():,.0f}-{small_fm['monthly_upi_value'].max():,.0f}")

    rc = series("M_DEMO_RAVI")
    rdev = dev[dev["merchant_id"] == "M_DEMO_RAVI"].set_index("date")
    fault_days = set(rdev.index[rdev["hardware_fault_flag"]])
    expect_fault = {day("D", k) for k in range(-6, 1)}
    c.add("RAVI: Soundbox offline + hardware fault exactly D-6..D0, back online D+1",
          fault_days == expect_fault and rdev.loc[day("D", 1), "uptime_pct"] >= 98, f"{len(fault_days)} fault days")
    rt = tk[tk["merchant_id"] == "M_DEMO_RAVI"]
    c.add("RAVI: device complaint ticket opened D-4, still open",
          len(rt) == 1 and rt["opened"].iloc[0] == day("D", -4) and rt["status"].iloc[0] == "open"
          and rt["category"].iloc[0] == "device", rt["summary"].iloc[0] if len(rt) else "")
    base = mean_between(rc, day("D", -27), day("D", -21))
    at_d0 = mean_between(rc, day("D", -2), day("D", 0))
    c.add("RAVI: UPI count down ~40% over 3 weeks (D-2..D0 vs D-27..D-21)", 0.50 <= at_d0 / base <= 0.70,
          f"{(1 - at_d0 / base):.0%} down")
    rec = mean_between(rc, day("D", 5), day("D", 7))
    c.add("RAVI: recovered by D+5..D+7 (>= 95% of pre-decline)", rec / base >= 0.95, f"{rec / base:.0%} of baseline")
    pre_g, post_g = mean_between(rc, day("G", -3), day("G", -1)), mean_between(rc, day("G", 4), day("G", 8))
    c.add("RAVI: post-go-live dip ~25% (G+4..G+8 vs G-3..G-1)", post_g / pre_g <= 0.85, f"{(1 - post_g / pre_g):.0%} down")
    ravi_fee = fm_ref.loc["M_DEMO_RAVI"]
    c.add("RAVI: fee-affected, est. fee ~500-600/month, monthly UPI ~2.5-3L",
          ravi_fee["is_fee_affected"] and 450 <= ravi_fee["est_monthly_fee"] <= 650
          and 230000 <= ravi_fee["monthly_upi_value"] <= 320000,
          f"Oct UPI {ravi_fee['monthly_upi_value']:,.0f}, fee {ravi_fee['est_monthly_fee']:,.0f}")

    meena = fm[fm["merchant_id"] == "M_DEMO_MEENA"]
    c.add("MEENA: monthly UPI 88-98k in each completed month, not fee-affected, Zone B",
          meena["monthly_upi_value"].between(88000, 98000).all() and not meena["is_fee_affected"].any()
          and ms.loc["M_DEMO_MEENA", "zone_id"] == "Z_B",
          ", ".join(f"{v:,.0f}" for v in meena["monthly_upi_value"]))

    # ---- labels ----
    c.add("labels: exactly one row per merchant",
          lab["merchant_id"].is_unique and set(lab["merchant_id"]) == set(m["merchant_id"]))
    leaks = [str(p.relative_to(REPO_ROOT)) for d in ("mcp_servers", "agent") for p in (REPO_ROOT / d).rglob("*")
             if p.is_file() and p.suffix in {".py", ".md", ".txt", ".yaml", ".json", ".sql"}
             and "merchant_labels" in p.read_text(encoding="utf-8", errors="ignore")]
    c.add("labels: merchant_labels not referenced in mcp_servers/ or agent/", not leaks, ", ".join(leaks))

    # ---- determinism ----
    if determinism:
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run([sys.executable, "-m", "data.generator", "--out", tmp], cwd=REPO_ROOT, check=True,
                           capture_output=True)
            files = sorted(p.name for p in out.iterdir() if p.suffix in {".parquet", ".csv", ".json"})
            diff = [f for f in files if _sha(out / f) != _sha(Path(tmp) / f)]
        c.add("determinism: same seed -> identical file hashes", not diff, f"{len(files)} files compared" + (f"; differ: {diff}" if diff else ""))
    return c


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else "missing"


# ------------------------------------------------------------------ charts

def _style(ax) -> None:
    ax.set_facecolor(SURFACE)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(AXIS)
    ax.tick_params(colors=MUTED, labelsize=8)
    ax.grid(axis="y", color=GRID, linewidth=0.6)
    ax.set_axisbelow(True)


def chart_volume_by_archetype(txn: pd.DataFrame, lab: pd.DataFrame, cal: Calendar, path: Path) -> None:
    d = txn.merge(lab[["merchant_id", "archetype"]])
    d["date"] = pd.to_datetime(d["date"])
    daily = d.groupby(["archetype", "date"])["upi_count"].sum().unstack(0)
    weekly = daily.rolling(7, center=True, min_periods=4).mean()
    indexed = 100 * weekly / daily.iloc[:28].mean()
    order = [a for a in ["healthy_steady", "festive_grower", "slow_decliner", "device_fault", "network_not_device",
                         "threshold_holder", "low_active", "closed_shop", "charge_complainer", "post_fee_dropper",
                         "scripted"] if a in indexed]
    fig, axes = plt.subplots(3, 4, figsize=(13, 7.5), sharex=True, sharey=True, facecolor=SURFACE)
    for ax, a in zip(axes.flat, order):
        _style(ax)
        ax.plot(indexed.index, indexed[a], color=SERIES[0], linewidth=2)
        for x, lbl, ha in ((cal.d0, "D0 ", "right"), (cal.g0, " G0", "left")):
            ax.axvline(pd.Timestamp(x), color=MUTED, linewidth=0.8, linestyle="--")
            ax.text(pd.Timestamp(x), 158, lbl, color=INK_2, fontsize=7, ha=ha)
        ax.set_title(f"{a.replace('_', ' ')} (n={lab['archetype'].eq(a).sum()})", color=INK, fontsize=9, loc="left")
        ax.set_ylim(0, 165)
    for ax in axes.flat[len(order):]:
        ax.set_visible(False)
    for ax in axes[-1]:
        ax.xaxis.set_major_formatter(matplotlib.dates.DateFormatter("%d %b"))
        ax.tick_params(axis="x", rotation=45)
    fig.suptitle("UPI transaction count by archetype, 7-day average, indexed to first 4 weeks = 100",
                 color=INK, fontsize=11, x=0.01, ha="left")
    fig.tight_layout()
    fig.savefig(path, dpi=110, facecolor=SURFACE)
    plt.close(fig)


def chart_fee_by_category(m: pd.DataFrame, lab: pd.DataFrame, path: Path) -> None:
    d = m[["merchant_id", "category"]].merge(lab[["merchant_id", "is_fee_affected"]])
    g = d.groupby("category")["is_fee_affected"].agg(["mean", "sum", "count"]).sort_values("mean")
    fig, ax = plt.subplots(figsize=(7.5, 4), facecolor=SURFACE)
    _style(ax)
    ax.grid(axis="y", visible=False)
    ax.grid(axis="x", color=GRID, linewidth=0.6)
    ax.barh(g.index, g["mean"] * 100, color=SERIES[0], height=0.6)
    for y, (_, r) in enumerate(g.iterrows()):
        ax.text(r["mean"] * 100 + 1, y, f"{int(r['sum'])}/{int(r['count'])}", va="center", color=INK_2, fontsize=8)
    ax.set_xlim(0, 110)
    ax.set_xlabel("% of merchants fee-affected (reference month)", color=INK_2, fontsize=9)
    ax.set_title("Fee-affected merchants by category", color=INK, fontsize=11, loc="left")
    fig.tight_layout()
    fig.savefig(path, dpi=110, facecolor=SURFACE)
    plt.close(fig)


def chart_tickets(tk: pd.DataFrame, cal: Calendar, path: Path) -> None:
    d = tk[pd.to_datetime(tk["opened_at"]) < pd.Timestamp(cal.d0) + timedelta(days=1)].copy()
    d["state"] = np.where(d["status"] == "resolved", "resolved by D0", "open at D0")
    g = d.groupby(["category", "state"]).size().unstack(fill_value=0)
    g = g.reindex(columns=["open at D0", "resolved by D0"], fill_value=0)
    g = g.loc[g.sum(axis=1).sort_values().index]
    fig, ax = plt.subplots(figsize=(7.5, 3.6), facecolor=SURFACE)
    _style(ax)
    ax.grid(axis="y", visible=False)
    ax.grid(axis="x", color=GRID, linewidth=0.6)
    left = np.zeros(len(g))
    for col, color in zip(g.columns, SERIES):
        ax.barh(g.index, g[col], left=left, color=color, height=0.6, label=col, edgecolor=SURFACE, linewidth=2)
        left += g[col].to_numpy()
    for y, total in enumerate(left):
        ax.text(total + 0.5, y, str(int(total)), va="center", color=INK_2, fontsize=8)
    ax.legend(frameon=False, fontsize=8, labelcolor=INK_2, loc="lower right")
    ax.set_title("Tickets opened up to D0, by category", color=INK, fontsize=11, loc="left")
    fig.tight_layout()
    fig.savefig(path, dpi=110, facecolor=SURFACE)
    plt.close(fig)


# ------------------------------------------------------------------ report

def _md_table(df: pd.DataFrame) -> str:
    cols = [str(c) for c in df.columns]
    lines = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    lines += ["| " + " | ".join(str(v) for v in row) + " |" for row in df.itertuples(index=False)]
    return "\n".join(lines)


def write_summary(t: dict[str, pd.DataFrame], checks: Checks, cfg: dict, cal: Calendar, gp: dict, out: Path) -> Path:
    m, lab, tk = t["merchants"], t["merchant_labels"], t["tickets"]
    txn = pd.concat([t["txn_daily"], t["txn_daily_future"]], ignore_index=True)
    charts = out / "charts"
    charts.mkdir(exist_ok=True)
    chart_volume_by_archetype(txn, lab, cal, charts / "volume_by_archetype.png")
    chart_fee_by_category(m, lab, charts / "fee_affected_by_category.png")
    chart_tickets(tk, cal, charts / "ticket_categories.png")

    zone = m.groupby("zone_id").agg(merchants=("merchant_id", "size"),
                                    soundbox=("has_soundbox", "sum"), pos=("has_pos", "sum")).reset_index()
    zone = zone.merge(t["field_agents"].groupby("zone_id").size().rename("field_agents").reset_index())
    cat = pd.crosstab(m["category"], m["zone_id"], margins=True, margins_name="total").reset_index()
    arch_cfg = cfg["archetypes"]["archetypes"]
    a = lab.groupby("archetype").agg(n=("merchant_id", "size"), save=("expected_line", lambda s: (s == "save").sum()),
                                     grow=("expected_line", lambda s: (s == "grow").sum()),
                                     escalate=("expected_escalation", "sum"),
                                     fee_affected=("is_fee_affected", "sum")).reset_index()
    a["config share"] = a["archetype"].map(lambda k: f"{arch_cfg[k]['share']:.0%}" if k in arch_cfg else "scripted")
    n_gen = (lab["archetype"] != "scripted").sum()
    a["actual share"] = a.apply(lambda r: f"{r['n'] / n_gen:.1%}" if r["archetype"] != "scripted" else "", axis=1)

    opened = pd.to_datetime(tk["opened_at"])
    resolved = pd.to_datetime(tk["resolved_at"])
    eod = pd.Timestamp(cal.d0) + timedelta(days=1)
    open_d0 = tk[(opened < eod) & (resolved.isna() | (resolved >= eod))]
    fm = t["merchant_fee_monthly"]
    ref = fm[pd.to_datetime(fm["month"]) == pd.Timestamp(cfg["sim_config"]["fee_rules"]["reference_month"] + "-01")]
    aff = ref[ref["is_fee_affected"]]

    d = txn.assign(date=pd.to_datetime(txn["date"]))
    key_days = [("D-21", cal.at("D", -21)), ("D-6", cal.at("D", -6)), ("D0", cal.i_d0), ("D+3", cal.at("D", 3)),
                ("D+7", cal.at("D", 7)), ("G0", cal.i_g0), ("G+5", cal.at("G", 5))]
    demo = pd.DataFrame({lbl: [int(d[(d["merchant_id"] == mid) & (d["date"] == pd.Timestamp(cal.date(i)))]["upi_count"].iloc[0])
                               for mid in ("M_DEMO_SMALL", "M_DEMO_RAVI", "M_DEMO_MEENA")] for lbl, i in key_days})
    demo.insert(0, "merchant", ["M_DEMO_SMALL", "M_DEMO_RAVI", "M_DEMO_MEENA"])

    chk = pd.DataFrame([{"check": n, "result": "PASS" if ok else "**FAIL**", "detail": det} for n, ok, det in checks.rows])
    lines = [
        "# AX-13 Phase 0 — dataset summary",
        "",
        f"Seed **{gp['seed']}** · D0 {cal.d0} · fee go-live (G0) {cal.g0} · window {cal.start} → {cal.end} "
        f"({cal.n_hist} history + {cal.n_future} future days). All data synthetic.",
        "",
        f"**Validation: {len(checks.rows) - len(checks.failed)}/{len(checks.rows)} checks passed.**",
        "",
        _md_table(chk),
        "",
        "## Counts by zone",
        _md_table(zone),
        "",
        "## Counts by category",
        _md_table(cat),
        "",
        "## Counts by archetype (hidden labels)",
        _md_table(a[["archetype", "n", "config share", "actual share", "save", "grow", "escalate", "fee_affected"]]),
        "",
        "## Fee (proposed framework, as reported)",
        f"- Fee-affected merchants in reference month {cfg['sim_config']['fee_rules']['reference_month']}: "
        f"**{len(aff)} of {len(m)} ({len(aff) / len(m):.0%})**.",
        f"- Estimated monthly fee among them: median ₹{aff['est_monthly_fee'].median():,.0f}, "
        f"range ₹{aff['est_monthly_fee'].min():,.0f}–₹{aff['est_monthly_fee'].max():,.0f}.",
        f"- Exempt (monthly UPI < ₹1L): {(ref['monthly_upi_value'] < 100000).sum()} merchants.",
        "",
        "![fee-affected by category](charts/fee_affected_by_category.png)",
        "",
        "## Tickets",
        f"- {len(tk)} tickets in the window; **{len(open_d0)} open or in progress at D0** "
        f"({', '.join(f'{k} {v}' for k, v in open_d0['category'].value_counts().items())}).",
        f"- {(opened >= eod).sum()} tickets dated in the future window (released by the clock).",
        "",
        "![ticket categories](charts/ticket_categories.png)",
        "",
        "## Volume by archetype over time",
        "![volume by archetype](charts/volume_by_archetype.png)",
        "",
        "## Demo merchants — UPI count on key days",
        _md_table(demo),
        "",
    ]
    path = out / "summary.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=get_settings().data_out_dir)
    ap.add_argument("--skip-determinism", action="store_true")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    cfg = load_configs()
    cal = Calendar(cfg["sim_config"])
    t = load(args.out)
    gp = json.loads((args.out / "gen_params.json").read_text(encoding="utf-8"))
    checks = run_checks(t, cfg, cal, gp, args.out, determinism=not args.skip_determinism)
    path = write_summary(t, checks, cfg, cal, gp, args.out)

    for name, ok, detail in checks.rows:
        print(f"{'PASS' if ok else 'FAIL':<6}{name}" + (f"  — {detail}" if detail else ""))
    print(f"\nSummary report → {path}")
    if checks.failed:
        print(f"\n{len(checks.failed)} check(s) FAILED.")
        return 1
    print("\nAll checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
