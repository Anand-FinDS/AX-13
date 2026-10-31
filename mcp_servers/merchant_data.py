"""Mock merchant data system (read only): profile, payments, devices, settlements, fees, peers.

    python -m mcp_servers.merchant_data      # stdio MCP server

All time-dependent reads use the v_* views, which stop at the simulation date.
"""
from __future__ import annotations

from datetime import date, timedelta
from statistics import median
from typing import Literal

from mcp.server.fastmcp import FastMCP

from data.generators.context import load_configs
from mcp_servers.db import ToolError, one, query, require_merchant, sim_date

mcp = FastMCP("merchant_data", log_level="WARNING")
FEE = load_configs()["sim_config"]["fee_rules"]


@mcp.tool()
def list_merchants(zone_id: str | None = None, category: str | None = None,
                   value_tier: str | None = None) -> list[dict]:
    """List merchants, optionally filtered by zone, category or value tier (low/mid/high)."""
    return query(
        "SELECT merchant_id, name, category, zone_id, merchant_value_tier, has_soundbox, has_pos "
        "FROM merchants WHERE (%(z)s::text IS NULL OR zone_id = %(z)s) AND (%(c)s::text IS NULL OR category = %(c)s) "
        "AND (%(t)s::text IS NULL OR merchant_value_tier = %(t)s) ORDER BY merchant_id",
        {"z": zone_id, "c": category, "t": value_tier})


@mcp.tool()
def get_merchant_profile(merchant_id: str) -> dict:
    """Merchant master record with zone and assigned field agent."""
    require_merchant(merchant_id)
    return one(
        "SELECT m.*, z.name AS zone_name, a.name AS assigned_agent_name FROM merchants m "
        "JOIN zones z USING (zone_id) JOIN field_agents a ON a.agent_id = m.assigned_agent_id "
        "WHERE m.merchant_id = %s", (merchant_id,))


@mcp.tool()
def get_txn_daily(merchant_id: str, days: int = 30) -> list[dict]:
    """Daily UPI/card aggregates for the last `days` days up to the simulation date."""
    require_merchant(merchant_id)
    return query("SELECT * FROM v_txn_daily WHERE merchant_id = %s AND date > %s ORDER BY date",
                 (merchant_id, sim_date() - timedelta(days=days)))


@mcp.tool()
def get_device_health(merchant_id: str, days: int = 14) -> list[dict]:
    """Daily Soundbox/POS health (uptime, offline minutes, peak vs off-peak network errors, hardware fault)."""
    require_merchant(merchant_id)
    return query("SELECT * FROM v_device_health_daily WHERE merchant_id = %s AND date > %s ORDER BY device_type, date",
                 (merchant_id, sim_date() - timedelta(days=days)))


@mcp.tool()
def get_settlements(merchant_id: str, days: int = 14) -> list[dict]:
    """Settlements (T+1) for the last `days` days, with status and delay."""
    require_merchant(merchant_id)
    return query("SELECT * FROM v_settlements WHERE merchant_id = %s AND date > %s ORDER BY date",
                 (merchant_id, sim_date() - timedelta(days=days)))


@mcp.tool()
def get_deductions(merchant_id: str, days: int = 90) -> list[dict]:
    """Deductions from settlements (rent, maintenance charges, UPI fee, other), with notified flag."""
    require_merchant(merchant_id)
    return query("SELECT * FROM v_deductions WHERE merchant_id = %s AND date > %s ORDER BY date",
                 (merchant_id, sim_date() - timedelta(days=days)))


@mcp.tool()
def get_fee_summary(merchant_id: str) -> dict:
    """Proposed UPI fee position: completed months, the current month so far, and which
    message variant applies (Retention playbook §6.2)."""
    require_merchant(merchant_id)
    today = sim_date()
    months = query("SELECT month, monthly_upi_value, value_above_2000, is_fee_affected, est_monthly_fee "
                   "FROM v_merchant_fee_monthly WHERE merchant_id = %s ORDER BY month", (merchant_id,))
    cur = one("SELECT coalesce(sum(upi_value),0) AS upi_value, coalesce(sum(upi_value_above_2000),0) AS value_above_2000,"
              " coalesce(sum(upi_count_above_2000),0) AS count_above_2000, count(*) AS days "
              "FROM v_txn_daily WHERE merchant_id = %s AND date >= %s", (merchant_id, today.replace(day=1)))
    last = months[-1] if months else None
    variant = None
    if last and not last["is_fee_affected"]:
        variant = "A_exempt" if last["monthly_upi_value"] < FEE["exempt_below_monthly_upi"] else "B_no_payments_above_2000"
    est_so_far = round(FEE["rate"] * cur["value_above_2000"], 2)
    return {
        "sim_date": today.isoformat(),
        "rules": {"rate": FEE["rate"], "applies_above": FEE["applies_above_txn_value"], "cap_per_txn": FEE["cap_per_txn"],
                  "exempt_below_monthly_upi": FEE["exempt_below_monthly_upi"], "basis": "proposed framework, as reported"},
        "completed_months": months,
        "last_full_month": last,
        "not_affected_variant": variant,
        "current_month_to_date": {**cur, "est_fee_if_not_exempt": est_so_far,
                                  "over_exemption_so_far": cur["upi_value"] >= FEE["exempt_below_monthly_upi"]},
    }


@mcp.tool()
def get_peer_change(merchant_id: str, base_start: str, base_end: str, comp_start: str, comp_end: str,
                    exclude_fee_affected: bool = False,
                    scope: Literal["category_zone", "zone"] = "category_zone") -> dict:
    """Seasonality check (Retention playbook §2.3): change in mean daily UPI count between two periods
    for this merchant vs same-category, same-zone peers (or all merchants in the zone with
    scope='zone'). Returns aggregates only. Set exclude_fee_affected=True from fee go-live onward."""
    m = require_merchant(merchant_id)
    b0, b1, c0, c1 = (date.fromisoformat(x) for x in (base_start, base_end, comp_start, comp_end))
    if max(b1, c1) > sim_date():
        raise ToolError("Periods must end on or before the simulation date.")
    rows = query(
        "WITH last_month AS (SELECT DISTINCT ON (merchant_id) merchant_id, is_fee_affected FROM v_merchant_fee_monthly "
        "  ORDER BY merchant_id, month DESC), "
        "p AS (SELECT merchant_id FROM merchants LEFT JOIN last_month USING (merchant_id) "
        "  WHERE (category = %(cat)s OR %(all)s) AND zone_id = %(zone)s AND (merchant_id = %(mid)s OR NOT %(ex)s "
        "  OR NOT coalesce(is_fee_affected, false))) "
        "SELECT t.merchant_id, avg(upi_count) FILTER (WHERE date BETWEEN %(b0)s AND %(b1)s) AS base, "
        "       avg(upi_count) FILTER (WHERE date BETWEEN %(c0)s AND %(c1)s) AS comp "
        "FROM v_txn_daily t JOIN p USING (merchant_id) GROUP BY t.merchant_id",
        {"cat": m["category"], "all": scope == "zone", "zone": m["zone_id"], "mid": merchant_id,
         "ex": exclude_fee_affected,
         "b0": b0, "b1": b1, "c0": c0, "c1": c1})
    change = {r["merchant_id"]: r["comp"] / r["base"] - 1 for r in rows if r["base"] and r["comp"] is not None}
    own = change.pop(merchant_id, None)
    peers = sorted(change.values())
    if own is None or len(peers) < 4:
        raise ToolError(f"Not enough data: merchant change {own}, {len(peers)} peers.")
    q25 = peers[len(peers) // 4]
    return {"merchant_change": round(own, 4), "peer_n": len(peers), "peer_median": round(median(peers), 4),
            "peer_bottom_quarter_cutoff": round(q25, 4), "below_bottom_quarter": own < q25,
            "peer_group": (f"{m['category']} in {m['zone_id']}" if scope == "category_zone" else f"all categories in {m['zone_id']}")
            + (", not fee-affected" if exclude_fee_affected else "")}


@mcp.tool()
def get_merchant_view(merchant_id: str, days: int = 30) -> dict:
    """One-call merchant view for scoring: profile, volume trend, devices, open tickets, recent
    deductions, settlement issues, fee position, recent CRM notes and actions."""
    profile = get_merchant_profile(merchant_id)
    today = sim_date()
    txn = get_txn_daily(merchant_id, days)
    last7 = [r["upi_count"] for r in txn[-7:]]
    prior = [r["upi_count"] for r in txn[-28:-7]]
    devices = query(
        "SELECT device_type, min(uptime_pct) AS min_uptime_7d, count(*) FILTER (WHERE uptime_pct < 50) AS offline_days_7d, "
        "count(*) FILTER (WHERE hardware_fault_flag) AS fault_days_7d, round(avg(network_errors_peak),1) AS peak_errors_avg_7d, "
        "round(avg(network_errors_offpeak),1) AS offpeak_errors_avg_7d, max(last_heartbeat) AS last_heartbeat "
        "FROM v_device_health_daily WHERE merchant_id = %s AND date > %s GROUP BY device_type",
        (merchant_id, today - timedelta(days=7)))
    return {
        "sim_date": today.isoformat(),
        "profile": profile,
        "volume": {
            "avg_upi_count_last_7d": round(sum(last7) / len(last7), 1) if last7 else None,
            "avg_upi_count_prior_21d": round(sum(prior) / len(prior), 1) if prior else None,
            "change_vs_prior": round(sum(last7) / len(last7) / (sum(prior) / len(prior)) - 1, 4) if last7 and prior and sum(prior) else None,
            "daily": [{k: r[k] for k in ("date", "upi_count", "upi_value", "failed_count", "failed_debited_count")} for r in txn],
        },
        "devices": devices,
        "open_tickets": query("SELECT * FROM v_tickets WHERE merchant_id = %s AND status <> 'resolved' ORDER BY opened_at",
                              (merchant_id,)),
        "deductions_30d": get_deductions(merchant_id, 30),
        "settlement_issues_14d": [s for s in get_settlements(merchant_id, 14) if s["status"] != "settled"],
        "fee": get_fee_summary(merchant_id),
        "recent_notes": query("SELECT ts, source, text FROM crm_notes WHERE merchant_id = %s ORDER BY ts DESC LIMIT 5",
                              (merchant_id,)),
        "recent_actions": query("SELECT ts, line, action_type, status, playbook_ref FROM actions WHERE merchant_id = %s "
                                "ORDER BY ts DESC LIMIT 5", (merchant_id,)),
    }


@mcp.tool()
def list_field_agents(zone_id: str) -> list[dict]:
    """Field agents in a zone with languages and daily visit capacity."""
    return query("SELECT * FROM field_agents WHERE zone_id = %s ORDER BY agent_id", (zone_id,))


if __name__ == "__main__":
    mcp.run()
