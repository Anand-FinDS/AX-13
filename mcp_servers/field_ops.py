"""Mock field operations: technician bookings and field-agent visit plans.

    python -m mcp_servers.field_ops
"""
from __future__ import annotations

from datetime import date
from typing import Literal

from mcp.server.fastmcp import FastMCP

from mcp_servers.db import ToolError, check_citation, new_id, one, pool, query, require_merchant, sim_date, write_action

mcp = FastMCP("field_ops", log_level="WARNING")
Line = Literal["save", "grow", "none"]


def _future_date(d: str) -> date:
    out = date.fromisoformat(d)
    if out < sim_date():
        raise ToolError(f"{d} is before the simulation date {sim_date()}.")
    return out


@mcp.tool()
def book_technician(merchant_id: str, device_type: Literal["soundbox", "pos"], preferred_date: str,
                    evidence: list[str], playbook_ref: str, line: Line = "save",
                    time_window: str = "10:00-13:00") -> dict:
    """Book a technician visit for a device fault (Retention playbook §3.1). Not for network issues (§4.3)."""
    check_citation(evidence, playbook_ref, line)
    m = require_merchant(merchant_id)
    if not m["has_" + device_type]:
        raise ToolError(f"{merchant_id} has no {device_type}.")
    d = _future_date(preferred_date)
    payload = {"device_type": device_type, "date": d, "time_window": time_window}
    with pool().connection() as conn:
        action_id = write_action(conn, merchant_id, line, "technician_booking", payload, evidence, playbook_ref,
                                 "agent_alone", "booked")
    return {"booking_id": action_id, **payload, "date": d.isoformat()}


@mcp.tool()
def plan_visit(agent_id: str, merchant_id: str, planned_date: str, stop_type: Literal["retain", "pitch"], brief: str,
               evidence: list[str], playbook_ref: str, line: Line = "save", stop_order: int | None = None) -> dict:
    """Add a stop to a field agent's day plan with a one-line brief."""
    check_citation(evidence, playbook_ref, line)
    m = require_merchant(merchant_id)
    a = one("SELECT * FROM field_agents WHERE agent_id = %s", (agent_id,))
    if not a:
        raise ToolError(f"Unknown agent_id {agent_id!r}")
    if a["zone_id"] != m["zone_id"]:
        raise ToolError(f"{agent_id} works in {a['zone_id']}; {merchant_id} is in {m['zone_id']}.")
    d = _future_date(planned_date)
    visit_id = new_id("V")
    with pool().connection() as conn:
        conn.execute("INSERT INTO visits (visit_id, agent_id, merchant_id, planned_date, stop_order, stop_type, brief)"
                     " VALUES (%s, %s, %s, %s, %s, %s, %s)", (visit_id, agent_id, merchant_id, d, stop_order, stop_type, brief))
        action_id = write_action(conn, merchant_id, line, "visit_planned",
                                 {"visit_id": visit_id, "agent_id": agent_id, "date": d, "stop_type": stop_type, "brief": brief},
                                 evidence, playbook_ref, "agent_alone", "planned")
    return {"visit_id": visit_id, "action_id": action_id, "planned_date": d.isoformat()}


@mcp.tool()
def list_visits(agent_id: str | None = None, merchant_id: str | None = None, planned_date: str | None = None) -> list[dict]:
    """Planned and completed visits, filtered by agent, merchant or date."""
    return query("SELECT * FROM visits WHERE (%(a)s::text IS NULL OR agent_id = %(a)s) "
                 "AND (%(m)s::text IS NULL OR merchant_id = %(m)s) AND (%(d)s::date IS NULL OR planned_date = %(d)s) "
                 "ORDER BY planned_date, stop_order NULLS LAST", {"a": agent_id, "m": merchant_id, "d": planned_date})


@mcp.tool()
def record_visit_outcome(visit_id: str, outcome: str, evidence: list[str], playbook_ref: str, line: Line = "save",
                         transcript_id: str | None = None) -> dict:
    """Record what happened on a visit, optionally linking the visit transcript."""
    check_citation(evidence, playbook_ref, line)
    v = one("SELECT * FROM visits WHERE visit_id = %s", (visit_id,))
    if not v:
        raise ToolError(f"Unknown visit_id {visit_id!r}")
    with pool().connection() as conn:
        conn.execute("UPDATE visits SET outcome = %s, transcript_id = %s WHERE visit_id = %s",
                     (outcome, transcript_id, visit_id))
        action_id = write_action(conn, v["merchant_id"], line, "visit_outcome",
                                 {"visit_id": visit_id, "outcome": outcome, "transcript_id": transcript_id},
                                 evidence, playbook_ref, "agent_alone", "done")
    return {"visit_id": visit_id, "action_id": action_id}


if __name__ == "__main__":
    mcp.run()
