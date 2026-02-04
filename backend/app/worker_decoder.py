from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from redis.asyncio import Redis

from app.config import settings, validate_chain_config
from app.db import async_session
from app.logging import configure_logging
from app.models import Trade
from app.utils import (
    STREAM_DECODED_TRADES,
    STREAM_RAW_EVENTS,
    acknowledge_message,
    consume_from_stream,
    ensure_consumer_group,
    publish_to_stream,
    retry_or_dead_letter,
)

configure_logging()
logger = logging.getLogger(__name__)

GROUP_NAME = "decoders"
CONSUMER_NAME = "decoder-1"

UNISWAP_V2_SWAP_SIGNATURE = b"Swap(address,uint256,uint256,uint256,uint256,address)"


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
    data = data.lower()
    if data.startswith("0x"):
        data = data[2:]
    if len(data) < 64 * count:
        return None
    values = []
    for i in range(count):
        start = i * 64
        end = start + 64
        try:
            values.append(int(data[start:end], 16))
        except ValueError:
            return None
    return values


def _parse_topic_address(topic: str | None) -> str | None:
    if not topic:
        return None
    if topic.startswith("0x"):
        topic = topic[2:]
    if len(topic) < 40:
        return None
    return f"0x{topic[-40:]}".lower()


def decode_raw_event(fields: dict[str, Any]) -> dict[str, Any]:
    chain = fields.get("chain", "ethereum")
    tx_hash = fields.get("txHash") or fields.get("tx_hash")
    log_index = _parse_int(fields.get("logIndex") or fields.get("log_index"))
    block_number = _parse_int(fields.get("blockNumber") or fields.get("block_number"))
    address = fields.get("address")
    topics = _parse_topics(fields.get("topics"))
    data = fields.get("data", "")

    wallet_address = None
    token_address = None
    side = None
    amount = None
    price = None
    usd_value = None

    if topics and topics[0].lower() == UNISWAP_V2_SWAP_TOPIC:
        decoded = _decode_uint256_list(data, 4)
        if decoded is not None:
            sender = _parse_topic_address(topics[1]) if len(topics) > 1 else None
            to_address = _parse_topic_address(topics[2]) if len(topics) > 2 else None
            wallet_address = sender or to_address
            token_address = address

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
        "block_time": None,
    }


async def handle_message(redis: Redis, session: Any, fields: dict[str, Any]) -> None:
    record = decode_raw_event(fields)
    if not record["tx_hash"]:
        raise ValueError("missing tx_hash")
    trade = Trade(**record)
    await session.merge(trade)
    await session.commit()
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
            try:
                await session.rollback()
            except Exception:
                logger.exception(
                    "decoder_session_rollback_failed",
                    extra={"message_id": message_id},
                )
            await retry_or_dead_letter(
                redis,
                stream=STREAM_RAW_EVENTS,
                group=GROUP_NAME,
                message_id=message_id,
                fields=fields,
            )
    return len(messages)


async def run_worker() -> None:
    validate_chain_config()
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    await ensure_consumer_group(redis, stream=STREAM_RAW_EVENTS, group=GROUP_NAME)
    logger.info("decoder_started")
    try:
        async with async_session() as session:
            while True:
                processed = await process_batch(redis, session)
                if processed == 0:
                    await asyncio.sleep(1)
    finally:
        await redis.close()


async def run_once() -> int:
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    await ensure_consumer_group(redis, stream=STREAM_RAW_EVENTS, group=GROUP_NAME)
    try:
        async with async_session() as session:
            return await process_batch(redis, session, count=1, block_ms=2_000)
    finally:
        await redis.close()


if __name__ == "__main__":
    asyncio.run(run_worker())
