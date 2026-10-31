"""Mock CRM: notes, action history and approval requests.

    python -m mcp_servers.crm

Deciding an approval is a human-only action and is not exposed here (see app/admin.py).
"""
from __future__ import annotations

from typing import Literal

from mcp.server.fastmcp import FastMCP
from psycopg.types.json import Jsonb

from mcp_servers.db import (
    ToolError,
    check_citation,
    jsonable,
    new_id,
    one,
    pool,
    query,
    require_merchant,
    sim_ts,
    write_action,
)

mcp = FastMCP("crm", log_level="WARNING")
Line = Literal["save", "grow", "none"]


@mcp.tool()
def add_note(merchant_id: str, source: Literal["agent", "voice"], text: str, evidence: list[str], playbook_ref: str,
             line: Line = "none", structured: dict | None = None) -> dict:
    """Write a CRM note (agent summary or VoiceCore extraction)."""
    check_citation(evidence, playbook_ref, line)
    require_merchant(merchant_id)
    note_id = new_id("N")
    with pool().connection() as conn:
        conn.execute("INSERT INTO crm_notes (note_id, merchant_id, ts, source, text, structured_json) "
                     "VALUES (%s, %s, %s, %s, %s, %s)",
                     (note_id, merchant_id, sim_ts(), source, text, Jsonb(jsonable(structured)) if structured else None))
        action_id = write_action(conn, merchant_id, line, "crm_update", {"note_id": note_id, "source": source},
                                 evidence, playbook_ref, "agent_alone", "done")
    return {"note_id": note_id, "action_id": action_id}


@mcp.tool()
def get_notes(merchant_id: str, limit: int = 20) -> list[dict]:
    """Most recent CRM notes for a merchant."""
    require_merchant(merchant_id)
    return query("SELECT * FROM crm_notes WHERE merchant_id = %s ORDER BY ts DESC LIMIT %s", (merchant_id, limit))


@mcp.tool()
def get_actions(merchant_id: str, limit: int = 20) -> list[dict]:
    """Most recent actions taken for a merchant, with evidence, playbook section and status."""
    require_merchant(merchant_id)
    return query("SELECT * FROM actions WHERE merchant_id = %s ORDER BY ts DESC LIMIT %s", (merchant_id, limit))


@mcp.tool()
def request_approval(merchant_id: str, action_type: Literal["loan_offer", "waiver", "high_value_action"], draft: dict,
                     evidence: list[str], playbook_ref: str, line: Line = "save") -> dict:
    """Queue a prepared action for a human (RM) decision. The graph pauses until it is decided."""
    check_citation(evidence, playbook_ref, line)
    require_merchant(merchant_id)
    approval_id = new_id("AP")
    with pool().connection() as conn:
        action_id = write_action(conn, merchant_id, line, action_type, draft, evidence, playbook_ref,
                                 "human_approves", "pending_approval")
        conn.execute("INSERT INTO approvals (approval_id, action_id, requested_at) VALUES (%s, %s, %s)",
                     (approval_id, action_id, sim_ts()))
    return {"approval_id": approval_id, "action_id": action_id, "status": "pending_approval"}


@mcp.tool()
def escalate_to_human(merchant_id: str, reasons: list[str], case_summary: str, evidence: list[str], playbook_ref: str,
                      line: Line = "save") -> dict:
    """Hand a case to a human (Autonomy policy §4): angry or disputed cases, low-confidence decisions.
    The agent takes no action on the merchant; the case is queued with its evidence."""
    check_citation(evidence, playbook_ref, line)
    require_merchant(merchant_id)
    if not reasons:
        raise ToolError("Rejected: give at least one escalation reason (Autonomy policy §4).")
    with pool().connection() as conn:
        action_id = write_action(conn, merchant_id, line, "escalation", {"reasons": reasons, "case_summary": case_summary},
                                 evidence, playbook_ref, "human_only", "queued")
    return {"action_id": action_id, "status": "queued"}


@mcp.tool()
def log_gate_result(merchant_id: str, draft_kind: Literal["fee_message", "cost_comparison", "loan_offer"],
                    draft: dict, result: Literal["pass", "block", "unsure"], rule_hits: list[dict], judge: dict | None,
                    evidence: list[str], playbook_ref: str, line: Line = "save") -> dict:
    """Log a compliance gate decision on a draft (Compliance rules §7.4): the draft, the result, the
    rules hit (each with its Compliance section) and the judge's verdict and reason."""
    check_citation(evidence, playbook_ref, line)
    require_merchant(merchant_id)
    with pool().connection() as conn:
        action_id = write_action(conn, merchant_id, line, "compliance_check",
                                 {"draft_kind": draft_kind, "draft": draft, "rule_hits": rule_hits, "judge": judge},
                                 evidence, playbook_ref, "agent_alone", result)
    return {"action_id": action_id, "result": result}


@mcp.tool()
def get_approval(approval_id: str) -> dict:
    """Status of an approval request: pending, approved or rejected, with who decided."""
    a = one("SELECT ap.*, ac.merchant_id, ac.action_type, ac.payload_json AS draft, ac.status AS action_status "
            "FROM approvals ap JOIN actions ac USING (action_id) WHERE ap.approval_id = %s", (approval_id,))
    if not a:
        raise ToolError(f"Unknown approval_id {approval_id!r}")
    return {**a, "decision": a["decision"] or "pending"}


if __name__ == "__main__":
    mcp.run()
