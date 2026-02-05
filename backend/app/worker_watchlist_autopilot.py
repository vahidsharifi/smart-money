from __future__ import annotations

import asyncio
import logging
import random
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select

from app.config import settings, validate_chain_config
from app.db import async_session
from app.logging import configure_logging
from app.models import Token, WatchPair
from app.services.seed_importer import SEED_PACK_SOURCE
from app.utils import HttpClient, RetryConfig, install_shutdown_handlers

configure_logging()
logger = logging.getLogger(__name__)

DEFAULT_CHAIN_QUERIES = {
    "ethereum": "ethereum",
    "bsc": "bsc",
}

CRITICAL_GOPLUS_FLAGS = {"is_honeypot", "is_blacklisted"}


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalize_address(value: Any) -> str | None:
    if not value:
        return None
    return str(value).lower()


def _extract_pairs(payload: dict[str, Any]) -> list[dict[str, Any]]:
    pairs = payload.get("pairs")
    if isinstance(pairs, list):
        return [pair for pair in pairs if isinstance(pair, dict)]
    return []


def _calculate_age_hours(pair: dict[str, Any], now: datetime) -> float | None:
    created_at = pair.get("pairCreatedAt")
    if created_at is None:
        return None
    try:
        created_at_float = float(created_at)
    except (TypeError, ValueError):
        return None
    if created_at_float > 1_000_000_000_000:
        created_ts = created_at_float / 1000.0
    elif created_at_float > 1_000_000_000:
        created_ts = created_at_float
    else:
        return None
    created_dt = datetime.utcfromtimestamp(created_ts)
    return max((now - created_dt).total_seconds() / 3600.0, 0.0)


def _priority_score(liquidity_usd: float, volume_24h: float) -> int:
    return int(min(10_000, liquidity_usd / 1000.0 + volume_24h / 500.0))


def _extract_goplus_flags(payload: dict[str, Any], token_address: str) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    result = payload.get("result")
    if not isinstance(result, dict):
        return {}
    token_info = result.get(token_address.lower()) or result.get(token_address)
    if not isinstance(token_info, dict):
        return {}
    return {flag: token_info.get(flag) for flag in CRITICAL_GOPLUS_FLAGS}


def _has_critical_flags(flags: dict[str, Any]) -> bool:
    for value in flags.values():
        if value in (None, "", 0, "0", False):
            continue
        return True
    return False


def _chain_liquidity_floor(chain: str) -> float:
    if chain == "bsc":
        return settings.autopilot_liquidity_floor_bsc
    return settings.autopilot_liquidity_floor_eth


async def fetch_top_pairs(client: HttpClient, *, chain: str) -> list[dict[str, Any]]:
    query = DEFAULT_CHAIN_QUERIES.get(chain, chain)
    url = f"{settings.dexscreener_base_url}/search"
    payload = await client.get_json(url, params={"q": query})
    if not isinstance(payload, dict):
        raise ValueError("DexScreener payload is not a dict")
    pairs = _extract_pairs(payload)
    if not pairs:
        raise ValueError("DexScreener returned no pairs")
    chain_pairs = [pair for pair in pairs if str(pair.get("chainId", "")).lower() == chain]
    return chain_pairs or pairs


async def _check_known_token_flags(
    client: HttpClient,
    *,
    chain: str,
    token_address: str,
) -> bool:
    chain_id = settings.chain_config[chain].chain_id
    url = f"{settings.goplus_base_url}/token_security/{chain_id}"
    payload = await client.get_json(url, params={"contract_addresses": token_address})
    flags = _extract_goplus_flags(payload, token_address)
    return _has_critical_flags(flags)


async def _token_known(session, *, chain: str, token_address: str) -> bool:
    result = await session.execute(
        select(Token.address).where(Token.chain == chain, Token.address == token_address)
    )
    return result.scalar_one_or_none() is not None


async def run_autopilot_once() -> int:
    validate_chain_config()
    now = datetime.utcnow()
    inserted = 0
    async with HttpClient(
        retry_config=RetryConfig(attempts=3, backoff_factor=0.5, max_backoff=5.0)
    ) as client:
        async with async_session() as session:
            for chain in ("ethereum", "bsc"):
                try:
                    pairs = await fetch_top_pairs(client, chain=chain)
                except Exception as exc:
                    logger.warning("autopilot_fetch_failed chain=%s error=%s", chain, exc)
                    raise

                liquidity_floor = _chain_liquidity_floor(chain)
                volume_floor = settings.autopilot_volume_floor_24h
                min_age_hours = settings.autopilot_min_age_hours
                age_multiplier = settings.autopilot_age_fallback_multiplier

                for pair in pairs:
                    if str(pair.get("chainId", "")).lower() != chain:
                        continue
                    liquidity_usd = _safe_float(
                        (pair.get("liquidity") or {}).get("usd")
                    ) or 0.0
                    volume_24h = _safe_float((pair.get("volume") or {}).get("h24")) or 0.0
                    if liquidity_usd < liquidity_floor or volume_24h < volume_floor:
                        continue

                    age_hours = _calculate_age_hours(pair, now)
                    if age_hours is None:
                        if liquidity_usd < liquidity_floor * age_multiplier:
                            continue
                        if volume_24h < volume_floor * age_multiplier:
                            continue
                    elif age_hours < min_age_hours:
                        continue

                    pair_address = _normalize_address(pair.get("pairAddress"))
                    if not pair_address:
                        continue

                    base_token = pair.get("baseToken") or {}
                    quote_token = pair.get("quoteToken") or {}
                    token0_address = _normalize_address(base_token.get("address"))
                    token1_address = _normalize_address(quote_token.get("address"))

                    token_to_check = token0_address or token1_address
                    if token_to_check and await _token_known(
                        session, chain=chain, token_address=token_to_check
                    ):
                        if await _check_known_token_flags(
                            client, chain=chain, token_address=token_to_check
                        ):
                            continue

                    priority = _priority_score(liquidity_usd, volume_24h)
                    expires_at = now + timedelta(hours=6)
                    existing = await session.get(
                        WatchPair, {"chain": chain, "pair_address": pair_address}
                    )
                    if existing:
                        existing.dex = str(pair.get("dexId") or existing.dex)
                        existing.token0_symbol = base_token.get("symbol") or existing.token0_symbol
                        existing.token0_address = token0_address or existing.token0_address
                        existing.token1_symbol = quote_token.get("symbol") or existing.token1_symbol
                        existing.token1_address = token1_address or existing.token1_address
                        existing.priority = priority
                        existing.expires_at = expires_at
                        existing.last_seen = now
                        if existing.source != SEED_PACK_SOURCE:
                            existing.source = "autopilot"
                    else:
                        session.add(
                            WatchPair(
                                chain=chain,
                                pair_address=pair_address,
                                dex=str(pair.get("dexId") or ""),
                                token0_symbol=base_token.get("symbol"),
                                token0_address=token0_address,
                                token1_symbol=quote_token.get("symbol"),
                                token1_address=token1_address,
                                source="autopilot",
                                priority=priority,
                                expires_at=expires_at,
                                last_seen=now,
                            )
                        )
                    inserted += 1

            await session.commit()

            for chain in ("ethereum", "bsc"):
                result = await session.execute(
                    select(WatchPair)
                    .where(
                        WatchPair.chain == chain,
                        WatchPair.source == "autopilot",
                        WatchPair.expires_at > now,
                    )
                    .order_by(WatchPair.priority.desc(), WatchPair.last_seen.desc())
                )
                active = result.scalars().all()
                excess = active[settings.autopilot_max_pairs_per_chain :]
                for pair in excess:
                    pair.expires_at = now
                    pair.priority = min(pair.priority, 0)
                if excess:
                    await session.commit()

    logger.info("autopilot_complete inserted=%s", inserted)
    return inserted


async def run_worker() -> None:
    validate_chain_config()
    stop_event = asyncio.Event()
    install_shutdown_handlers(stop_event, logger)
    while not stop_event.is_set():
        try:
            await run_autopilot_once()
        except Exception as exc:
            logger.exception("autopilot_iteration_failed error=%s", exc)
        sleep_seconds = random.randint(
            settings.autopilot_min_sleep_seconds, settings.autopilot_max_sleep_seconds
        )
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=float(sleep_seconds))
        except asyncio.TimeoutError:
            continue


if __name__ == "__main__":
    asyncio.run(run_worker())
