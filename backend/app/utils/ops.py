from __future__ import annotations

import asyncio
import time
from contextlib import suppress

from redis.asyncio import Redis

HEARTBEAT_INTERVAL_SECONDS = 15
HEARTBEAT_PREFIX = "titan:hb"


def heartbeat_key(worker_name: str) -> str:
    return f"{HEARTBEAT_PREFIX}:{worker_name}"


async def heartbeat_loop(redis: Redis, *, worker_name: str) -> None:
    key = heartbeat_key(worker_name)
    while True:
        await redis.set(key, str(int(time.time())), ex=HEARTBEAT_INTERVAL_SECONDS * 4)
        await asyncio.sleep(HEARTBEAT_INTERVAL_SECONDS)


async def start_heartbeat(redis: Redis, *, worker_name: str) -> asyncio.Task[None]:
    task = asyncio.create_task(heartbeat_loop(redis, worker_name=worker_name))
    return task


async def stop_heartbeat(task: asyncio.Task[None] | None) -> None:
    if task is None:
        return
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task
