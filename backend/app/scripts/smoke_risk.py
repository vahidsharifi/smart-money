import asyncio
from datetime import datetime

from redis.asyncio import Redis
from sqlalchemy import select

from app.config import settings
from app.db import async_session
from app.models import TokenRisk
from app.utils import STREAM_DECODED_TRADES, publish_to_stream
from app.worker_risk import run_once

TOKEN_PLACEHOLDERS = {
    "ethereum": "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",
    "bsc": "0xBB4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c",
}


def _choose_chain() -> str:
    for candidate in ("ethereum", "bsc"):
        if candidate in settings.chain_config:
            return candidate
    if settings.chain_config:
        return next(iter(settings.chain_config.keys()))
    return "ethereum"


async def main() -> None:
    chain = _choose_chain()
    token_address = TOKEN_PLACEHOLDERS.get(chain, TOKEN_PLACEHOLDERS["ethereum"])
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    payload = {
        "chain": chain,
        "token_address": token_address,
        "tx_hash": "0x" + "2" * 64,
        "log_index": "0",
        "injectedAt": datetime.utcnow().isoformat(),
    }
    try:
        message_id = await publish_to_stream(redis, STREAM_DECODED_TRADES, payload)
        print(f"Injected fake decoded trade into {STREAM_DECODED_TRADES} with id {message_id}")
    finally:
        await redis.close()

    await run_once()

    async with async_session() as session:
        result = await session.execute(
            select(TokenRisk).where(TokenRisk.chain == chain, TokenRisk.address == token_address.lower())
        )
        token_risk = result.scalar_one_or_none()
        if token_risk is None:
            raise RuntimeError("token_risk snapshot not found after risk worker run")
        print(f"Risk snapshot stored for token {token_address} on {chain}")


if __name__ == "__main__":
    asyncio.run(main())
