import asyncio
import json
from datetime import datetime

from redis.asyncio import Redis

from app.config import settings
from app.utils import STREAM_RAW_EVENTS, publish_to_stream


async def main() -> None:
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    payload = {
        "chain": "ethereum",
        "address": "0x0000000000000000000000000000000000000000",
        "topics": json.dumps(["0x" + "0" * 64]),
        "data": "0x",
        "blockNumber": hex(12_345_678),
        "txHash": "0x" + "1" * 64,
        "logIndex": "0x0",
        "injectedAt": datetime.utcnow().isoformat(),
    }
    try:
        message_id = await publish_to_stream(redis, STREAM_RAW_EVENTS, payload)
        print(f"Injected fake log into {STREAM_RAW_EVENTS} with id {message_id}")
    finally:
        await redis.close()


if __name__ == "__main__":
    asyncio.run(main())
