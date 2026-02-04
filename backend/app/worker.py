import asyncio
import logging
from typing import Any

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import async_session
from app.logging import configure_logging
from app.models import ScoreRecord
from app.scoring import deterministic_score
from app.services import fetch_dexscreener, fetch_goplus

configure_logging()
logger = logging.getLogger(__name__)

STREAM_NAME = "score_jobs"
GROUP_NAME = "scorers"
CONSUMER_NAME = "worker-1"


def _parse_job(message: dict[str, Any]) -> tuple[str, str]:
    token_address = message.get("token_address")
    chain = message.get("chain", "ethereum")
    if not token_address:
        raise ValueError("token_address missing")
    return token_address, chain


async def ensure_group(redis: Redis) -> None:
    try:
        await redis.xgroup_create(STREAM_NAME, GROUP_NAME, id="0", mkstream=True)
    except Exception:
        pass


async def handle_job(redis: Redis, session: AsyncSession, message: dict[str, Any]) -> None:
    token_address, chain = _parse_job(message)
    dex = await fetch_dexscreener(redis, token_address)
    goplus = await fetch_goplus(redis, token_address)
    score_value, reasons = deterministic_score(dex, goplus)

    record = ScoreRecord(
        token_address=token_address,
        chain=chain,
        score=score_value,
        reasons=[reason.model_dump() for reason in reasons],
    )
    session.add(record)
    await session.commit()


async def run_worker() -> None:
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    await ensure_group(redis)
    async with async_session() as session:
        while True:
            try:
                response = await redis.xreadgroup(
                    GROUP_NAME,
                    CONSUMER_NAME,
                    streams={STREAM_NAME: ">"},
                    count=1,
                    block=5000,
                )
                if not response:
                    continue
                _, entries = response[0]
                for message_id, fields in entries:
                    try:
                        await handle_job(redis, session, fields)
                        await redis.xack(STREAM_NAME, GROUP_NAME, message_id)
                    except Exception as exc:
                        logger.exception("job_failed", extra={"error": str(exc)})
            except Exception as exc:
                logger.exception("worker_loop_failed", extra={"error": str(exc)})
                await asyncio.sleep(2)


if __name__ == "__main__":
    asyncio.run(run_worker())
