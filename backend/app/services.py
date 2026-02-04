import asyncio
import json
import logging
import random
from typing import Any

import httpx
from redis.asyncio import Redis

from app.config import settings

logger = logging.getLogger(__name__)


async def fetch_with_retries(url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    backoff = 0.5
    max_attempts = 3
    for attempt in range(1, max_attempts + 1):
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(url, params=params)
                response.raise_for_status()
                return response.json()
        except (httpx.HTTPError, json.JSONDecodeError) as exc:
            logger.warning("request_failed", extra={"url": url, "attempt": attempt, "error": str(exc)})
            if attempt == max_attempts:
                raise
            sleep_time = backoff * (2 ** (attempt - 1)) + random.uniform(0, 0.2)
            await asyncio.sleep(sleep_time)
    return {}


async def get_cached_json(redis: Redis, key: str) -> dict[str, Any] | None:
    cached = await redis.get(key)
    if not cached:
        return None
    return json.loads(cached)


async def set_cached_json(redis: Redis, key: str, value: dict[str, Any], ttl: int) -> None:
    await redis.set(key, json.dumps(value), ex=ttl)


async def fetch_dexscreener(redis: Redis, token_address: str) -> dict[str, Any]:
    cache_key = f"dexscreener:{token_address}"
    cached = await get_cached_json(redis, cache_key)
    if cached:
        return cached
    url = f"{settings.dexscreener_base_url}/tokens/{token_address}"
    data = await fetch_with_retries(url)
    await set_cached_json(redis, cache_key, data, ttl=300)
    return data


async def fetch_goplus(redis: Redis, token_address: str, chain_id: str = "1") -> dict[str, Any]:
    cache_key = f"goplus:{chain_id}:{token_address}"
    cached = await get_cached_json(redis, cache_key)
    if cached:
        return cached
    url = f"{settings.goplus_base_url}/token_security/{chain_id}"
    data = await fetch_with_retries(url, params={"contract_addresses": token_address})
    await set_cached_json(redis, cache_key, data, ttl=600)
    return data


async def narrate_with_ollama(reasons: list[dict[str, Any]]) -> str:
    prompt = (
        "Summarize the following structured security reasons into a concise narrative. "
        "Do not add new facts. Reasons: "
        f"{json.dumps(reasons)}"
    )
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.post(
                f"{settings.ollama_url}/api/generate",
                json={"model": "llama3", "prompt": prompt, "stream": False},
            )
            response.raise_for_status()
            payload = response.json()
            return payload.get("response", "")
    except httpx.HTTPError as exc:
        logger.warning("ollama_failed", extra={"error": str(exc)})
        return "Narrative unavailable; see structured reasons."
