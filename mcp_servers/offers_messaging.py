"""Mock merchant messaging, offers and waivers.

    python -m mcp_servers.offers_messaging

Backstops for the Phase 3 gates: loan offers need an approved approval; waivers above the
per-merchant cap or beyond the zone budget need one too (Autonomy policy §3).
"""
from __future__ import annotations

import math
from datetime import timedelta
from functools import lru_cache
from typing import Literal

import yaml
from mcp.server.fastmcp import FastMCP

from common.settings import get_settings
from mcp_servers.db import (
    ToolError,
    approved_action,
    check_citation,
    new_id,
    one,
    pool,
    query,
    require_merchant,
    sim_date,
    sim_ts,
    write_action,
)

mcp = FastMCP("offers_messaging", log_level="WARNING")
Line = Literal["save", "grow", "none"]


@mcp.tool()
def send_message(merchant_id: str, channel: Literal["whatsapp", "sms", "app"], text: str, evidence: list[str],
                 playbook_ref: str, line: Line = "save", english: str | None = None) -> dict:
    """Send the merchant a message (mock: logged and copied to the CRM, nothing is sent). `english` is the
    English version kept for the audit log when the message is in another language."""
    check_citation(evidence, playbook_ref, line)
    require_merchant(merchant_id)
    with pool().connection() as conn:
        action_id = write_action(conn, merchant_id, line, "message", {"channel": channel, "text": text, "english": english},
                                 evidence, playbook_ref, "agent_alone", "sent")
        conn.execute("INSERT INTO crm_notes (note_id, merchant_id, ts, source, text) VALUES (%s, %s, %s, 'agent', %s)",
                     (new_id("N"), merchant_id, sim_ts(), f"[{channel}] {text}"))
    return {"action_id": action_id, "channel": channel, "status": "sent"}


@mcp.tool()
def make_offer(merchant_id: str, product: Literal["soundbox", "pos", "loan", "insurance", "plan_change"],
               channel: Literal["app", "whatsapp", "field"], text: str, evidence: list[str], playbook_ref: str,
               line: Line = "grow", approval_id: str | None = None, english: str | None = None) -> dict:
    """Make a catalogue offer. Loan offers require an approved approval_id (Compliance rules §5)."""
    check_citation(evidence, playbook_ref, line)
    require_merchant(merchant_id)
    approval = approved_action(approval_id, merchant_id, "loan_offer") if product == "loan" else None
    offer_id = new_id("OA")
    with pool().connection() as conn:
        conn.execute("INSERT INTO offers_history (offer_id, merchant_id, date, product, channel, response) "
                     "VALUES (%s, %s, %s, %s, %s, 'pending')", (offer_id, merchant_id, sim_date(), product, channel))
        action_id = write_action(conn, merchant_id, line, "offer",
                                 {"offer_id": offer_id, "product": product, "channel": channel, "text": text, "english": english,
                                  "approval_id": approval_id}, evidence, playbook_ref,
                                 "human_approves" if approval else "agent_alone", "sent")
        if approval:
            conn.execute("UPDATE actions SET status = 'executed' WHERE action_id = %s", (approval["action_id"],))
    return {"offer_id": offer_id, "action_id": action_id, "product": product, "response": "pending"}


def _waiver_spend(zone_id: str, merchant_id: str | None = None) -> float:
    d = sim_date()
    r = one("SELECT coalesce(sum((a.payload_json->>'amount')::numeric), 0) AS spent FROM actions a "
            "JOIN merchants m USING (merchant_id) WHERE a.action_type = 'waiver' AND a.status = 'granted' "
            "AND m.zone_id = %s AND (%s::text IS NULL OR a.merchant_id = %s) "
            "AND a.ts >= %s AND a.ts < (%s::date + interval '1 month')",
            (zone_id, merchant_id, merchant_id, d.replace(day=1), d.replace(day=1)))
    return float(r["spent"])


@mcp.tool()
def get_waiver_budget(zone_id: str) -> dict:
    """Zone waiver budget for the current simulation month: cap, spent, remaining, per-merchant cap."""
    z = one("SELECT * FROM zones WHERE zone_id = %s", (zone_id,))
    if not z:
        raise ToolError(f"Unknown zone_id {zone_id!r}")
    spent = _waiver_spend(zone_id)
    return {"zone_id": zone_id, "month": sim_date().strftime("%Y-%m"), "budget": z["waiver_budget_month"],
            "spent": spent, "remaining": z["waiver_budget_month"] - spent, "cap_per_merchant": z["waiver_cap_per_merchant"]}


@mcp.tool()
def grant_waiver(merchant_id: str, amount: float, reason: str, evidence: list[str], playbook_ref: str,
                 line: Line = "save", approval_id: str | None = None) -> dict:
    """Grant a fee or rental waiver. Within the per-merchant cap and zone budget the agent acts alone;
    otherwise an approved approval_id for a waiver of at least this amount is required."""
    check_citation(evidence, playbook_ref, line)
    m = require_merchant(merchant_id)
    if amount <= 0:
        raise ToolError("Waiver amount must be positive.")
    budget = get_waiver_budget(m["zone_id"])
    merchant_spent = _waiver_spend(m["zone_id"], merchant_id)
    within = merchant_spent + amount <= budget["cap_per_merchant"] and amount <= budget["remaining"]
    if not within:
        a = approved_action(approval_id, merchant_id, "waiver")
        if float(a["payload_json"].get("amount", 0)) < amount:
            raise ToolError(f"Rejected: approval {approval_id} covers ₹{a['payload_json'].get('amount')}, not ₹{amount}.")
    with pool().connection() as conn:
        action_id = write_action(conn, merchant_id, line, "waiver", {"amount": amount, "reason": reason,
                                 "approval_id": None if within else approval_id}, evidence, playbook_ref,
                                 "agent_alone" if within else "human_approves", "granted")
        if not within:
            conn.execute("UPDATE actions SET status = 'executed' WHERE action_id = (SELECT action_id FROM approvals "
                         "WHERE approval_id = %s)", (approval_id,))
    return {"action_id": action_id, "amount": amount, "tier": "agent_alone" if within else "human_approves",
            "merchant_spent_this_month": merchant_spent + amount}


@lru_cache(maxsize=1)
def _lender() -> dict:
    return yaml.safe_load((get_settings().data_config_dir / "lender.yaml").read_text(encoding="utf-8"))


@mcp.tool()
def get_loan_quote(merchant_id: str) -> dict:
    """The partner lender's written in-principle offer for a merchant (mock lender, illustrative terms):
    lender, amount, tenure, APR, charges and Key Fact Statement link, or the lender's reason for no offer.
    The lender decides; AX-13 only relays the terms (Product catalogue §4)."""
    m = require_merchant(merchant_id)
    cfg = _lender()
    a, t = cfg["assessment"], cfg["terms"]
    months = query("SELECT month, monthly_upi_value FROM v_merchant_fee_monthly WHERE merchant_id = %s "
                   "ORDER BY month DESC LIMIT %s", (merchant_id, a["min_completed_months"]))
    if a["requires_soundbox"] and not m["has_soundbox"]:
        return {"merchant_id": merchant_id, "lender": cfg["lender_name"], "offer": False, "reason": "No Soundbox in place"}
    if len(months) < a["min_completed_months"]:
        return {"merchant_id": merchant_id, "lender": cfg["lender_name"], "offer": False,
                "reason": f"Fewer than {a['min_completed_months']} completed months of recorded UPI"}
    avg = sum(r["monthly_upi_value"] for r in months) / len(months)
    step = a["amount_round_down_to"]
    amount = min(a["amount_max"], math.floor(avg * a["amount_months_of_upi"] / step) * step)
    if amount < a["amount_min"]:
        return {"merchant_id": merchant_id, "lender": cfg["lender_name"], "offer": False,
                "reason": f"Recorded UPI too low for the minimum loan of ₹{a['amount_min']:,}"}
    quote_id = f"Q-{merchant_id}-{sim_date():%Y%m%d}"
    return {"merchant_id": merchant_id, "lender": cfg["lender_name"], "offer": True, "quote_id": quote_id,
            "amount": amount, "tenure_months": t["tenure_months"], "apr_pct": t["apr_pct"],
            "processing_fee": f"{t['processing_fee_pct']:g}% of the loan amount plus GST",
            "prepayment": t["prepayment"], "late_payment": t["late_payment"],
            "kfs_link": cfg["kfs_base_url"] + quote_id,
            "valid_until": (sim_date() + timedelta(days=t["valid_days"])).isoformat(),
            "basis": f"average recorded UPI of the last {len(months)} completed months: ₹{avg:,.0f}"}


@mcp.tool()
def get_offers(merchant_id: str) -> list[dict]:
    """Past offers to a merchant and their responses, up to the simulation date."""
    require_merchant(merchant_id)
    return query("SELECT * FROM v_offers_history WHERE merchant_id = %s ORDER BY date", (merchant_id,))


if __name__ == "__main__":
    mcp.run()
