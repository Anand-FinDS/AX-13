"""The agent's tool list, as it gets it: real stdio servers through mcp_servers.client."""
from __future__ import annotations

import asyncio

from mcp_servers.client import HUMAN_ONLY, agent_tools, get_client

EXPECTED = {"get_merchant_view", "book_technician", "request_approval", "get_approval", "make_offer",
            "grant_waiver", "get_sim_state", "get_peer_change"}


def test_agent_tools_exclude_human_only_actions():
    raw = {t.name for t in asyncio.run(get_client().get_tools())}   # unfiltered, straight from the servers
    assert not raw & HUMAN_ONLY, raw & HUMAN_ONLY
    assert EXPECTED <= raw

    async def persistent():
        async with agent_tools() as tools:
            return {t.name for t in tools}
    assert asyncio.run(persistent()) == raw
