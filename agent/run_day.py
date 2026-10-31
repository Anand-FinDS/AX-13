"""Run the AX-13 graph for every merchant at the current simulation date.

    python -m agent.run_day                       # all merchants
    python -m agent.run_day M_DEMO_RAVI ...        # some merchants
    python -m agent.run_day --resume-only          # only resume threads paused at an RM approval
    python -m agent.run_day --no-fixture           # do not inject eval fixtures as first drafts
    python -m agent.run_day --estimate             # dry run: LLM calls needed vs cached, nothing written
    python -m agent.run_day --live                 # cache misses call the API (default: replay, no API)

One LangGraph thread per merchant (thread_id "merchant:<id>", checkpoints in ax13_graph). A case
already handled on the thread is skipped. A thread paused at an approval is resumed once every
approval it waits for has been decided (read through MCP); until then the merchant is skipped.

LLM spend: replay mode (default) serves every call from data/out/llm_cache and never calls the API.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from agent.checkpoint import checkpointer
from agent.graph import build_graph, summary
from agent.tools import call
from common import llm
from common.telemetry import init_tracing
from mcp_servers.client import agent_tools

log = logging.getLogger("ax13.run_day")


def thread(merchant_id: str) -> dict:
    return {"configurable": {"thread_id": f"merchant:{merchant_id}"}}


async def _one(graph, tools, mid: str, inject_fixture: bool, resume_only: bool) -> dict:
    cfg = thread(mid)
    snap = await graph.aget_state(cfg)
    if snap.next:   # paused at await_approval
        ids = [a["approval_id"] for a in snap.values.get("approvals") or []]
        decisions = {i: (await call(tools, "get_approval", approval_id=i))["decision"] for i in ids}
        if any(d == "pending" for d in decisions.values()):
            return {"merchant_id": mid, "status": "awaiting_rm", "approvals": decisions, "actions": [], "cases": [],
                    "log": [f"awaiting RM decision on {', '.join(ids)}"]}
        out = await graph.ainvoke(Command(resume=decisions), cfg)
        return {**summary(out), "resumed": True}
    if resume_only:
        return {"merchant_id": mid, "status": "idle", "actions": [], "cases": [], "log": []}
    out = await graph.ainvoke({"merchant_id": mid, "inject_fixture": inject_fixture}, cfg)
    return {**summary(out), "paused": "__interrupt__" in out}


def _stats_since(before: dict) -> dict:
    return {k: llm.STATS[k] - before[k] for k in llm.STATS}


async def estimate(merchant_ids: list[str] | None = None, inject_fixture: bool = True, concurrency: int = 6) -> dict:
    """Dry run in replay mode up to the act step: how many LLM calls the run would need and how many
    are cached. Writes nothing (in-memory checkpointer, no gate log). Approximate: after a cache miss
    the dry run falls back to the template, so a redraft that would follow is not counted."""
    mode, before = llm.get_mode(), dict(llm.STATS)
    llm.set_mode("replay")
    try:
        async with agent_tools() as tools:
            with checkpointer() as saver:
                real = build_graph(tools, saver)
                dry = build_graph(tools, InMemorySaver(), interrupt_before=["act", "escalate"])
                ids = merchant_ids or [m["merchant_id"] for m in await call(tools, "list_merchants")]
                sem = asyncio.Semaphore(concurrency)

                async def one(mid):
                    async with sem:
                        snap = await real.aget_state(thread(mid))
                        if snap.next:          # paused at an RM approval: resuming needs no LLM
                            return
                        await dry.ainvoke({"merchant_id": mid, "inject_fixture": inject_fixture, "dry_run": True,
                                           "handled_cases": (snap.values or {}).get("handled_cases") or []},
                                          {"configurable": {"thread_id": f"estimate:{mid}"}})
                await asyncio.gather(*(one(m) for m in ids))
    finally:
        llm.set_mode(mode)
        d = _stats_since(before)
        llm.STATS.update(before)      # an estimate is a look-ahead, not usage: leave the counters as they were
    return {"merchants": len(ids), "llm_calls_needed": d["misses"], "llm_cached": d["hits"],
            "embeddings_needed": d["embed_misses"], "embeddings_cached": d["embed_hits"]}


async def run_day(merchant_ids: list[str] | None = None, inject_fixture: bool = True, resume_only: bool = False,
                  concurrency: int = 6) -> list[dict]:
    """Run the day. LLM calls follow the current mode (common.llm.set_mode). run_day_with_stats also
    returns the LLM cache hits and misses of the run."""
    return (await run_day_with_stats(merchant_ids, inject_fixture, resume_only, concurrency))[0]


async def run_day_with_stats(merchant_ids: list[str] | None = None, inject_fixture: bool = True,
                             resume_only: bool = False, concurrency: int = 6) -> tuple[list[dict], dict]:
    before = dict(llm.STATS)
    results = await _run(merchant_ids, inject_fixture, resume_only, concurrency)
    return results, _stats_since(before)


async def _run(merchant_ids, inject_fixture, resume_only, concurrency) -> list[dict]:
    init_tracing()
    async with agent_tools() as tools:
        with checkpointer() as saver:
            graph = build_graph(tools, saver)
            ids = merchant_ids or [m["merchant_id"] for m in await call(tools, "list_merchants")]
            sem = asyncio.Semaphore(concurrency)

            async def guarded(mid):
                async with sem:
                    try:
                        return await _one(graph, tools, mid, inject_fixture, resume_only)
                    except Exception as e:  # one merchant's failure must not stop the day
                        log.exception("merchant %s failed", mid)
                        return {"merchant_id": mid, "status": "error", "error": f"{type(e).__name__}: {e}",
                                "actions": [], "cases": [], "log": []}
            return list(await asyncio.gather(*(guarded(m) for m in ids)))


def print_report(results: list[dict], verbose: bool = False) -> None:
    from collections import Counter
    print(Counter(r["status"] for r in results))
    kinds = Counter((a["kind"], a["status"]) for r in results for a in r["actions"])
    for (k, s), n in sorted(kinds.items()):
        print(f"  {k:<16}{s:<20}{n:>4}")
    for r in results:
        if r["status"] in ("no_case", "already_handled", "idle") and not verbose:
            continue
        print(f"\n{r['merchant_id']} [{r['status']}{', resumed' if r.get('resumed') else ''}"
              f"{', paused for RM' if r.get('paused') else ''}] {', '.join(r['cases'])}")
        if r.get("error"):
            print(f"  ERROR {r['error']}")
        if verbose or r["merchant_id"].startswith("M_DEMO"):
            for line in r["log"]:
                print(f"  {line}")


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("merchants", nargs="*")
    ap.add_argument("--no-fixture", action="store_true")
    ap.add_argument("--resume-only", action="store_true")
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--estimate", action="store_true", help="dry run: LLM calls needed vs cached, nothing written")
    ap.add_argument("--live", action="store_true", help="cache misses call the API (default: replay, no API)")
    args = ap.parse_args()
    t0 = time.time()
    if args.estimate:
        print(asyncio.run(estimate(args.merchants or None, not args.no_fixture)))
        return 0
    llm.set_mode("live" if args.live else "replay")
    results, stats = asyncio.run(run_day_with_stats(args.merchants or None, not args.no_fixture, args.resume_only))
    print_report(results, args.verbose)
    print(f"\n{len(results)} merchants in {time.time() - t0:.0f}s · mode {llm.get_mode()} · LLM {stats['hits']} cached, "
          f"{stats['misses']} not cached, {stats['api_calls']} API calls")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
