"""Shared plumbing for the mock MCP servers.

- One connection pool per server process, logged in as the restricted MCP role
  (POSTGRES_MCP_USER). Time-dependent reads go through the v_* views, which the database
  cuts at sim_state.sim_date; the role cannot read the raw future tables or ax13_eval.
- Writes are stamped in simulation time (sim_date + wall-clock time of day).
- Every write tool must cite evidence and a real playbook section (Retention playbook §1.4);
  calls without them are rejected, not just logged.
"""
from __future__ import annotations

import json
import re
import uuid
from datetime import date, datetime
from decimal import Decimal
from functools import lru_cache
from typing import Any

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from common.settings import REPO_ROOT, get_settings

DOCS = {
    "Retention playbook": "retention_playbook.md",
    "Product catalogue": "product_catalogue.md",
    "Compliance rules": "compliance_rules.md",
    "Autonomy policy": "autonomy_policy.md",
}
REF_PATTERN = re.compile(r"^(?P<doc>" + "|".join(DOCS) + r") §(?P<sec>\d+(?:\.\d+)?)$")
LINES = ("save", "grow", "none")


class ToolError(ValueError):
    """Raised for a rejected tool call; FastMCP returns it to the caller as a tool error."""


@lru_cache(maxsize=1)
def pool() -> ConnectionPool:
    # prepare_threshold=None: no server-side prepared statements, so a reload (reset) that
    # recreates tables never leaves stale plans behind.
    return ConnectionPool(get_settings().postgres_mcp_dsn, min_size=1, max_size=4, open=True,
                          kwargs={"row_factory": dict_row, "prepare_threshold": None, "autocommit": False})


def jsonable(v: Any) -> Any:
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, datetime):
        return v.isoformat(sep=" ", timespec="minutes")
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, dict):
        return {k: jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [jsonable(x) for x in v]
    return v


def query(sql: str, params: tuple | dict = ()) -> list[dict]:
    with pool().connection() as conn:
        return [jsonable(r) for r in conn.execute(sql, params).fetchall()]


def one(sql: str, params: tuple | dict = ()) -> dict | None:
    rows = query(sql, params)
    return rows[0] if rows else None


def sim_date() -> date:
    with pool().connection() as conn:
        return conn.execute("SELECT sim_date FROM sim_state").fetchone()["sim_date"]


def sim_ts() -> datetime:
    """Simulation timestamp: today's sim date with the current wall-clock time of day."""
    return datetime.combine(sim_date(), datetime.now().time().replace(microsecond=0))


def new_id(prefix: str) -> str:
    """IDs for agent-written rows; the prefix keeps them apart from seeded IDs (T00001, O00001)."""
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


def require_merchant(merchant_id: str) -> dict:
    m = one("SELECT * FROM merchants WHERE merchant_id = %s", (merchant_id,))
    if not m:
        raise ToolError(f"Unknown merchant_id {merchant_id!r}")
    return m


# ---------------------------------------------------------------- evidence rule (§1.4)

@lru_cache(maxsize=1)
def known_sections() -> dict[str, set[str]]:
    """Section numbers that actually exist in docs/: '## 3.' headings and '**3.1 ' sub-headings."""
    out = {}
    for name, fname in DOCS.items():
        text = (REPO_ROOT / "docs" / fname).read_text(encoding="utf-8")
        secs = set(re.findall(r"^## (\d+)\.", text, re.MULTILINE)) | set(re.findall(r"^\*\*(\d+\.\d+) ", text, re.MULTILINE))
        out[name] = secs
    return out


def check_citation(evidence: list[str] | None, playbook_ref: str | None, line: str) -> None:
    if not evidence or not all(isinstance(e, str) and e.strip() for e in evidence):
        raise ToolError("Rejected: cite at least one evidence item (Retention playbook §1.4).")
    m = REF_PATTERN.match((playbook_ref or "").strip())
    if not m:
        raise ToolError("Rejected: playbook_ref must look like 'Retention playbook §3.1' "
                        f"(documents: {', '.join(DOCS)}).")
    if m["sec"] not in known_sections()[m["doc"]]:
        raise ToolError(f"Rejected: {playbook_ref} does not exist.")
    if line not in LINES:
        raise ToolError(f"Rejected: line must be one of {LINES}.")


def write_action(conn, merchant_id: str, line: str, action_type: str, payload: dict, evidence: list[str],
                 playbook_ref: str, autonomy_tier: str, status: str) -> str:
    """Audit row for every write (Autonomy policy §5). Runs inside the caller's transaction."""
    action_id = new_id("A")
    conn.execute(
        "INSERT INTO actions (action_id, merchant_id, ts, line, action_type, payload_json, evidence, playbook_ref,"
        " autonomy_tier, status) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (action_id, merchant_id, sim_ts(), line, action_type, Jsonb(jsonable(payload)), Jsonb(evidence),
         playbook_ref.strip(), autonomy_tier, status))
    return action_id


def approved_action(approval_id: str | None, merchant_id: str, action_type: str) -> dict:
    """The human-approved draft behind approval_id, or ToolError. Decisions are made only in app/admin.py."""
    if not approval_id:
        raise ToolError(f"Rejected: {action_type} needs human approval. Call request_approval first.")
    a = one("SELECT ap.decision, ac.action_id, ac.merchant_id, ac.action_type, ac.status, ac.payload_json "
            "FROM approvals ap JOIN actions ac USING (action_id) WHERE ap.approval_id = %s", (approval_id,))
    if not a or a["merchant_id"] != merchant_id or a["action_type"] != action_type:
        raise ToolError(f"Rejected: approval {approval_id} is not for a {action_type} to {merchant_id}.")
    if a["decision"] != "approved":
        raise ToolError(f"Rejected: approval {approval_id} is {a['decision'] or 'still pending'}.")
    if a["status"] == "executed":
        raise ToolError(f"Rejected: approval {approval_id} has already been used.")
    return a


def dumps(v: Any) -> str:
    return json.dumps(jsonable(v), ensure_ascii=False)
