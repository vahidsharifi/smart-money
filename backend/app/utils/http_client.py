from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any

import httpx
import logging
import time


@dataclass(slots=True)
class RetryConfig:
    attempts: int = 3
    backoff_factor: float = 0.5
    max_backoff: float = 5.0
    circuit_breaker_threshold: int = 4
    circuit_breaker_timeout: float = 30.0


class HttpClient:
    def __init__(
        self,
        *,
        timeout: float = 10.0,
        user_agent: str = "smart-money-service/1.0",
        retry_config: RetryConfig | None = None,
    ) -> None:
        self._logger = logging.getLogger(__name__)
        self._timeout = httpx.Timeout(timeout)
        self._headers = {"User-Agent": user_agent}
        self._retry = retry_config or RetryConfig()
        self._client = httpx.AsyncClient(timeout=self._timeout, headers=self._headers)
        self._failure_count = 0
        self._circuit_open_until: float | None = None

    def _circuit_open(self) -> bool:
        if self._circuit_open_until is None:
            return False
        if time.monotonic() >= self._circuit_open_until:
            self._circuit_open_until = None
            self._failure_count = 0
            return False
        return True

    def _record_failure(self, exc: Exception) -> None:
        self._failure_count += 1
        if self._failure_count >= self._retry.circuit_breaker_threshold:
            self._circuit_open_until = time.monotonic() + self._retry.circuit_breaker_timeout
            self._logger.warning(
                "circuit_opened failures=%s timeout_seconds=%s error=%s",
                self._failure_count,
                self._retry.circuit_breaker_timeout,
                exc,
            )

    def _record_success(self) -> None:
        self._failure_count = 0
        self._circuit_open_until = None

    async def request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        last_error: Exception | None = None
        if self._circuit_open():
            raise httpx.HTTPError("circuit_breaker_open")
        for attempt in range(1, self._retry.attempts + 1):
            try:
                response = await self._client.request(method, url, **kwargs)
                response.raise_for_status()
                self._record_success()
                return response
            except (httpx.HTTPError, httpx.TimeoutException) as exc:
                last_error = exc
                self._record_failure(exc)
                if self._circuit_open():
                    break
                if attempt >= self._retry.attempts:
                    break
                backoff = min(self._retry.backoff_factor * (2 ** (attempt - 1)), self._retry.max_backoff)
                await asyncio.sleep(backoff)
        assert last_error is not None
        raise last_error

    async def get_json(self, url: str, **kwargs: Any) -> Any:
        last_error: Exception | None = None
        if self._circuit_open():
            raise httpx.HTTPError("circuit_breaker_open")
        for attempt in range(1, self._retry.attempts + 1):
            try:
                response = await self.request("GET", url, **kwargs)
                return response.json()
            except (json.JSONDecodeError, ValueError) as exc:
                last_error = exc
                self._record_failure(exc)
                if self._circuit_open():
                    break
                if attempt >= self._retry.attempts:
                    break
                backoff = min(
                    self._retry.backoff_factor * (2 ** (attempt - 1)),
                    self._retry.max_backoff,
                )
                await asyncio.sleep(backoff)
        assert last_error is not None
        raise last_error

    async def close(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> "HttpClient":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.close()
