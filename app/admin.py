"""Human-only actions, called directly by the Streamlit app. Never exposed as MCP tools.

- RM decisions on approvals (Compliance rules §5, Autonomy policy §1)
- The simulation clock: advance a day, jump to fee go-live, reset

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
