"""Human-only actions, called directly by the Streamlit app. Never exposed as MCP tools.

- RM decisions on approvals (Compliance rules §5, Autonomy policy §1)
- The simulation clock: advance a day, jump to fee go-live, reset
- Closing a case a human has handled
- Read views the human screens need (queues, field plan, merchant timeline), cut at sim_date

Uses the main Postgres login (the MCP role cannot do any of this: it has no UPDATE on
sim_state or on approval decisions).
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Literal

import psycopg
from psycopg.rows import dict_row

from common.settings import get_settings
from data.generators.context import load_configs
from data.load_postgres import load

_SIM = load_configs()["sim_config"]
D0: date = _SIM["dates"]["sim_start_date"]
G0: date = _SIM["dates"]["fee_go_live_date"]
LAST_DAY: date = D0 + timedelta(days=_SIM["scale"]["future_days"])


def _connect() -> psycopg.Connection:
    s = get_settings()
    return psycopg.connect(s.postgres_dsn, row_factory=dict_row,
                           options=f"-c search_path={s.postgres_schema}")


def get_sim_state() -> dict:
    with _connect() as conn:
        return conn.execute("SELECT sim_date, fee_live FROM sim_state").fetchone()


def _set_date(d: date) -> dict:
    with _connect() as conn:
        conn.execute("UPDATE sim_state SET sim_date = %s, fee_live = %s", (d, d >= G0))
        return conn.execute("SELECT sim_date, fee_live FROM sim_state").fetchone()


def advance_day(n: int = 1) -> dict:
    """Release the next n pre-generated days (stops at the end of the future window)."""
    cur = get_sim_state()["sim_date"]
    return _set_date(min(cur + timedelta(days=n), LAST_DAY))


def jump_to_go_live() -> dict:
    """Move straight to fee go-live. Never moves the clock backwards."""
    cur = get_sim_state()["sim_date"]
    return _set_date(max(cur, G0))


def reset() -> dict:
    """Back to D0: reload every AX-13 table from the generated files (runtime tables empty,
    agent changes to seeded rows undone) and clear the LangGraph checkpointer threads."""
    s = get_settings()
    load(s.data_out_dir)
    cleared = {}
    with _connect() as conn:
        tables = conn.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = %s "
            "AND table_name <> 'checkpoint_migrations'", (s.postgres_checkpoint_schema,)).fetchall()
        for t in tables:
            name = f"{s.postgres_checkpoint_schema}.{t['table_name']}"
            cleared[name] = conn.execute(f"SELECT count(*) AS n FROM {name}").fetchone()["n"]
            conn.execute(f"TRUNCATE {name}")
    return {**get_sim_state(), "checkpoint_rows_cleared": cleared}


def list_pending_approvals(zone_id: str | None = None) -> list[dict]:
    """RM approval queue."""
    with _connect() as conn:
        return conn.execute(
            "SELECT ap.approval_id, ap.requested_at, ac.merchant_id, m.name, m.zone_id, ac.action_type, ac.line, "
            "ac.payload_json AS draft, ac.evidence, ac.playbook_ref FROM approvals ap JOIN actions ac USING (action_id) "
            "JOIN merchants m USING (merchant_id) WHERE ap.decision IS NULL AND (%s::text IS NULL OR m.zone_id = %s) "
            "ORDER BY ap.requested_at", (zone_id, zone_id)).fetchall()


def decide_approval(approval_id: str, decision: Literal["approved", "rejected"], decided_by: str) -> dict:
    """Record an RM decision. The agent then reads it with get_approval and resumes."""
    if decision not in ("approved", "rejected"):
        raise ValueError("decision must be 'approved' or 'rejected'")
    if not decided_by.strip():
        raise ValueError("decided_by is required")
    with _connect() as conn:
        ap = conn.execute("SELECT * FROM approvals WHERE approval_id = %s FOR UPDATE", (approval_id,)).fetchone()
        if not ap:
            raise ValueError(f"Unknown approval {approval_id}")
        if ap["decision"]:
            raise ValueError(f"Approval {approval_id} already {ap['decision']}")
        sim_date = conn.execute("SELECT sim_date FROM sim_state").fetchone()["sim_date"]
        ts = datetime.combine(sim_date, datetime.now().time().replace(microsecond=0))
        conn.execute("UPDATE approvals SET decision = %s, decided_at = %s, decided_by = %s WHERE approval_id = %s",
                     (decision, ts, decided_by, approval_id))
        conn.execute("UPDATE actions SET status = %s, approved_by = %s WHERE action_id = %s",
                     (decision, decided_by if decision == "approved" else None, ap["action_id"]))
    return {"approval_id": approval_id, "decision": decision, "decided_by": decided_by, "decided_at": ts}


# ---------------------------------------------------------------- read views for the human screens

def actions_log(merchant_ids: list[str] | None = None, action_types: list[str] | None = None) -> list[dict]:
    """Audit trail (Autonomy policy §5): every action with its evidence, section, tier, status and approver."""
    with _connect() as conn:
        return conn.execute(
            "SELECT a.ts, a.merchant_id, m.name, a.line, a.action_type, a.autonomy_tier, a.status, a.playbook_ref, "
            "a.approved_by, a.payload_json, a.evidence FROM actions a JOIN merchants m USING (merchant_id) "
            "WHERE (%(m)s::text[] IS NULL OR a.merchant_id = ANY(%(m)s)) "
            "AND (%(t)s::text[] IS NULL OR a.action_type = ANY(%(t)s)) ORDER BY a.ts, a.action_id",
            {"m": merchant_ids, "t": action_types}).fetchall()


def human_queue(zone_id: str | None = None) -> list[dict]:
    """Cases handed to a human (Autonomy policy §4) that are still open."""
    with _connect() as conn:
        return conn.execute(
            "SELECT a.action_id, a.ts, a.merchant_id, m.name, m.zone_id, a.line, a.payload_json, a.evidence, "
            "a.playbook_ref FROM actions a JOIN merchants m USING (merchant_id) WHERE a.action_type = 'escalation' "
            "AND a.status = 'queued' AND (%s::text IS NULL OR m.zone_id = %s) ORDER BY a.ts", (zone_id, zone_id)).fetchall()


def close_escalation(action_id: str, handled_by: str, note: str = "") -> dict:
    """A human marks an escalated case handled; the note goes to the CRM."""
    if not handled_by.strip():
        raise ValueError("handled_by is required")
    with _connect() as conn:
        a = conn.execute("SELECT * FROM actions WHERE action_id = %s AND action_type = 'escalation' FOR UPDATE",
                         (action_id,)).fetchone()
        if not a:
            raise ValueError(f"Unknown escalation {action_id}")
        sim_date = conn.execute("SELECT sim_date FROM sim_state").fetchone()["sim_date"]
        ts = datetime.combine(sim_date, datetime.now().time().replace(microsecond=0))
        conn.execute("UPDATE actions SET status = 'handled', approved_by = %s WHERE action_id = %s", (handled_by, action_id))
        conn.execute("INSERT INTO crm_notes (note_id, merchant_id, ts, source, text) VALUES (gen_random_uuid()::text, %s, %s, "
                     "'human', %s)", (a["merchant_id"], ts, f"[{handled_by}] Escalation handled. {note}".strip()))
    return {"action_id": action_id, "status": "handled"}


def merchants() -> list[dict]:
    with _connect() as conn:
        return conn.execute("SELECT merchant_id, name, owner_name, category, zone_id, language_pref, merchant_value_tier, "
                            "assigned_agent_id FROM merchants ORDER BY merchant_id NOT LIKE 'M_DEMO%%', merchant_id").fetchall()


def field_agents(zone_id: str | None = None) -> list[dict]:
    with _connect() as conn:
        return conn.execute("SELECT * FROM field_agents WHERE (%s::text IS NULL OR zone_id = %s) ORDER BY agent_id",
                            (zone_id, zone_id)).fetchall()


def field_plan(planned_date: date, agent_id: str | None = None, zone_id: str | None = None) -> list[dict]:
    """Visit stops for a day: Save (retain) stops first, then Grow (pitch), in the order they were planned."""
    with _connect() as conn:
        return conn.execute(
            "SELECT v.*, m.name, m.owner_name, m.category, m.language_pref, m.pin_code, f.name AS agent_name "
            "FROM visits v JOIN merchants m USING (merchant_id) JOIN field_agents f USING (agent_id) "
            "WHERE v.planned_date = %s AND (%s::text IS NULL OR v.agent_id = %s) AND (%s::text IS NULL OR f.zone_id = %s) "
            "ORDER BY v.agent_id, v.stop_type = 'pitch', v.stop_order NULLS LAST, v.visit_id",
            (planned_date, agent_id, agent_id, zone_id, zone_id)).fetchall()


def merchant_timeline(merchant_id: str, days: int = 60) -> dict:
    """Profile, daily UPI up to sim_date, tickets, actions, notes and visits for one merchant."""
    with _connect() as conn:
        q = lambda sql, *a: conn.execute(sql, a).fetchall()
        return {
            "profile": q("SELECT m.*, z.name AS zone_name, f.name AS agent_name FROM merchants m JOIN zones z USING (zone_id) "
                         "JOIN field_agents f ON f.agent_id = m.assigned_agent_id WHERE m.merchant_id = %s", merchant_id)[0],
            "txn": q("SELECT date, upi_count, upi_value FROM v_txn_daily WHERE merchant_id = %s AND date > "
                     "(SELECT sim_date FROM sim_state) - %s ORDER BY date", merchant_id, days),
            "tickets": q("SELECT * FROM v_tickets WHERE merchant_id = %s ORDER BY opened_at DESC", merchant_id),
            "actions": q("SELECT * FROM actions WHERE merchant_id = %s ORDER BY ts DESC", merchant_id),
            "notes": q("SELECT * FROM crm_notes WHERE merchant_id = %s ORDER BY ts DESC", merchant_id),
            "visits": q("SELECT * FROM visits WHERE merchant_id = %s ORDER BY planned_date DESC", merchant_id),
        }


def day_counts() -> list[dict]:
    """Actions per simulation day, type and tier (for the overview)."""
    with _connect() as conn:
        return conn.execute("SELECT ts::date AS day, action_type, autonomy_tier, status, count(*) AS n FROM actions "
                            "GROUP BY 1, 2, 3, 4 ORDER BY 1, 2").fetchall()
