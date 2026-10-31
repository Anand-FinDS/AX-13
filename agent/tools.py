"""Calling MCP tools from agent code (outside the LLM loop).

    async with agent_tools() as tools:
        view = await call(tools, "get_merchant_view", merchant_id="M_DEMO_RAVI")
"""
from __future__ import annotations

import json

# Tools whose result is a list of rows: the adapter returns one content block per row.
LIST_TOOLS = {"list_merchants", "list_field_agents", "list_tickets", "list_visits", "get_txn_daily", "get_device_health",
              "get_settlements", "get_deductions", "get_notes", "get_actions", "get_offers"}


class ToolRejected(Exception):
    """The server refused the call; the message is what the model would read."""


def by_name(tools: list) -> dict:
    return tools if isinstance(tools, dict) else {t.name: t for t in tools}


async def call(tools, name: str, **args):
    out = await by_name(tools)[name].ainvoke(args)
    texts = [b.get("text", "") if isinstance(b, dict) else str(b) for b in out] if isinstance(out, list) else [out]
    if len(texts) == 1 and texts[0].startswith("Error executing tool"):
        raise ToolRejected(texts[0].split(": ", 1)[-1])
    items = [json.loads(t) for t in texts]
    return items if name in LIST_TOOLS else items[0]
