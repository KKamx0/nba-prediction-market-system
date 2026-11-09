"""Shared HTTP helper: rate limiting, bounded retries, JSON validation."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any

import httpx

from edgebot.ratelimit import TokenBucket

log = logging.getLogger(__name__)

RETRY_STATUSES = {429, 500, 502, 503, 504}
# Generic on purpose: no project URL or personal details go to third parties.
USER_AGENT = "edgebot/0.1"


class FetchError(RuntimeError):
    pass


class JsonClient:
    def __init__(
        self,
        base_url: str,
        *,
        rate_per_s: float = 2.0,
        max_retries: int = 4,
        timeout_s: float = 20.0,
        sleep: Callable[[float], None] = time.sleep,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._http = httpx.Client(
            base_url=base_url,
            timeout=timeout_s,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            transport=transport,
        )
        self._bucket = TokenBucket(rate_per_s, sleep=sleep)
        self._max_retries = max_retries
        self._sleep = sleep
        self.last_headers: httpx.Headers = httpx.Headers()

    def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        for attempt in range(self._max_retries + 1):
            self._bucket.acquire()
            try:
                resp = self._http.get(path, params=params)
            except httpx.TransportError as exc:
                if attempt == self._max_retries:
                    raise FetchError(f"GET {path} failed: {exc}") from exc
                self._sleep(2**attempt)
                continue
            if resp.status_code in RETRY_STATUSES and attempt < self._max_retries:
                retry_after = resp.headers.get("Retry-After")
                delay = float(retry_after) if retry_after and retry_after.isdigit() else 2**attempt
                log.warning("GET %s -> %s, retrying in %.1fs", path, resp.status_code, delay)
                self._sleep(delay)
                continue
            self.last_headers = resp.headers
            if resp.status_code != 200:
                # path only: query strings may carry API keys and must not be logged
                raise FetchError(f"GET {path} -> HTTP {resp.status_code}")
            try:
                return resp.json()
            except ValueError as exc:
                raise FetchError(f"GET {path} returned non-JSON") from exc
        raise FetchError(f"GET {path} exhausted retries")  # pragma: no cover

    def close(self) -> None:
        self._http.close()
