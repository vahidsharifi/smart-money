import asyncio
import uuid
from datetime import datetime, timedelta

from redis.asyncio import Redis
from sqlalchemy import delete, func, select

from app.config import settings, validate_chain_config, watch_pairs_cap_for_chain
from app.db import async_session
from app.models import WatchPair
from app.services.seed_importer import SEED_PACK_SOURCE
from app.services.watch_pairs import WATCH_PAIRS_SNAPSHOT_KEY, get_watch_pairs_snapshot
from app.worker_watchlist_autopilot import _apply_churn_control


def _sample_address() -> str:
    return f"0x{uuid.uuid4().hex}{uuid.uuid4().hex}"[:42]


async def _seed(chain: str, cap: int) -> None:
    now = datetime.utcnow()
    async with async_session() as session:
        await session.execute(delete(WatchPair).where(WatchPair.chain == chain))
        anchor = WatchPair(
            chain=chain,
            pair_address=_sample_address(),
            dex="seed",
            token0_symbol="ANCH",
            token0_address=_sample_address(),
            token1_symbol="USDT",
            token1_address=_sample_address(),
            source=SEED_PACK_SOURCE,
            priority=9_999,
            score=999,
            reason={"anchor": True},
            expires_at=now + timedelta(days=365),
            last_seen=now,
        )
        session.add(anchor)
        for idx in range(10):
            session.add(
                WatchPair(
                    chain=chain,
                    pair_address=_sample_address(),
                    dex="synthetic",
                    token0_symbol=f"T{idx}",
                    token0_address=_sample_address(),
                    token1_symbol="WETH",
                    token1_address=_sample_address(),
                    source="autopilot",
                    priority=100 - idx,
                    score=float(100 - (idx * 5)),
                    reason={"liquidity": 100_000 - idx * 1_000, "volume": 200_000 + idx * 5_000},
                    expires_at=now + timedelta(minutes=5),
                    last_seen=now - timedelta(minutes=idx),
                )
            )
        await session.commit()

    if cap >= 11:
        settings.max_watch_pairs_eth = 5


async def _verify(chain: str, cap: int) -> None:
    now = datetime.utcnow()
    async with async_session() as session:
        result = await session.execute(
            select(func.count())
            .select_from(WatchPair)
            .where(WatchPair.chain == chain, WatchPair.expires_at > now)
        )
        active_count = int(result.scalar_one())

        anchor = await session.execute(
            select(WatchPair).where(
                WatchPair.chain == chain,
                WatchPair.source == SEED_PACK_SOURCE,
                WatchPair.expires_at > now,
            )
        )
        if anchor.scalar_one_or_none() is None:
            raise RuntimeError("anchor pair was not preserved")

        if active_count > cap:
            raise RuntimeError(f"active set exceeded cap: {active_count} > {cap}")


async def main() -> None:
    validate_chain_config()
    chain = "ethereum"
    cap = watch_pairs_cap_for_chain(chain)
    await _seed(chain, cap)
    cap = watch_pairs_cap_for_chain(chain)

    async with async_session() as session:
        demoted = await _apply_churn_control(session, chain=chain, now=datetime.utcnow())
        await session.commit()
        print(f"Demoted {demoted} pairs")

    await _verify(chain, cap)

    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    try:
        await redis.delete(WATCH_PAIRS_SNAPSHOT_KEY)
        snapshot = await get_watch_pairs_snapshot(redis)
        snapshot_size = len(snapshot.get(chain, []))
        if snapshot_size > cap:
            raise RuntimeError(f"listener snapshot exceeded cap: {snapshot_size} > {cap}")
        print(f"Listener snapshot ({chain}) size: {snapshot_size}")
    finally:
        await redis.close()


if __name__ == "__main__":
    asyncio.run(main())
