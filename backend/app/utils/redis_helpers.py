from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable

from redis.asyncio import Redis


async def publish_to_stream(
    redis: Redis,
    stream: str,
    message: dict[str, Any],
    *,
    maxlen: int | None = None,
) -> str:
    return await redis.xadd(stream, message, maxlen=maxlen)


async def consume_from_stream(
    redis: Redis,
    *,
    stream: str,
    group: str,
    consumer: str,
    count: int = 10,
    block_ms: int = 5_000,
) -> list[tuple[str, dict[str, str]]]:
    response = await redis.xreadgroup(
        group,
        consumer,
        streams={stream: ">"},
        count=count,
        block=block_ms,
    )
    messages: list[tuple[str, dict[str, str]]] = []
    for _, entries in response:
        for message_id, fields in entries:
            messages.append((message_id, fields))
    return messages


async def acknowledge_message(redis: Redis, *, stream: str, group: str, message_id: str) -> None:
    await redis.xack(stream, group, message_id)


async def retry_or_dead_letter(
    redis: Redis,
    *,
    stream: str,
    group: str,
    message_id: str,
    fields: dict[str, Any],
    max_retries: int = 3,
    dead_letter_stream: str | None = None,
) -> None:
    retry_count = int(fields.get("retry_count", 0)) + 1
    updated_fields = {**fields, "retry_count": str(retry_count)}
    target_stream = dead_letter_stream or f"{stream}:dead"

    if retry_count > max_retries:
        await redis.xadd(target_stream, updated_fields)
    else:
        await redis.xadd(stream, updated_fields)

    await redis.xack(stream, group, message_id)


async def process_messages_with_retry(
    redis: Redis,
    *,
    stream: str,
    group: str,
    consumer: str,
    handler: Callable[[dict[str, Any]], Awaitable[None]],
    count: int = 10,
    block_ms: int = 5_000,
    max_retries: int = 3,
    dead_letter_stream: str | None = None,
) -> None:
    messages = await consume_from_stream(
        redis,
        stream=stream,
        group=group,
        consumer=consumer,
        count=count,
        block_ms=block_ms,
    )
    for message_id, fields in messages:
        try:
            await handler(fields)
        except Exception:
            await retry_or_dead_letter(
                redis,
                stream=stream,
                group=group,
                message_id=message_id,
                fields=fields,
                max_retries=max_retries,
                dead_letter_stream=dead_letter_stream,
            )
        else:
            await acknowledge_message(redis, stream=stream, group=group, message_id=message_id)


async def dedupe_with_ttl(redis: Redis, *, key: str, value: str, ttl_seconds: int) -> bool:
    added = await redis.sadd(key, value)
    if added:
        await redis.expire(key, ttl_seconds)
        return False
    return True


async def ensure_consumer_group(
    redis: Redis,
    *,
    stream: str,
    group: str,
    start_id: str = "0",
) -> None:
    try:
        await redis.xgroup_create(stream, group, id=start_id, mkstream=True)
    except Exception as exc:
        if "BUSYGROUP" not in str(exc):
            raise


async def wait_for_group(redis: Redis, *, stream: str, group: str, timeout: float = 5.0) -> None:
    deadline = asyncio.get_event_loop().time() + timeout
    while True:
        try:
            await redis.xinfo_groups(stream)
            return
        except Exception:
            if asyncio.get_event_loop().time() >= deadline:
                raise
            await asyncio.sleep(0.1)
