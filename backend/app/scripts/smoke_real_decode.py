import asyncio
import json
from datetime import datetime
from pathlib import Path

from redis.asyncio import Redis
from sqlalchemy import and_, select

from app.config import settings
from app.db import async_session
from app.models import Trade
from app.utils import STREAM_RAW_EVENTS, publish_to_stream
from app.worker_decoder import run_once

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
FIXTURE_FILES = [
    "uniswap_v2_swap.json",
    "uniswap_v3_swap.json",
    "pancakeswap_v2_swap.json",
]

def _serialize_payload(payload: dict) -> dict[str, str]:
    serialized: dict[str, str] = {}
    for key, value in payload.items():
        if value is None:
            serialized[key] = ""
        elif isinstance(value, (list, dict)):
            serialized[key] = json.dumps(value)
        else:
            serialized[key] = str(value)
    return serialized


async def main() -> None:
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    tx_hashes: list[str] = []
    try:
        for fixture_file in FIXTURE_FILES:
            payload = json.loads((FIXTURES_DIR / fixture_file).read_text())
            payload["injectedAt"] = datetime.utcnow().isoformat()
            tx_hashes.append(payload["txHash"])
            message_id = await publish_to_stream(
                redis, STREAM_RAW_EVENTS, _serialize_payload(payload)
            )
            print(f"Injected fixture {fixture_file} into {STREAM_RAW_EVENTS} id={message_id}")
    finally:
        await redis.close()

    await run_once()

    async with async_session() as session:
        result = await session.execute(
            select(Trade).where(
                and_(
                    Trade.tx_hash.in_(tx_hashes),
                    Trade.decode_confidence >= 0.6,
                    Trade.dex.is_not(None),
                )
            )
        )
        trades = result.scalars().all()

    if len(trades) < 2:
        raise RuntimeError(
            f"Expected at least 2 decoded trades with confidence>=0.6 and dex populated, got {len(trades)}"
        )

    print(f"Real decode smoke passed with {len(trades)} qualifying trades")


if __name__ == "__main__":
    asyncio.run(main())
