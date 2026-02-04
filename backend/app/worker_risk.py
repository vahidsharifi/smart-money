from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime
from typing import Any

from redis.asyncio import Redis

from app.config import settings, validate_chain_config
from app.db import async_session
from app.logging import configure_logging
from app.models import TokenRisk
from app.utils import (
    HttpClient,
    STREAM_DECODED_TRADES,
    STREAM_RISK_JOBS,
    acknowledge_message,
    consume_from_stream,
    dedupe_with_ttl,
    ensure_consumer_group,
    install_shutdown_handlers,
    publish_to_stream,
    retry_or_dead_letter,
)

configure_logging()
logger = logging.getLogger(__name__)

DECODED_GROUP = "risk-enqueue"
DECODED_CONSUMER = "risk-enqueue-1"
RISK_GROUP = "risk-workers"
RISK_CONSUMER = "risk-worker-1"

DEDUPLICATION_SET = "titan:risk_jobs:dedupe"
DEDUPLICATION_TTL_SECONDS = 60

DEX_CACHE_TTL_SECONDS = 60
GOPLUS_CACHE_TTL_SECONDS = 300

_dex_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_goplus_cache: dict[str, tuple[float, dict[str, Any]]] = {}


def _boolish(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y"}
    return False


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


def _get_cached(cache: dict[str, tuple[float, dict[str, Any]]], key: str, ttl: int) -> dict[str, Any] | None:
    cached = cache.get(key)
    if not cached:
        return None
    timestamp, payload = cached
    if time.time() - timestamp > ttl:
        cache.pop(key, None)
        return None
    return payload


def _set_cached(cache: dict[str, tuple[float, dict[str, Any]]], key: str, value: dict[str, Any]) -> None:
    cache[key] = (time.time(), value)


async def fetch_dexscreener(client: HttpClient, *, token_address: str) -> dict[str, Any]:
    cache_key = token_address
    cached = _get_cached(_dex_cache, cache_key, DEX_CACHE_TTL_SECONDS)
    if cached is not None:
        return cached
    url = f"{settings.dexscreener_base_url}/tokens/{token_address}"
    payload = await client.get_json(url)
    if isinstance(payload, dict):
        _set_cached(_dex_cache, cache_key, payload)
    return payload


async def fetch_goplus(client: HttpClient, *, chain: str, token_address: str) -> dict[str, Any]:
    cache_key = f"{chain}:{token_address}"
    cached = _get_cached(_goplus_cache, cache_key, GOPLUS_CACHE_TTL_SECONDS)
    if cached is not None:
        return cached
    chain_id = settings.chain_config[chain].chain_id
    url = f"{settings.goplus_base_url}/token_security/{chain_id}"
    payload = await client.get_json(url, params={"contract_addresses": token_address})
    if isinstance(payload, dict):
        _set_cached(_goplus_cache, cache_key, payload)
    return payload


def _extract_dex_metrics(dex_payload: dict[str, Any]) -> dict[str, Any]:
    pairs = dex_payload.get("pairs") if isinstance(dex_payload, dict) else None
    if not isinstance(pairs, list):
        return {}
    liquidity_values = []
    for pair in pairs:
        liquidity = pair.get("liquidity") if isinstance(pair, dict) else None
        if isinstance(liquidity, dict):
            liquidity_usd = _safe_float(liquidity.get("usd"))
            if liquidity_usd is not None:
                liquidity_values.append(liquidity_usd)
    max_liquidity = max(liquidity_values) if liquidity_values else None
    return {
        "pair_count": len(pairs),
        "max_liquidity_usd": max_liquidity,
    }


def _extract_goplus_metrics(goplus_payload: dict[str, Any], token_address: str) -> dict[str, Any]:
    if not isinstance(goplus_payload, dict):
        return {}
    result = goplus_payload.get("result")
    if not isinstance(result, dict):
        return {}
    token_info = result.get(token_address.lower()) or result.get(token_address) or {}
    if not isinstance(token_info, dict):
        return {}
    return {
        "is_honeypot": token_info.get("is_honeypot"),
        "is_blacklisted": token_info.get("is_blacklisted"),
        "is_proxy": token_info.get("is_proxy"),
        "is_mintable": token_info.get("is_mintable"),
        "holder_count": token_info.get("holder_count"),
    }


def _derive_flags(dex_metrics: dict[str, Any], goplus_metrics: dict[str, Any]) -> list[str]:
    flags: list[str] = []
    if _boolish(goplus_metrics.get("is_honeypot")):
        flags.append("honeypot")
    if _boolish(goplus_metrics.get("is_blacklisted")):
        flags.append("blacklisted")
    if _boolish(goplus_metrics.get("is_proxy")):
        flags.append("proxy")
    if _boolish(goplus_metrics.get("is_mintable")):
        flags.append("mintable")
    max_liquidity = dex_metrics.get("max_liquidity_usd")
    if max_liquidity is not None and max_liquidity < 10_000:
        flags.append("low_liquidity")
    return flags


def _calculate_tss(flags: list[str], dex_metrics: dict[str, Any]) -> float:
    score = 100.0
    if dex_metrics.get("pair_count") == 0:
        score -= 30
    for _ in flags:
        score -= 15
    score = max(score, 0.0)
    return score


async def enqueue_risk_job(redis: Redis, *, chain: str, token_address: str) -> None:
    dedupe_value = f"{chain}:{token_address}"
    is_duplicate = await dedupe_with_ttl(
        redis,
        key=DEDUPLICATION_SET,
        value=dedupe_value,
        ttl_seconds=DEDUPLICATION_TTL_SECONDS,
    )
    if is_duplicate:
        logger.debug("risk_job_deduped chain=%s token=%s", chain, token_address)
        return
    await publish_to_stream(redis, STREAM_RISK_JOBS, {"chain": chain, "token_address": token_address})


async def handle_decoded_trade(redis: Redis, fields: dict[str, Any]) -> None:
    chain = str(fields.get("chain") or "ethereum").lower()
    token_address = _normalize_address(fields.get("token_address") or fields.get("tokenAddress"))
    if not token_address:
        logger.debug("risk_job_skipped_missing_token chain=%s", chain)
        return
    await enqueue_risk_job(redis, chain=chain, token_address=token_address)


async def process_decoded_batch(
    redis: Redis,
    *,
    count: int = 10,
    block_ms: int = 5_000,
) -> int:
    messages = await consume_from_stream(
        redis,
        stream=STREAM_DECODED_TRADES,
        group=DECODED_GROUP,
        consumer=DECODED_CONSUMER,
        count=count,
        block_ms=block_ms,
    )
    for message_id, fields in messages:
        try:
            await handle_decoded_trade(redis, fields)
            await acknowledge_message(
                redis,
                stream=STREAM_DECODED_TRADES,
                group=DECODED_GROUP,
                message_id=message_id,
            )
        except Exception:
            logger.exception("risk_enqueue_failed", extra={"message_id": message_id})
            await retry_or_dead_letter(
                redis,
                stream=STREAM_DECODED_TRADES,
                group=DECODED_GROUP,
                message_id=message_id,
                fields=fields,
            )
    return len(messages)


async def _store_token_risk(
    *,
    session: Any,
    chain: str,
    token_address: str,
    score: float,
    components: dict[str, Any],
) -> None:
    tss = None
    flags = None
    if isinstance(components, dict):
        tss_payload = components.get("tss")
        if isinstance(tss_payload, dict):
            tss = _safe_float(tss_payload.get("score"))
        flags_value = components.get("flags")
        if isinstance(flags_value, list):
            flags = flags_value
    record = TokenRisk(
        chain=chain,
        address=token_address,
        token_address=token_address,
        score=score,
        tss=tss,
        flags=flags,
        components=components,
        updated_at=datetime.utcnow(),
    )
    await session.merge(record)
    await session.commit()


async def handle_risk_job(
    client: HttpClient,
    *,
    session: Any,
    chain: str,
    token_address: str,
) -> None:
    try:
        dex_payload = await fetch_dexscreener(client, token_address=token_address)
        goplus_payload = await fetch_goplus(client, chain=chain, token_address=token_address)
        dex_metrics = _extract_dex_metrics(dex_payload)
        goplus_metrics = _extract_goplus_metrics(goplus_payload, token_address)
        flags = _derive_flags(dex_metrics, goplus_metrics)
        score = _calculate_tss(flags, dex_metrics)
        components = {
            "tss": {
                "score": score,
                "dexscreener": dex_metrics,
                "goplus": goplus_metrics,
            },
            "flags": flags,
        }
    except Exception:
        logger.exception("risk_data_unavailable chain=%s token=%s", chain, token_address)
        score = 0.0
        components = {
            "tss": {"score": score, "dexscreener": {}, "goplus": {}},
            "flags": ["data_unavailable"],
        }
    await _store_token_risk(
        session=session,
        chain=chain,
        token_address=token_address,
        score=score,
        components=components,
    )


async def process_risk_batch(
    redis: Redis,
    *,
    client: HttpClient,
    session: Any,
    count: int = 10,
    block_ms: int = 5_000,
) -> int:
    messages = await consume_from_stream(
        redis,
        stream=STREAM_RISK_JOBS,
        group=RISK_GROUP,
        consumer=RISK_CONSUMER,
        count=count,
        block_ms=block_ms,
    )
    for message_id, fields in messages:
        chain = str(fields.get("chain") or "ethereum").lower()
        token_address = _normalize_address(fields.get("token_address") or fields.get("tokenAddress"))
        if not token_address:
            logger.debug("risk_job_missing_token chain=%s", chain)
            await acknowledge_message(
                redis,
                stream=STREAM_RISK_JOBS,
                group=RISK_GROUP,
                message_id=message_id,
            )
            continue
        try:
            await handle_risk_job(client, session=session, chain=chain, token_address=token_address)
            await acknowledge_message(
                redis,
                stream=STREAM_RISK_JOBS,
                group=RISK_GROUP,
                message_id=message_id,
            )
        except Exception:
            logger.exception("risk_job_failed", extra={"message_id": message_id})
            await retry_or_dead_letter(
                redis,
                stream=STREAM_RISK_JOBS,
                group=RISK_GROUP,
                message_id=message_id,
                fields=fields,
            )
    return len(messages)


async def run_worker() -> None:
    validate_chain_config()
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    await ensure_consumer_group(redis, stream=STREAM_DECODED_TRADES, group=DECODED_GROUP)
    await ensure_consumer_group(redis, stream=STREAM_RISK_JOBS, group=RISK_GROUP)
    logger.info("risk_worker_started")
    stop_event = asyncio.Event()
    install_shutdown_handlers(stop_event, logger)
    try:
        async with async_session() as session:
            async with HttpClient() as client:
                while not stop_event.is_set():
                    decoded = await process_decoded_batch(redis)
                    risk_jobs = await process_risk_batch(redis, client=client, session=session)
                    if decoded == 0 and risk_jobs == 0:
                        try:
                            await asyncio.wait_for(stop_event.wait(), timeout=1.0)
                        except asyncio.TimeoutError:
                            continue
    finally:
        await redis.close()


async def run_once() -> int:
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    await ensure_consumer_group(redis, stream=STREAM_DECODED_TRADES, group=DECODED_GROUP)
    await ensure_consumer_group(redis, stream=STREAM_RISK_JOBS, group=RISK_GROUP)
    try:
        async with async_session() as session:
            async with HttpClient() as client:
                decoded = await process_decoded_batch(redis, count=1, block_ms=2_000)
                risk_jobs = await process_risk_batch(
                    redis, client=client, session=session, count=1, block_ms=2_000
                )
                return decoded + risk_jobs
    finally:
        await redis.close()


if __name__ == "__main__":
    asyncio.run(run_worker())
