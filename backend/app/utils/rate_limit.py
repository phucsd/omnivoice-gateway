"""Simple thread-safe in-memory sliding window rate limiter for sensitive endpoints."""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from typing import Deque, Dict

from fastapi import HTTPException, Request, status

from app.config import settings


class InMemoryRateLimiter:
    def __init__(self) -> None:
        self._buckets: Dict[str, Deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, key: str, *, limit: int, window_seconds: int) -> None:
        now = time.monotonic()
        cutoff = now - window_seconds
        with self._lock:
            bucket = self._buckets[key]
            while bucket and bucket[0] < cutoff:
                bucket.popleft()
            if len(bucket) >= limit:
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail="Quá nhiều yêu cầu trong thời gian ngắn. Vui lòng thử lại sau ít phút.",
                )
            bucket.append(now)

    def clear(self) -> None:
        with self._lock:
            self._buckets.clear()


rate_limiter = InMemoryRateLimiter()


def get_client_ip(request: Request) -> str:
    """Resolve best-effort client IP respecting reverse-proxy forwarded headers."""
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def enforce_auth_rate_limit(
    request: Request,
    action: str,
    *,
    limit: int = 10,
    window_seconds: int = 300,
    extra_key: str = "",
) -> None:
    """Rate limit auth operations except when explicitly bypassed by unit tests."""
    if (settings.APP_ENV or "").lower() == "testing":
        return
    ip = get_client_ip(request)
    suffix = f":{extra_key.strip().lower()}" if extra_key else ""
    rate_limiter.check(f"{action}:{ip}{suffix}", limit=limit, window_seconds=window_seconds)
