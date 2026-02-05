import asyncio
from app.utils import random_evm_address
from datetime import datetime, timedelta

from redis.asyncio import Redis
from sqlalchemy import func, select

from app.config import validate_chain_config, settings
from app.db import async_session
from app.models import WatchPair
from app.services.watch_pairs import get_watch_pairs_snapshot
from app.worker_watchlist_autopilot import run_autopilot_once


def _sample_address() -> str:
    return random_evm_address()


async def _count_watch_pairs() -> int:
    async with async_session() as session:
        result = await session.execute(select(func.count()).select_from(WatchPair))
        return int(result.scalar_one())


async def _insert_synthetic_pairs() -> None:
    now = datetime.utcnow()
    async with async_session() as session:
        for chain in ("ethereum", "bsc", "ethereum"):
            session.add(
                WatchPair(
                    chain=chain,
                    pair_address=_sample_address(),
                    dex="synthetic",
                    token0_symbol="AAA",
                    token0_address=_sample_address(),
                    token1_symbol="BBB",
                    token1_address=_sample_address(),
                    source="autopilot",
                    priority=1,
                    expires_at=now + timedelta(hours=6),
                    last_seen=now,
                )
            )
        await session.commit()


async def _verify_autopilot_pairs() -> None:
    async with async_session() as session:
        result = await session.execute(
            select(WatchPair)
            .where(WatchPair.source == "autopilot")
            .order_by(WatchPair.last_seen.desc())
            .limit(3)
        )
        pairs = result.scalars().all()
        if not pairs:
            raise RuntimeError("No autopilot watch_pairs found")
        for pair in pairs:
            if pair.expires_at is None or pair.last_seen is None:
                raise RuntimeError("Autopilot watch_pairs missing TTL fields")


async def main() -> None:
    validate_chain_config()
    before_count = await _count_watch_pairs()
    used_autopilot = False
    try:
        inserted = await run_autopilot_once()
        used_autopilot = True
        print(f"Autopilot inserted {inserted} watch pairs.")
    except Exception as exc:
        print(f"DexScreener unavailable, falling back to synthetic pairs: {exc}")
        await _insert_synthetic_pairs()

    after_count = await _count_watch_pairs()
    if after_count <= before_count and used_autopilot:
        print("Autopilot produced no net increase; inserting synthetic pairs.")
        await _insert_synthetic_pairs()
        after_count = await _count_watch_pairs()

    if after_count <= before_count:
        raise RuntimeError("watch_pairs count did not increase")

    await _verify_autopilot_pairs()

    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    try:
        snapshot = await get_watch_pairs_snapshot(redis)
        total = sum(len(items) for items in snapshot.values())
        print(f"Listener snapshot size: {total}")
    finally:
        await redis.close()


if __name__ == "__main__":
    asyncio.run(main())
