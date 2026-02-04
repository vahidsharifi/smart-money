from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import httpx


@dataclass(slots=True)
class RetryConfig:
    attempts: int = 3
    backoff_factor: float = 0.5
    max_backoff: float = 5.0


class HttpClient:
    def __init__(
        self,
        *,
        timeout: float = 10.0,
        user_agent: str = "smart-money-service/1.0",
        retry_config: RetryConfig | None = None,
    ) -> None:
        self._timeout = httpx.Timeout(timeout)
        self._headers = {"User-Agent": user_agent}
        self._retry = retry_config or RetryConfig()
        self._client = httpx.AsyncClient(timeout=self._timeout, headers=self._headers)

    async def request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        last_error: Exception | None = None
        for attempt in range(1, self._retry.attempts + 1):
            try:
                response = await self._client.request(method, url, **kwargs)
                response.raise_for_status()
                return response
            except (httpx.HTTPError, httpx.TimeoutException) as exc:
                last_error = exc
                if attempt >= self._retry.attempts:
                    break
                backoff = min(self._retry.backoff_factor * (2 ** (attempt - 1)), self._retry.max_backoff)
                await asyncio.sleep(backoff)
        assert last_error is not None
        raise last_error

    async def get_json(self, url: str, **kwargs: Any) -> Any:
        response = await self.request("GET", url, **kwargs)
        return response.json()

    async def close(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> "HttpClient":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.close()
