"""Simulation clock, read only. Moving the clock is human-only (app/admin.py).

    python -m mcp_servers.sim_clock
"""
from __future__ import annotations

from datetime import date, timedelta

from mcp.server.fastmcp import FastMCP

from data.generators.context import load_configs
from mcp_servers.db import one

mcp = FastMCP("sim_clock", log_level="WARNING")
DATES = load_configs()["sim_config"]["dates"]
FUTURE_DAYS = load_configs()["sim_config"]["scale"]["future_days"]


@mcp.tool()
def get_sim_state() -> dict:
    """Current simulation date, whether the UPI fee is live, and the key dates (D0, fee go-live)."""
    s = one("SELECT sim_date, fee_live FROM sim_state")
    d0, g0 = DATES["sim_start_date"], DATES["fee_go_live_date"]
    cur_d = date.fromisoformat(s["sim_date"])
    return {**s, "sim_start_date": d0.isoformat(), "fee_go_live_date": g0.isoformat(),
            "day": f"D{(cur_d - d0).days:+d}", "days_to_go_live": (g0 - cur_d).days,
            "last_available_date": (d0 + timedelta(days=FUTURE_DAYS)).isoformat()}


if __name__ == "__main__":
    mcp.run()
