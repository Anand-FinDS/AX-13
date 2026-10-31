"""MCP client config the agent uses: launches every mock server over stdio.

    from mcp_servers.client import agent_tools
    async with agent_tools() as tools:  # one long-lived process per server (use this in the agent)
        ...

    tools = await get_tools()           # stateless: a new server process per call (slow, ~3s/call)

Human-only actions (approval decisions, moving the clock) live in app/admin.py and must never
appear in this tool list; both entry points refuse to return them.
"""
from __future__ import annotations

import sys
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager

from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.tools import load_mcp_tools

from common.settings import REPO_ROOT

SERVERS = ("merchant_data", "ticketing", "field_ops", "crm", "offers_messaging", "sim_clock")
HUMAN_ONLY = frozenset({"decide_approval", "list_pending_approvals", "advance_day", "jump_to_go_live", "reset"})


def server_config() -> dict[str, dict]:
    return {name: {"transport": "stdio", "command": sys.executable, "args": ["-m", f"mcp_servers.{name}"],
                   "cwd": str(REPO_ROOT), "env": {"PYTHONIOENCODING": "utf-8"}} for name in SERVERS}


def get_client() -> MultiServerMCPClient:
    return MultiServerMCPClient(server_config())


def _check(tools: list) -> list:
    leaked = HUMAN_ONLY & {t.name for t in tools}
    if leaked:
        raise RuntimeError(f"Human-only actions exposed as agent tools: {sorted(leaked)}")
    return tools


async def get_tools(client: MultiServerMCPClient | None = None) -> list:
    return _check(await (client or get_client()).get_tools())


@asynccontextmanager
async def agent_tools() -> AsyncIterator[list]:
    """Tools bound to persistent sessions: each server starts once and stays up for the block."""
    client = get_client()
    async with AsyncExitStack() as stack:
        tools = []
        for name in SERVERS:
            session = await stack.enter_async_context(client.session(name))
            tools += await load_mcp_tools(session)
        yield _check(tools)
