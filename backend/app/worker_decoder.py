from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import httpx
from redis.asyncio import Redis

from app.config import settings, validate_chain_config
from app.db import async_session
from app.dex_registry import lookup_dex
from app.logging import configure_logging
from app.models import Trade
from app.utils.ops import start_heartbeat, stop_heartbeat
from app.utils import (
    STREAM_DECODED_TRADES,
    STREAM_RAW_EVENTS,
    STREAM_RAW_EVENTS_DEAD,
    acknowledge_message,
    consume_from_stream,
    ensure_consumer_group,
    install_shutdown_handlers,
    publish_to_stream,
    retry_or_dead_letter,
    normalize_evm_address,
)
from app.utils.wallets import is_wallet_ignored

configure_logging()
logger = logging.getLogger(__name__)

GROUP_NAME = "decoders"
CONSUMER_NAME = "decoder-1"

UNISWAP_V2_SWAP_SIGNATURE = b"Swap(address,uint256,uint256,uint256,uint256,address)"
UNISWAP_V2_SYNC_SIGNATURE = b"Sync(uint112,uint112)"
UNISWAP_V3_SWAP_SIGNATURE = b"Swap(address,address,int256,int256,uint160,uint128,int24)"

TOKEN0_SELECTOR = "0x0dfe1681"
TOKEN1_SELECTOR = "0xd21220a7"
TOKEN_LOOKUP_TTL_SECONDS = 60 * 60 * 6
MIN_PUBLISH_CONFIDENCE = 0.6


def _rotl(value: int, shift: int) -> int:
    return ((value << shift) | (value >> (64 - shift))) & ((1 << 64) - 1)


def _keccak_f(state: list[int]) -> None:
    rounds = [
        0x0000000000000001,
        0x0000000000008082,
        0x800000000000808A,
        0x8000000080008000,
        0x000000000000808B,
        0x0000000080000001,
        0x8000000080008081,
        0x8000000000008009,
        0x000000000000008A,
        0x0000000000000088,
        0x0000000080008009,
        0x000000008000000A,
        0x000000008000808B,
        0x800000000000008B,
        0x8000000000008089,
        0x8000000000008003,
        0x8000000000008002,
        0x8000000000000080,
        0x000000000000800A,
        0x800000008000000A,
        0x8000000080008081,
        0x8000000000008080,
        0x0000000080000001,
        0x8000000080008008,
    ]
    rotation = [
        [0, 36, 3, 41, 18],
        [1, 44, 10, 45, 2],
        [62, 6, 43, 15, 61],
        [28, 55, 25, 21, 56],
        [27, 20, 39, 8, 14],
    ]

    for rc in rounds:
        c = [
            state[0] ^ state[5] ^ state[10] ^ state[15] ^ state[20],
            state[1] ^ state[6] ^ state[11] ^ state[16] ^ state[21],
            state[2] ^ state[7] ^ state[12] ^ state[17] ^ state[22],
            state[3] ^ state[8] ^ state[13] ^ state[18] ^ state[23],
            state[4] ^ state[9] ^ state[14] ^ state[19] ^ state[24],
        ]
        d = [
            c[4] ^ _rotl(c[1], 1),
            c[0] ^ _rotl(c[2], 1),
            c[1] ^ _rotl(c[3], 1),
            c[2] ^ _rotl(c[4], 1),
            c[3] ^ _rotl(c[0], 1),
        ]
        for x in range(5):
            for y in range(5):
                state[x + 5 * y] ^= d[x]

        b = [0] * 25
        for x in range(5):
            for y in range(5):
                b[y + 5 * ((2 * x + 3 * y) % 5)] = _rotl(
                    state[x + 5 * y],
                    rotation[x][y],
                )

        for x in range(5):
            for y in range(5):
                state[x + 5 * y] = b[x + 5 * y] ^ (
                    (~b[(x + 1) % 5 + 5 * y]) & b[(x + 2) % 5 + 5 * y]
                )

        state[0] ^= rc


def _keccak_256(data: bytes) -> bytes:
    rate = 136
    state = [0] * 25
    padded = bytearray(data)
    padded.append(0x01)
    while len(padded) % rate != rate - 1:
        padded.append(0x00)
    padded.append(0x80)

    for offset in range(0, len(padded), rate):
        block = padded[offset : offset + rate]
        for i in range(rate // 8):
            lane = int.from_bytes(block[i * 8 : (i + 1) * 8], "little")
            state[i] ^= lane
        _keccak_f(state)

    output = b"".join(state[i].to_bytes(8, "little") for i in range(25))
    return output[:32]


UNISWAP_V2_SWAP_TOPIC = f"0x{_keccak_256(UNISWAP_V2_SWAP_SIGNATURE).hex()}"
UNISWAP_V2_SYNC_TOPIC = f"0x{_keccak_256(UNISWAP_V2_SYNC_SIGNATURE).hex()}"
UNISWAP_V3_SWAP_TOPIC = f"0x{_keccak_256(UNISWAP_V3_SWAP_SIGNATURE).hex()}"


def _parse_int(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        if value.startswith("0x"):
            return int(value, 16)
        return int(value)
    return None


def _parse_topics(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return []
        if isinstance(parsed, list):
            return [str(item) for item in parsed]
    return []


def _decode_uint256_list(data: str, count: int) -> list[int] | None:
    if not isinstance(data, str):
        return None
    payload = data[2:] if data.startswith("0x") else data
    if len(payload) < 64 * count:
        return None
    values = []
    for i in range(count):
        start = i * 64
        end = start + 64
        try:
            values.append(int(payload[start:end], 16))
        except ValueError:
            return None
    return values


def _decode_int256_list(data: str, count: int) -> list[int] | None:
    raw = _decode_uint256_list(data, count)
    if raw is None:
        return None
    signed: list[int] = []
    for value in raw:
        if value >= (1 << 255):
            value -= (1 << 256)
        signed.append(value)
    return signed


def _parse_topic_address(topic: str | None) -> str | None:
    if not topic:
        return None
    payload = topic[2:] if topic.startswith("0x") else topic
    if len(payload) < 40:
        return None
    return f"0x{payload[-40:]}".lower()


def _parse_word_address(word_hex: str) -> str | None:
    payload = word_hex[2:] if word_hex.startswith("0x") else word_hex
    if len(payload) < 64:
        return None
    return f"0x{payload[-40:]}".lower()


def _rpc_http_url(chain: str) -> str | None:
    chain_cfg = settings.chain_config.get(chain)
    return chain_cfg.rpc_http if chain_cfg else None


async def _rpc_eth_call(chain: str, to_address: str, data: str) -> str | None:
    rpc_url = _rpc_http_url(chain)
    if not rpc_url:
        return None
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "eth_call",
        "params": [{"to": to_address, "data": data}, "latest"],
    }
    async with httpx.AsyncClient(timeout=5.0) as client:
        response = await client.post(rpc_url, json=payload)
    response.raise_for_status()
    body = response.json()
    if "error" in body:
        return None
    result = body.get("result")
    return result if isinstance(result, str) else None


async def _get_pool_token(redis: Redis, chain: str, pair_address: str, selector: str) -> str | None:
    cache_key = f"decode:token_lookup:{chain}:{pair_address.lower()}:{selector}"
    cached = await redis.get(cache_key)
    if cached:
        return cached
    try:
        raw = await _rpc_eth_call(chain, pair_address, selector)
    except Exception:
        logger.exception("token_lookup_failed", extra={"chain": chain, "pair": pair_address})
        return None
    if not raw:
        return None
    token_address = _parse_word_address(raw)
    if not token_address:
        return None
    await redis.set(cache_key, token_address, ex=TOKEN_LOOKUP_TTL_SECONDS)
    return token_address


async def decode_raw_event(redis: Redis, fields: dict[str, Any]) -> dict[str, Any]:
    chain = str(fields.get("chain", "ethereum")).lower()
    tx_hash = fields.get("txHash") or fields.get("tx_hash")
    log_index = _parse_int(fields.get("logIndex") or fields.get("log_index"))
    block_number = _parse_int(fields.get("blockNumber") or fields.get("block_number"))
    address = normalize_evm_address(fields.get("address"))
    topics = [topic.lower() for topic in _parse_topics(fields.get("topics"))]
    data = fields.get("data", "")

    wallet_address = None
    token_address = None
    side = None
    amount = None
    price = None
    usd_value = None
    dex = None
    pair_address = None
    decode_confidence = 0.0

    topic0 = topics[0] if topics else None
    registry_entry = lookup_dex(chain, address) if address else None

    if registry_entry and topic0 in (UNISWAP_V2_SWAP_TOPIC, UNISWAP_V3_SWAP_TOPIC):
        dex = registry_entry.dex
        pair_address = address
        decode_confidence = 0.5

        token0 = await _get_pool_token(redis, chain, pair_address, TOKEN0_SELECTOR)
        token1 = await _get_pool_token(redis, chain, pair_address, TOKEN1_SELECTOR)
        if token0 and token1:
            decode_confidence += 0.2
        elif token0 or token1:
            decode_confidence += 0.1

        if topic0 == UNISWAP_V2_SWAP_TOPIC:
            decoded = _decode_uint256_list(str(data), 4)
            if decoded is not None:
                amount0_in, amount1_in, amount0_out, amount1_out = decoded
                sender = _parse_topic_address(topics[1]) if len(topics) > 1 else None
                to_address = _parse_topic_address(topics[2]) if len(topics) > 2 else None
                wallet_address = sender or to_address
                if amount0_out > 0 or amount1_in > 0:
                    side = "buy"
                    token_address = token0 or token1
                    amount = float(amount0_out or amount1_in)
                else:
                    side = "sell"
                    token_address = token0 or token1
                    amount = float(amount0_in or amount1_out)
                decode_confidence += 0.2
        elif topic0 == UNISWAP_V3_SWAP_TOPIC:
            decoded = _decode_int256_list(str(data), 2)
            sender = _parse_topic_address(topics[1]) if len(topics) > 1 else None
            recipient = _parse_topic_address(topics[2]) if len(topics) > 2 else None
            wallet_address = sender or recipient
            if decoded is not None:
                amount0, amount1 = decoded
                side = "buy" if amount0 < 0 else "sell"
                token_address = token0 if amount0 != 0 else token1
                amount = float(abs(amount0) if amount0 != 0 else abs(amount1))
                decode_confidence += 0.2

    # Parse known sync events so the decoder recognizes them without emitting trades.
    if topic0 == UNISWAP_V2_SYNC_TOPIC and registry_entry and registry_entry.strategy == "v2_pair":
        decode_confidence = max(decode_confidence, 0.3)

    decode_confidence = min(decode_confidence, 1.0)

    return {
        "chain": chain,
        "tx_hash": tx_hash,
        "log_index": log_index or 0,
        "block_number": block_number,
        "wallet_address": wallet_address,
        "token_address": token_address,
        "side": side,
        "amount": amount,
        "price": price,
        "usd_value": usd_value,
        "dex": dex,
        "pair_address": pair_address,
        "decode_confidence": decode_confidence,
        "block_time": None,
    }


async def handle_message(redis: Redis, session: Any, fields: dict[str, Any]) -> None:
    record = await decode_raw_event(redis, fields)
    if not record["tx_hash"]:
        raise ValueError("missing tx_hash")
    if record.get("wallet_address") and await is_wallet_ignored(
        session, chain=record["chain"], wallet_address=record["wallet_address"]
    ):
        logger.info(
            "decoder_skipped_ignored_wallet chain=%s wallet=%s tx=%s",
            record["chain"],
            record["wallet_address"],
            record["tx_hash"],
        )
        return
    trade = Trade(**record)
    await session.merge(trade)
    await session.commit()

    if record["decode_confidence"] >= MIN_PUBLISH_CONFIDENCE:
        await publish_to_stream(
            redis,
            STREAM_DECODED_TRADES,
            {key: "" if value is None else str(value) for key, value in record.items()},
        )


async def process_batch(
    redis: Redis,
    session: Any,
    *,
    count: int = 10,
    block_ms: int = 5_000,
) -> int:
    messages = await consume_from_stream(
        redis,
        stream=STREAM_RAW_EVENTS,
        group=GROUP_NAME,
        consumer=CONSUMER_NAME,
        count=count,
        block_ms=block_ms,
    )
    for message_id, fields in messages:
        try:
            await handle_message(redis, session, fields)
            await acknowledge_message(
                redis,
                stream=STREAM_RAW_EVENTS,
                group=GROUP_NAME,
                message_id=message_id,
            )
        except Exception:
            logger.exception("decoder_message_failed", extra={"message_id": message_id})
            await retry_or_dead_letter(
                redis,
                stream=STREAM_RAW_EVENTS,
                group=GROUP_NAME,
                message_id=message_id,
                fields=fields,
                dead_letter_stream=STREAM_RAW_EVENTS_DEAD,
            )
    return len(messages)


async def run_worker() -> None:
    validate_chain_config()
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    await ensure_consumer_group(redis, stream=STREAM_RAW_EVENTS, group=GROUP_NAME)
    logger.info("decoder_started")
    heartbeat_task = await start_heartbeat(redis, worker_name=CONSUMER_NAME)
    stop_event = asyncio.Event()
    install_shutdown_handlers(stop_event, logger)
    try:
        async with async_session() as session:
            while not stop_event.is_set():
                processed = await process_batch(redis, session)
                if processed == 0:
                    try:
                        await asyncio.wait_for(stop_event.wait(), timeout=1.0)
                    except asyncio.TimeoutError:
                        continue
    finally:
        await stop_heartbeat(heartbeat_task)
        await redis.close()


async def run_once() -> int:
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    await ensure_consumer_group(redis, stream=STREAM_RAW_EVENTS, group=GROUP_NAME)
    try:
        async with async_session() as session:
            return await process_batch(redis, session, count=16, block_ms=2_000)
    finally:
        await redis.close()


if __name__ == "__main__":
    asyncio.run(run_worker())
