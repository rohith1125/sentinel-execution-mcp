"""Sliding-window rate limiter backed by Redis with in-memory fallback."""

from __future__ import annotations

import time
from collections import defaultdict

import structlog

logger = structlog.get_logger(__name__)

# In-memory fallback store: client_id -> {window_key: count}
_mem_store: dict[str, dict[str, int]] = defaultdict(dict)


class RateLimiter:
    """
    Sliding window rate limiter backed by Redis.
    Falls back to in-memory if Redis unavailable.
    Key: sentinel:rl:{client_id}:{window_start_minute}
    """

    async def check_and_increment(
        self,
        client_id: str,
        limit_per_minute: int,
        redis: object | None,
    ) -> tuple[bool, int]:
        """Returns (allowed, remaining). Uses Redis INCR + EXPIRE."""
        window_key = str(int(time.time()) // 60)
        redis_key = f"sentinel:rl:{client_id}:{window_key}"

        if redis is not None:
            try:
                count = await redis.incr(redis_key)  # type: ignore[union-attr]
                if count == 1:
                    # First request in this window — set TTL of 90s to overlap windows
                    await redis.expire(redis_key, 90)  # type: ignore[union-attr]
                remaining = max(0, limit_per_minute - count)
                allowed = count <= limit_per_minute
                return allowed, remaining
            except Exception as exc:
                logger.warning("rate_limiter.redis_error", error=str(exc))
                # Fall through to in-memory

        # In-memory fallback
        bucket = _mem_store[client_id]
        # Purge old windows
        bucket = {k: v for k, v in bucket.items() if k == window_key}
        _mem_store[client_id] = bucket

        count = bucket.get(window_key, 0) + 1
        bucket[window_key] = count
        remaining = max(0, limit_per_minute - count)
        allowed = count <= limit_per_minute
        return allowed, remaining
