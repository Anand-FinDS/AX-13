"""LangGraph checkpointer on Postgres schema ax13_graph (main login; the MCP role cannot see it).

The sync PostgresSaver is used from the async graph through threads. On Windows the MCP stdio
servers need the Proactor event loop, which psycopg's async driver does not support.
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager

from langgraph.checkpoint.postgres import PostgresSaver
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from common.settings import get_settings


class ThreadedPostgresSaver(PostgresSaver):
    async def aget_tuple(self, config):
        return await asyncio.to_thread(self.get_tuple, config)

    async def alist(self, config, *, filter=None, before=None, limit=None) -> AsyncIterator:
        for item in await asyncio.to_thread(lambda: list(self.list(config, filter=filter, before=before, limit=limit))):
            yield item

    async def aput(self, config, checkpoint, metadata, new_versions):
        return await asyncio.to_thread(self.put, config, checkpoint, metadata, new_versions)

    async def aput_writes(self, config, writes, task_id, task_path=""):
        return await asyncio.to_thread(self.put_writes, config, writes, task_id, task_path)

    async def adelete_thread(self, thread_id):
        return await asyncio.to_thread(self.delete_thread, thread_id)


@contextmanager
def checkpointer() -> Iterator[ThreadedPostgresSaver]:
    with ConnectionPool(get_settings().postgres_checkpoint_dsn, min_size=1, max_size=8, open=True,
                        kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row}) as pool:
        saver = ThreadedPostgresSaver(pool)
        saver.setup()
        yield saver
