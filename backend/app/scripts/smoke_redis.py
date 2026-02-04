import asyncio

from redis.asyncio import Redis

from app.config import settings
from app.utils import (
    STREAM_RAW_EVENTS,
    acknowledge_message,
    consume_from_stream,
    ensure_consumer_group,
    publish_to_stream,
)


async def main() -> None:
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    stream = STREAM_RAW_EVENTS
    group = "smoke_group"
    consumer = "smoke_consumer"

    try:
        await ensure_consumer_group(redis, stream=stream, group=group)
        message_id = await publish_to_stream(redis, stream, {"payload": "smoke_test"})
        messages = await consume_from_stream(
            redis,
            stream=stream,
            group=group,
            consumer=consumer,
            count=1,
            block_ms=5_000,
        )
        if not messages:
            raise RuntimeError("No messages consumed from stream")
        consumed_id, _ = messages[0]
        if consumed_id != message_id:
            raise RuntimeError("Consumed message id mismatch")
        await acknowledge_message(redis, stream=stream, group=group, message_id=consumed_id)
        print("Redis stream smoke test succeeded")
    finally:
        await redis.close()


if __name__ == "__main__":
    asyncio.run(main())
