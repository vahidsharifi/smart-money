import json
import logging
from typing import Any

import httpx
from redis.asyncio import Redis

from app.config import settings
from app.utils import HttpClient, RetryConfig

logger = logging.getLogger(__name__)

_http_client: HttpClient | None = None

async def get_http_client() -> HttpClient:
    global _http_client
    if _http_client is None:
        _http_client = HttpClient(
            retry_config=RetryConfig(attempts=3, backoff_factor=0.5, max_backoff=5.0)
        )
    return _http_client


async def close_http_client() -> None:
    global _http_client
    if _http_client is not None:
        await _http_client.close()
        _http_client = None


async def fetch_with_retries(url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    client = await get_http_client()
    try:
        payload = await client.get_json(url, params=params)
        if isinstance(payload, dict):
            return payload
        raise ValueError("Expected JSON object")
    except (httpx.HTTPError, json.JSONDecodeError, ValueError) as exc:
        logger.warning("request_failed", extra={"url": url, "error": str(exc)})
        raise


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
                json={"model": settings.ollama_model, "prompt": prompt, "stream": False},
            )
            response.raise_for_status()
            payload = response.json()
            return payload.get("response", "")
    except httpx.HTTPError as exc:
        logger.warning("ollama_failed", extra={"error": str(exc)})
        return "Narrative unavailable; see structured reasons."
