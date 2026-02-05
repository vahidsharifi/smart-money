from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import websockets
from redis.asyncio import Redis

from app.config import settings, validate_chain_config
from app.logging import configure_logging
from app.utils import STREAM_RAW_EVENTS, dedupe_with_ttl, install_shutdown_handlers, publish_to_stream

configure_logging()
logger = logging.getLogger(__name__)

DEDUPLICATION_SET = "titan:raw_events:dedupe"
DEDUPLICATION_TTL_SECONDS = 3_600
RECONNECT_BASE_SECONDS = 1.0
RECONNECT_MAX_SECONDS = 30.0


def build_watchlist() -> dict[str, list[str]]:
    return {
        "ethereum": [address.lower() for address in settings.watched_addresses_eth],
        "bsc": [address.lower() for address in settings.watched_addresses_bsc],
    }


def get_ws_url(chain: str) -> str:
    config = settings.chain_config[chain]
    if not config.rpc_ws:
        raise RuntimeError(f"CHAIN_CONFIG for {chain} must include rpc_ws for listener")
    return config.rpc_ws


async def publish_log(redis: Redis, *, chain: str, log_event: dict[str, Any]) -> None:
    tx_hash = log_event.get("transactionHash")
    log_index = log_event.get("logIndex")
    if not tx_hash or log_index is None:
        logger.warning("listener_missing_log_fields chain=%s", chain)
        return

    dedupe_value = f"{chain}:{tx_hash}:{log_index}"
    is_duplicate = await dedupe_with_ttl(
        redis,
        key=DEDUPLICATION_SET,
        value=dedupe_value,
        ttl_seconds=DEDUPLICATION_TTL_SECONDS,
    )
    if is_duplicate:
        logger.debug("listener_deduped chain=%s tx=%s log_index=%s", chain, tx_hash, log_index)
        return

    payload = {
        "chain": chain,
        "address": log_event.get("address"),
        "topics": json.dumps(log_event.get("topics", [])),
        "data": log_event.get("data"),
        "blockNumber": log_event.get("blockNumber"),
        "txHash": tx_hash,
        "logIndex": log_index,
    }
    await publish_to_stream(redis, STREAM_RAW_EVENTS, payload)
    logger.info("listener_published chain=%s tx=%s log_index=%s", chain, tx_hash, log_index)


async def listen_chain(
    redis: Redis,
    *,
    chain: str,
    ws_url: str,
    addresses: list[str],
    stop_event: asyncio.Event,
) -> None:
    backoff = RECONNECT_BASE_SECONDS
    while not stop_event.is_set():
        if not addresses:
            logger.warning("listener_no_addresses chain=%s", chain)
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=60.0)
            except asyncio.TimeoutError:
                continue
            continue

        try:
            logger.info("listener_connecting chain=%s ws=%s", chain, ws_url)
            async with websockets.connect(ws_url, ping_interval=20, ping_timeout=20) as socket:
                subscribe_payload = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "eth_subscribe",
                    "params": ["logs", {"address": addresses}],
                }
                await socket.send(json.dumps(subscribe_payload))
                logger.info("listener_subscribe_sent chain=%s", chain)

                subscription_id: str | None = None
                backoff = RECONNECT_BASE_SECONDS
                while not stop_event.is_set():
                    try:
                        message = await asyncio.wait_for(socket.recv(), timeout=1.0)
                    except asyncio.TimeoutError:
                        continue
                    data = json.loads(message)
                    if data.get("id") == 1 and "result" in data:
                        subscription_id = data["result"]
                        logger.info("listener_subscribed chain=%s sub=%s", chain, subscription_id)
                        continue
                    if data.get("method") != "eth_subscription":
                        logger.debug("listener_ignored_message chain=%s payload=%s", chain, data)
                        continue
                    params = data.get("params", {})
                    if subscription_id and params.get("subscription") != subscription_id:
                        logger.debug("listener_subscription_mismatch chain=%s payload=%s", chain, data)
                        continue
                    log_event = params.get("result")
                    if not isinstance(log_event, dict):
                        logger.debug("listener_empty_log chain=%s payload=%s", chain, data)
                        continue
                    await publish_log(redis, chain=chain, log_event=log_event)
        except Exception as exc:
            logger.warning(
                "listener_connection_error chain=%s error=%s backoff=%s",
                chain,
                exc,
                backoff,
            )
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=backoff)
            except asyncio.TimeoutError:
                pass
            backoff = min(backoff * 2, RECONNECT_MAX_SECONDS)


async def run_worker() -> None:
    validate_chain_config()
    watchlist = build_watchlist()
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    logger.info(
        "listener_started chains=%s",
        list(settings.chain_config.keys()),
    )
    stop_event = asyncio.Event()
    install_shutdown_handlers(stop_event, logger)
    try:
        tasks = []
        for chain in ("ethereum", "bsc"):
            ws_url = get_ws_url(chain)
            tasks.append(
                asyncio.create_task(
                    listen_chain(
                        redis,
                        chain=chain,
                        ws_url=ws_url,
                        addresses=watchlist[chain],
                        stop_event=stop_event,
                    )
                )
            )
        await asyncio.gather(*tasks)
    finally:
        await redis.close()


if __name__ == "__main__":
    asyncio.run(run_worker())
