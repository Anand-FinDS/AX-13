"""The simulation clock: what the MCP role can see, and what admin actions do to it."""
from __future__ import annotations

from datetime import date

import psycopg
import pytest
from langgraph.checkpoint.postgres import PostgresSaver

from app import admin
from common.settings import get_settings
from mcp_servers import merchant_data, sim_clock, ticketing
from mcp_servers.db import query
from tests.conftest import CITE

D0, G0 = admin.D0, admin.G0


def test_d0_hides_future_rows(fresh):
    assert fresh["sim_date"] == D0 and fresh["fee_live"] is False
    txn = merchant_data.get_txn_daily("M_DEMO_RAVI", days=400)
    assert len(txn) == 120 and txn[-1]["date"] == D0.isoformat()
    assert max(r["date"] for r in merchant_data.get_device_health("M_DEMO_RAVI", 400)) == D0.isoformat()
    for view in ("v_txn_daily", "v_device_health_daily", "v_settlements", "v_deductions", "v_offers_history"):
        assert query(f"SELECT max(date) AS d FROM {view}")[0]["d"] <= D0.isoformat(), view
    assert query("SELECT count(*) AS n FROM v_tickets WHERE opened_at::date > %s", (D0,))[0]["n"] == 0
    assert query("SELECT max(month) AS m FROM v_merchant_fee_monthly")[0]["m"] == "2026-10-01"


def test_ravi_at_d0(fresh):
    v = merchant_data.get_merchant_view("M_DEMO_RAVI")
    sb = next(d for d in v["devices"] if d["device_type"] == "soundbox")
    assert sb["fault_days_7d"] == 7 and sb["offline_days_7d"] == 7
    assert [t["summary"][:28] for t in v["open_tickets"]] == ["Soundbox keeps disconnecting"]
    assert v["volume"]["change_vs_prior"] < -0.2
    assert v["fee"]["last_full_month"]["is_fee_affected"] is True


def test_mcp_role_cannot_bypass_views_or_reach_labels(fresh):
    s = get_settings()
    with psycopg.connect(s.postgres_mcp_dsn) as conn:
        for sql in ("SELECT count(*) FROM txn_daily_future", "SELECT count(*) FROM txn_daily",
                    "SELECT count(*) FROM device_health_daily", f"SELECT count(*) FROM {s.postgres_eval_schema}.merchant_labels",
                    "UPDATE sim_state SET sim_date = sim_date + 1",
                    "UPDATE approvals SET decision = 'approved'", "UPDATE actions SET approved_by = 'me'"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute(sql)
            conn.rollback()


def test_advance_day_releases_exactly_one_day(fresh):
    before = len(merchant_data.get_txn_daily("M_DEMO_RAVI", 400))
    st = admin.advance_day()
    assert st["sim_date"] == date(2026, 11, 1) and st["fee_live"] is False
    after = merchant_data.get_txn_daily("M_DEMO_RAVI", 400)
    assert len(after) == before + 1 and after[-1]["date"] == "2026-11-01"
    assert sim_clock.get_sim_state()["day"] == "D+1"


def test_jump_to_go_live_sets_fee_live(fresh):
    st = admin.jump_to_go_live()
    assert st["sim_date"] == G0 and st["fee_live"] is True
    assert sim_clock.get_sim_state()["days_to_go_live"] == 0
    # upi_fee deductions start appearing the day after go-live (T+1 settlement)
    admin.advance_day()
    assert any(d["type"] == "upi_fee" for d in merchant_data.get_deductions("M_DEMO_RAVI", 5))


def test_advance_stops_at_end_of_window(fresh):
    assert admin.advance_day(500)["sim_date"] == admin.LAST_DAY


def test_reset_restores_seeded_state_and_clears_checkpoints(fresh):
    s = get_settings()
    ticket = ticketing.list_tickets("M_DEMO_RAVI")[0]
    ticketing.update_ticket(ticket["ticket_id"], "resolved", "device swapped", **CITE)
    admin.advance_day(3)
    # a real LangGraph checkpoint in the checkpointer schema
    with PostgresSaver.from_conn_string(s.postgres_checkpoint_dsn) as saver:
        saver.setup()
        cfg = {"configurable": {"thread_id": "M_DEMO_RAVI", "checkpoint_ns": ""}}
        saver.put(cfg, {"v": 1, "id": "cp-1", "ts": "2026-10-31T00:00:00+00:00", "channel_values": {"x": 1},
                        "channel_versions": {}, "versions_seen": {}, "pending_sends": []}, {}, {})
        assert saver.get_tuple(cfg) is not None

    st = admin.reset()
    assert st["sim_date"] == D0 and st["fee_live"] is False
    assert any(n > 0 for n in st["checkpoint_rows_cleared"].values())
    assert ticketing.list_tickets("M_DEMO_RAVI")[0]["status"] == "open"
    assert query("SELECT count(*) AS n FROM actions")[0]["n"] == 0
    with PostgresSaver.from_conn_string(s.postgres_checkpoint_dsn) as saver:
        assert saver.get_tuple({"configurable": {"thread_id": "M_DEMO_RAVI", "checkpoint_ns": ""}}) is None
