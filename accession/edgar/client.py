"""HTTP client for SEC EDGAR that follows the fair-access rules.

- Every request carries the declared User-Agent.
- A shared rate limiter keeps us under SEC's 10 requests/second ceiling.
- 429 and 5xx responses are retried with exponential backoff.
- Responses are cached in the object store. Archive documents are immutable,
  so they are never fetched twice. Index-style endpoints (submissions,
  company facts, the ticker map) change as companies file, so callers can
  ask for a refresh.
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from typing import Any

import httpx
import structlog

from accession.storage import ObjectStore

logger = structlog.get_logger(__name__)

_RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class RateLimiter:
    """Spaces calls at least `1 / max_per_second` apart, across threads."""

    def __init__(
        self,
        max_per_second: float,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._interval = 1.0 / max_per_second
        self._clock = clock
        self._sleep = sleep
        self._lock = threading.Lock()
        self._next_slot = 0.0

    def acquire(self) -> None:
        with self._lock:
            now = self._clock()
            wait = self._next_slot - now
            self._next_slot = max(now, self._next_slot) + self._interval
        if wait > 0:
            self._sleep(wait)


class EdgarError(RuntimeError):
    pass


class EdgarClient:
    def __init__(
        self,
        user_agent: str,
        store: ObjectStore,
        max_requests_per_second: float = 8.0,
        max_retries: int = 4,
        backoff_seconds: float = 1.0,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._store = store
        self._limiter = RateLimiter(max_requests_per_second, sleep=sleep)
        self._max_retries = max_retries
        self._backoff = backoff_seconds
        self._sleep = sleep
        self._http = httpx.Client(
            headers={"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"},
            timeout=30.0,
            follow_redirects=True,
            transport=transport,
        )

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> EdgarClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def get_bytes(self, url: str, cache_key: str, refresh: bool = False) -> bytes:
        """Return the body at `url`, from `cache_key` in the store when present."""
        if not refresh and self._store.exists(cache_key):
            logger.debug("edgar_cache_hit", key=cache_key)
            return self._store.get(cache_key)

        body = self._fetch(url)
        self._store.put(cache_key, body)
        logger.info("edgar_fetched", url=url, key=cache_key, bytes=len(body))
        return body

    def get_json(self, url: str, cache_key: str, refresh: bool = False) -> Any:
        return json.loads(self.get_bytes(url, cache_key, refresh=refresh))

    def _fetch(self, url: str) -> bytes:
        for attempt in range(self._max_retries + 1):
            self._limiter.acquire()
            try:
                response = self._http.get(url)
            except httpx.TransportError as exc:
                if attempt == self._max_retries:
                    raise EdgarError(f"GET {url} failed: {exc}") from exc
                self._backoff_sleep(attempt, None, url, str(exc))
                continue

            if response.status_code == 200:
                return response.content
            if response.status_code in _RETRYABLE_STATUS and attempt < self._max_retries:
                self._backoff_sleep(
                    attempt, response.headers.get("Retry-After"), url, response.status_code
                )
                continue
            raise EdgarError(f"GET {url} returned {response.status_code}")
        raise AssertionError("unreachable")

    def _backoff_sleep(
        self, attempt: int, retry_after: str | None, url: str, reason: object
    ) -> None:
        delay = self._backoff * 2**attempt
        if retry_after and retry_after.isdigit():
            delay = max(delay, float(retry_after))
        logger.warning("edgar_retry", url=url, attempt=attempt + 1, delay=delay, reason=reason)
        self._sleep(delay)
