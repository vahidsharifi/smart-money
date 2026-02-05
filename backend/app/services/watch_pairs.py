from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.sql import nulls_last

from app.config import settings, watch_pairs_cap_for_chain
from app.db import async_session
from app.models import WatchPair

WATCH_PAIRS_SNAPSHOT_KEY = "titan:watch_pairs:snapshot"
WATCH_PAIRS_SNAPSHOT_TTL_SECONDS = 60


def _normalize_address(value: Any) -> str | None:
    if not value:
        return None
    return str(value).lower()


async def get_watch_pairs_snapshot(redis: Redis) -> dict[str, list[str]]:
    cached = await redis.get(WATCH_PAIRS_SNAPSHOT_KEY)
    if cached:
        return json.loads(cached)

    snapshot: dict[str, list[str]] = {chain: [] for chain in settings.chain_config}
    now = datetime.utcnow()
    async with async_session() as session:
        for chain in settings.chain_config:
            cap = watch_pairs_cap_for_chain(chain)
            result = await session.execute(
                select(WatchPair)
                .where(
                    WatchPair.chain == chain,
                    WatchPair.expires_at > now,
                )
                .order_by(
                    WatchPair.priority.desc(),
                    WatchPair.score.desc(),
                    nulls_last(WatchPair.last_seen.desc()),
                )
                .limit(cap)
            )
            pairs = result.scalars().all()
            snapshot[chain] = [
                address
                for address in (_normalize_address(pair.pair_address) for pair in pairs)
                if address
            ]

    await redis.set(WATCH_PAIRS_SNAPSHOT_KEY, json.dumps(snapshot), ex=WATCH_PAIRS_SNAPSHOT_TTL_SECONDS)
    return snapshot
