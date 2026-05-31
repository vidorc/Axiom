"""Redis client factory (async).

Redis is transport, NOT a source of truth (ADR-0001): pub/sub for the live run
viewer and per-provider rate-limit token buckets. Wiping Redis must never lose
run state — a property the Phase 0 Spike A chaos test verifies (PHASE_0.md §4).
"""

from __future__ import annotations

from redis.asyncio import Redis

from axiom.platform.config import Settings, get_settings

_client: Redis | None = None


def get_redis(settings: Settings | None = None) -> Redis:
    """Return the process-wide async Redis client, creating it on first use."""
    global _client
    if _client is None:
        settings = settings or get_settings()
        _client = Redis.from_url(
            settings.redis_url,
            decode_responses=True,
        )
    return _client


async def close_redis() -> None:
    """Close the Redis client on graceful shutdown."""
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None
