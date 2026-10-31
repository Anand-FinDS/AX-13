"""Load data/out/*.parquet into Postgres (DATA_PREPARATION.md §8).

    python -m data.load_postgres

Drops and recreates every AX-13 table, so a reload is a full demo reset: runtime tables
(actions, visits, crm_notes, approvals) come back empty. Other schemas (e.g. the LangGraph
checkpointer's) are not touched.

Schemas:
    ax13       - everything MCP servers serve, plus the runtime tables AX-13 writes to
    ax13_eval  - hidden ground truth (merchant_labels). The MCP role has no access.

The MCP servers log in as a restricted role (POSTGRES_MCP_USER). It reads time-dependent data
only through v_* views filtered on sim_state.sim_date, so unreleased future rows are invisible
to it at the database level. It cannot decide approvals or move the clock: those are human-only
actions in app/admin.py, which uses the main login.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import psycopg
import pyarrow.parquet as pq
from psycopg import sql

from common.settings import get_settings

EVAL_SCHEMA = get_settings().postgres_eval_schema

# (table, DDL body). Order = creation order; FKs point backwards only.
TABLES: list[tuple[str, str]] = [
    ("zones", """
        zone_id                  text PRIMARY KEY,
        name                     text NOT NULL,
        languages                text[] NOT NULL,
        waiver_budget_month      numeric(12,2) NOT NULL,
        waiver_cap_per_merchant  numeric(12,2) NOT NULL"""),
    ("field_agents", """
        agent_id        text PRIMARY KEY,
        name            text NOT NULL,
        zone_id         text NOT NULL REFERENCES {s}.zones,
        languages       text[] NOT NULL,
        visits_per_day  int NOT NULL,
        home_lat        double precision NOT NULL,
        home_lng        double precision NOT NULL"""),
    ("merchants", """
        merchant_id          text PRIMARY KEY,
        name                 text NOT NULL,
        owner_name           text NOT NULL,
        category             text NOT NULL,
        zone_id              text NOT NULL REFERENCES {s}.zones,
        pin_code             text NOT NULL,
        lat                  double precision NOT NULL,
        lng                  double precision NOT NULL,
        language_pref        text NOT NULL,
        onboard_date         date NOT NULL,
        has_qr               boolean NOT NULL,
        has_soundbox         boolean NOT NULL,
        has_pos              boolean NOT NULL,
        soundbox_rent_month  numeric(10,2) NOT NULL,
        assigned_agent_id    text NOT NULL REFERENCES {s}.field_agents,
        phone_masked         text NOT NULL,
        merchant_value_tier  text NOT NULL CHECK (merchant_value_tier IN ('low','mid','high'))"""),
    *[(t, """
        merchant_id           text NOT NULL REFERENCES {s}.merchants,
        date                  date NOT NULL,
        upi_count             int NOT NULL,
        upi_value             numeric(14,2) NOT NULL,
        upi_count_above_2000  int NOT NULL,
        upi_value_above_2000  numeric(14,2) NOT NULL,
        card_count            int NOT NULL,
        card_value            numeric(14,2) NOT NULL,
        failed_count          int NOT NULL,
        failed_debited_count  int NOT NULL,
        peak_hour_share       real NOT NULL,
        PRIMARY KEY (merchant_id, date)""") for t in ("txn_daily", "txn_daily_future")],
    ("device_health_daily", """
        merchant_id             text NOT NULL REFERENCES {s}.merchants,
        device_id               text NOT NULL,
        date                    date NOT NULL,
        device_type             text NOT NULL CHECK (device_type IN ('soundbox','pos')),
        uptime_pct              real NOT NULL CHECK (uptime_pct BETWEEN 0 AND 100),
        offline_minutes         int NOT NULL,
        network_errors_peak     int NOT NULL,
        network_errors_offpeak  int NOT NULL,
        hardware_fault_flag     boolean NOT NULL,
        last_heartbeat          timestamp NOT NULL,
        PRIMARY KEY (merchant_id, device_id, date)"""),
    ("settlements", """
        settlement_id  text PRIMARY KEY,
        merchant_id    text NOT NULL REFERENCES {s}.merchants,
        date           date NOT NULL,
        gross_amount   numeric(14,2) NOT NULL,
        net_amount     numeric(14,2) NOT NULL,
        delay_hours    int NOT NULL,
        status         text NOT NULL CHECK (status IN ('settled','delayed','held'))"""),
    ("deductions", """
        deduction_id   text PRIMARY KEY,
        merchant_id    text NOT NULL REFERENCES {s}.merchants,
        settlement_id  text NOT NULL REFERENCES {s}.settlements,
        date           date NOT NULL,
        type           text NOT NULL CHECK (type IN ('soundbox_rent','maintenance_charge','upi_fee','other')),
        amount         numeric(12,2) NOT NULL,
        notified       boolean NOT NULL"""),
    ("tickets", """
        ticket_id    text PRIMARY KEY,
        merchant_id  text NOT NULL REFERENCES {s}.merchants,
        opened_at    timestamp NOT NULL,
        resolved_at  timestamp,
        channel      text NOT NULL CHECK (channel IN ('call','app','field')),
        category     text NOT NULL CHECK (category IN ('device','settlement','charge','failed_txn','other')),
        status       text NOT NULL CHECK (status IN ('open','in_progress','resolved')),
        summary      text NOT NULL,
        sentiment    text NOT NULL CHECK (sentiment IN ('calm','frustrated','angry'))"""),
    ("offers_history", """
        offer_id     text PRIMARY KEY,
        merchant_id  text NOT NULL REFERENCES {s}.merchants,
        date         date NOT NULL,
        product      text NOT NULL CHECK (product IN ('soundbox','pos','loan','insurance','plan_change')),
        channel      text NOT NULL CHECK (channel IN ('app','whatsapp','field')),
        response     text NOT NULL CHECK (response IN ('accepted','declined','no_response','pending'))"""),
    ("merchant_fee_monthly", """
        merchant_id        text NOT NULL REFERENCES {s}.merchants,
        month              date NOT NULL,
        monthly_upi_value  numeric(14,2) NOT NULL,
        value_above_2000   numeric(14,2) NOT NULL,
        is_fee_affected    boolean NOT NULL,
        est_monthly_fee    numeric(12,2) NOT NULL,
        PRIMARY KEY (merchant_id, month)"""),
    ("sim_state", """
        id        boolean PRIMARY KEY DEFAULT true CHECK (id),
        sim_date  date NOT NULL,
        fee_live  boolean NOT NULL"""),
]

# Written by AX-13 at runtime; created empty.
RUNTIME_TABLES: list[tuple[str, str]] = [
    ("actions", """
        action_id      text PRIMARY KEY DEFAULT gen_random_uuid()::text,
        merchant_id    text NOT NULL REFERENCES {s}.merchants,
        ts             timestamp NOT NULL DEFAULT now(),
        line           text NOT NULL CHECK (line IN ('save','grow','none')),
        action_type    text NOT NULL,
        payload_json   jsonb NOT NULL DEFAULT '{{}}',
        evidence       jsonb NOT NULL,          -- signals cited (Retention playbook §1.4)
        playbook_ref   text NOT NULL,           -- e.g. 'Retention playbook §3.1'
        autonomy_tier  text NOT NULL CHECK (autonomy_tier IN ('agent_alone','human_approves','human_only')),
        approved_by    text,
        status         text NOT NULL"""),
    ("visits", """
        visit_id       text PRIMARY KEY DEFAULT gen_random_uuid()::text,
        agent_id       text NOT NULL REFERENCES {s}.field_agents,
        merchant_id    text NOT NULL REFERENCES {s}.merchants,
        planned_date   date NOT NULL,
        stop_order     int,
        stop_type      text NOT NULL CHECK (stop_type IN ('retain','pitch')),
        brief          text,
        outcome        text,
        transcript_id  text"""),
    ("crm_notes", """
        note_id          text PRIMARY KEY DEFAULT gen_random_uuid()::text,
        merchant_id      text NOT NULL REFERENCES {s}.merchants,
        ts               timestamp NOT NULL DEFAULT now(),
        source           text NOT NULL CHECK (source IN ('agent','voice','human')),
        text             text NOT NULL,
        structured_json  jsonb"""),
    ("approvals", """
        approval_id   text PRIMARY KEY DEFAULT gen_random_uuid()::text,
        action_id     text NOT NULL REFERENCES {s}.actions,
        requested_at  timestamp NOT NULL DEFAULT now(),
        decided_at    timestamp,
        decision      text CHECK (decision IN ('approved','rejected')),
        decided_by    text"""),
]

EVAL_TABLES: list[tuple[str, str]] = [
    ("merchant_labels", """
        merchant_id          text PRIMARY KEY,
        archetype            text NOT NULL,
        onset_date           date,
        expected_line        text NOT NULL CHECK (expected_line IN ('save','grow','none')),
        expected_action      text NOT NULL,
        expected_escalation  boolean NOT NULL,
        is_fee_affected      boolean NOT NULL,
        notes                text"""),
]

# Read views for the MCP role: everything time-dependent is cut at sim_state.sim_date.
VIEWS = {
    "v_txn_daily": """
        SELECT t.* FROM (SELECT * FROM {s}.txn_daily UNION ALL SELECT * FROM {s}.txn_daily_future) t,
               {s}.sim_state st
        WHERE t.date <= st.sim_date""",
    "v_device_health_daily": "SELECT d.* FROM {s}.device_health_daily d, {s}.sim_state st WHERE d.date <= st.sim_date",
    "v_settlements": "SELECT x.* FROM {s}.settlements x, {s}.sim_state st WHERE x.date <= st.sim_date",
    "v_deductions": "SELECT x.* FROM {s}.deductions x, {s}.sim_state st WHERE x.date <= st.sim_date",
    "v_offers_history": "SELECT x.* FROM {s}.offers_history x, {s}.sim_state st WHERE x.date <= st.sim_date",
    "v_tickets": """
        SELECT t.ticket_id, t.merchant_id, t.opened_at,
               CASE WHEN t.resolved_at::date <= st.sim_date THEN t.resolved_at END AS resolved_at,
               t.channel, t.category,
               CASE WHEN t.resolved_at::date > st.sim_date THEN 'in_progress' ELSE t.status END AS status,
               t.summary, t.sentiment
        FROM {s}.tickets t, {s}.sim_state st
        WHERE t.opened_at::date <= st.sim_date""",
    "v_merchant_fee_monthly": """
        SELECT f.* FROM {s}.merchant_fee_monthly f, {s}.sim_state st
        WHERE (f.month + interval '1 month' - interval '1 day')::date <= st.sim_date""",
}

# MCP role privileges. Column lists keep it from setting approval decisions or approved_by.
MCP_GRANTS = [
    "GRANT USAGE ON SCHEMA {s} TO {r}",
    "GRANT SELECT ON {s}.zones, {s}.field_agents, {s}.merchants, {s}.sim_state TO {r}",
    "GRANT SELECT ON " + ", ".join("{s}." + v for v in VIEWS) + " TO {r}",
    "GRANT INSERT ON {s}.tickets, {s}.offers_history TO {r}",
    "GRANT SELECT (ticket_id), UPDATE (status, resolved_at) ON {s}.tickets TO {r}",
    "GRANT SELECT, INSERT ON {s}.crm_notes TO {r}",
    "GRANT SELECT, INSERT, UPDATE (outcome, transcript_id) ON {s}.visits TO {r}",
    "GRANT SELECT, UPDATE (status) ON {s}.actions TO {r}",
    "GRANT INSERT (action_id, merchant_id, ts, line, action_type, payload_json, evidence, playbook_ref,"
    " autonomy_tier, status) ON {s}.actions TO {r}",
    "GRANT SELECT, INSERT (approval_id, action_id, requested_at) ON {s}.approvals TO {r}",
]

INDEXES = [
    "CREATE INDEX ON {s}.settlements (merchant_id, date)",
    "CREATE INDEX ON {s}.deductions (merchant_id, date)",
    "CREATE INDEX ON {s}.tickets (merchant_id, opened_at)",
    "CREATE INDEX ON {s}.offers_history (merchant_id, date)",
    "CREATE INDEX ON {s}.device_health_daily (date)",
    "CREATE INDEX ON {s}.txn_daily_future (date)",
    "CREATE INDEX ON {s}.actions (merchant_id, ts)",
    "CREATE INDEX ON {s}.crm_notes (merchant_id, ts)",
]


def _recreate(conn: psycopg.Connection, schema: str, tables: list[tuple[str, str]]) -> None:
    conn.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
    for name, _ in reversed(tables):
        conn.execute(f"DROP TABLE IF EXISTS {schema}.{name} CASCADE")
    for name, body in tables:
        conn.execute(f"CREATE TABLE {schema}.{name} ({body.format(s=schema)}\n)")


def _ensure_mcp_role(conn: psycopg.Connection, schema: str) -> str:
    s = get_settings()
    role = s.postgres_mcp_user
    pw = s.postgres_mcp_password.get_secret_value()
    if not pw:
        raise SystemExit("POSTGRES_MCP_PASSWORD is not set in .env")
    exists = conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)).fetchone()
    verb = "ALTER" if exists else "CREATE"
    conn.execute(sql.SQL(verb + " ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD {}").format(
        sql.Identifier(role), sql.Literal(pw)))
    r = sql.Identifier(role).as_string(conn)
    for stmt in MCP_GRANTS:
        conn.execute(stmt.format(s=schema, r=r))
    # Nothing on the hidden labels or the checkpointer, for this role or anyone else by default.
    for other in (EVAL_SCHEMA, s.postgres_checkpoint_schema):
        conn.execute(f"CREATE SCHEMA IF NOT EXISTS {other}")
        conn.execute(f"REVOKE ALL ON SCHEMA {other} FROM PUBLIC, {r}")
    return role


def _copy(conn: psycopg.Connection, schema: str, name: str, path: Path) -> int:
    table = pq.read_table(path)
    cols = table.column_names
    with conn.cursor() as cur, cur.copy(f"COPY {schema}.{name} ({', '.join(cols)}) FROM STDIN") as cp:
        for row in table.to_pylist():
            cp.write_row([row[c] for c in cols])
    return table.num_rows


def load(out_dir: Path) -> list[tuple[str, int, int]]:
    s = get_settings()
    schema = s.postgres_schema
    missing = [n for n, _ in TABLES + EVAL_TABLES if not (out_dir / f"{n}.parquet").exists()]
    if missing:
        raise SystemExit(f"Missing outputs {missing}; run `python -m data.generator` first.")

    report = []
    with psycopg.connect(s.postgres_dsn, connect_timeout=5) as conn:
        _recreate(conn, schema, TABLES + RUNTIME_TABLES)
        _recreate(conn, EVAL_SCHEMA, EVAL_TABLES)
        for target, tables in ((schema, TABLES), (EVAL_SCHEMA, EVAL_TABLES)):
            for name, _ in tables:
                n = _copy(conn, target, name, out_dir / f"{name}.parquet")
                db = conn.execute(f"SELECT count(*) FROM {target}.{name}").fetchone()[0]
                report.append((f"{target}.{name}", n, db))
        for name, _ in RUNTIME_TABLES:
            report.append((f"{schema}.{name}", 0, conn.execute(f"SELECT count(*) FROM {schema}.{name}").fetchone()[0]))
        for ddl in INDEXES:
            conn.execute(ddl.format(s=schema))
        for name, body in VIEWS.items():
            conn.execute(f"CREATE VIEW {schema}.{name} AS {body.format(s=schema)}")
        _ensure_mcp_role(conn, schema)
        conn.execute("ANALYZE")
    return report


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=get_settings().data_out_dir)
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    t0 = time.time()
    report = load(args.out)
    print(f"{'table':<34}{'parquet':>9}{'postgres':>10}")
    print("-" * 53)
    for name, n, db in report:
        print(f"{name:<34}{n:>9,}{db:>10,}{'' if n == db else '  MISMATCH'}")
    bad = [r for r in report if r[1] != r[2]]
    print(f"\nLoaded in {time.time() - t0:.1f}s. "
          + ("All row counts match." if not bad else f"{len(bad)} table(s) mismatched."))
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
