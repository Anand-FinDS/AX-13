"""Mock ticketing system: list, open, update and chase support tickets.

    python -m mcp_servers.ticketing
"""
from __future__ import annotations

from typing import Literal

from mcp.server.fastmcp import FastMCP

from mcp_servers.db import ToolError, check_citation, new_id, one, pool, query, require_merchant, sim_ts, write_action

mcp = FastMCP("ticketing", log_level="WARNING")
Line = Literal["save", "grow", "none"]


def _visible_ticket(ticket_id: str) -> dict:
    t = one("SELECT * FROM v_tickets WHERE ticket_id = %s", (ticket_id,))
    if not t:
        raise ToolError(f"Unknown ticket_id {ticket_id!r}")
    return t


@mcp.tool()
def list_tickets(merchant_id: str, status: Literal["open", "in_progress", "resolved"] | None = None) -> list[dict]:
    """Tickets for a merchant up to the simulation date, optionally filtered by status."""
    require_merchant(merchant_id)
    return query("SELECT * FROM v_tickets WHERE merchant_id = %s AND (%s::text IS NULL OR status = %s) ORDER BY opened_at",
                 (merchant_id, status, status))


@mcp.tool()
def create_ticket(merchant_id: str, category: Literal["device", "settlement", "charge", "failed_txn", "other"],
                  summary: str, channel: Literal["call", "app", "field"], evidence: list[str], playbook_ref: str,
                  line: Line = "save", sentiment: Literal["calm", "frustrated", "angry"] = "calm") -> dict:
    """Open a ticket. Requires evidence and a playbook section."""
    check_citation(evidence, playbook_ref, line)
    require_merchant(merchant_id)
    ticket_id = new_id("TA")
    with pool().connection() as conn:
        conn.execute("INSERT INTO tickets (ticket_id, merchant_id, opened_at, resolved_at, channel, category, status,"
                     " summary, sentiment) VALUES (%s, %s, %s, NULL, %s, %s, 'open', %s, %s)",
                     (ticket_id, merchant_id, sim_ts(), channel, category, summary, sentiment))
        action_id = write_action(conn, merchant_id, line, "ticket_create", {"ticket_id": ticket_id, "category": category,
                                 "summary": summary}, evidence, playbook_ref, "agent_alone", "done")
    return {"ticket_id": ticket_id, "action_id": action_id, "status": "open"}


@mcp.tool()
def update_ticket(ticket_id: str, status: Literal["open", "in_progress", "resolved"], note: str,
                  evidence: list[str], playbook_ref: str, line: Line = "save") -> dict:
    """Change a ticket's status; 'resolved' stamps resolved_at in simulation time."""
    check_citation(evidence, playbook_ref, line)
    t = _visible_ticket(ticket_id)
    ts = sim_ts()
    with pool().connection() as conn:
        conn.execute("UPDATE tickets SET status = %s, resolved_at = %s WHERE ticket_id = %s",
                     (status, ts if status == "resolved" else None, ticket_id))
        action_id = write_action(conn, t["merchant_id"], line, "ticket_update",
                                 {"ticket_id": ticket_id, "from": t["status"], "to": status, "note": note},
                                 evidence, playbook_ref, "agent_alone", "done")
    return {"ticket_id": ticket_id, "status": status, "action_id": action_id}


@mcp.tool()
def chase_ticket(ticket_id: str, note: str, evidence: list[str], playbook_ref: str, line: Line = "save") -> dict:
    """Chase an unresolved ticket with the owning team (logged; the mock sends nothing)."""
    check_citation(evidence, playbook_ref, line)
    t = _visible_ticket(ticket_id)
    if t["status"] == "resolved":
        raise ToolError(f"Ticket {ticket_id} is already resolved.")
    with pool().connection() as conn:
        action_id = write_action(conn, t["merchant_id"], line, "ticket_chase", {"ticket_id": ticket_id, "note": note},
                                 evidence, playbook_ref, "agent_alone", "done")
    return {"ticket_id": ticket_id, "action_id": action_id, "chased": True}


if __name__ == "__main__":
    mcp.run()
