"""
MekongAI Nexus - Redis API Cache Layer.

Cache API responses in Redis with configurable TTL.
Dramatically reduces DB load for frequently accessed endpoints
like session lists, agent lists, system stats.

Usage:
    from app.core.cache import cache_response, invalidate_cache

    @router.get("/sessions")
    @cache_response(prefix="sessions", ttl=30)
    async def list_sessions(...):
        ...

    # Invalidate when data changes:
    await invalidate_cache("sessions")
"""

import functools
import hashlib
import json
from typing import Any, Optional

import structlog

from app.core.dependencies import get_redis

logger = structlog.get_logger(__name__)

# Default TTLs (seconds) for different cache categories
CACHE_TTLS = {
    "sessions": 15,       # Session list — moderate staleness OK
    "session_messages": 5, # Messages — need fresher data
    "agents": 10,         # Agent list
    "tasks": 10,          # Task list
    "swarms": 10,         # Swarm list
    "stats": 30,          # System stats — slow-changing
    "artifacts": 20,      # Artifacts list
    "profiles": 300,      # Profiles — rarely change
    "models": 300,        # Models — rarely change
}


def _make_cache_key(prefix: str, params: dict[str, Any]) -> str:
    """Generate a deterministic cache key from prefix + params."""
    params_str = json.dumps(params, sort_keys=True, default=str)
    param_hash = hashlib.md5(params_str.encode()).hexdigest()[:12]
    return f"nexus:cache:{prefix}:{param_hash}"


async def get_cached(prefix: str, params: dict[str, Any]) -> Optional[Any]:
    """Get a cached response from Redis."""
    try:
        redis = await get_redis()
        key = _make_cache_key(prefix, params)
        data = await redis.get(key)
        if data:
            logger.debug("cache_hit", prefix=prefix, key=key)
            return json.loads(data)
    except Exception as e:
        logger.debug("cache_get_error", error=str(e))
    return None


async def set_cached(prefix: str, params: dict[str, Any], value: Any, ttl: Optional[int] = None) -> None:
    """Set a cached response in Redis with TTL."""
    try:
        redis = await get_redis()
        key = _make_cache_key(prefix, params)
        _ttl = ttl or CACHE_TTLS.get(prefix, 30)
        await redis.setex(key, _ttl, json.dumps(value, default=str))
        logger.debug("cache_set", prefix=prefix, key=key, ttl=_ttl)
    except Exception as e:
        logger.debug("cache_set_error", error=str(e))


async def invalidate_cache(prefix: str) -> int:
    """Invalidate all cache entries for a given prefix.

    Uses SCAN to find and delete matching keys (safe for production).
    Returns count of deleted keys.
    """
    try:
        redis = await get_redis()
        pattern = f"nexus:cache:{prefix}:*"
        deleted = 0
        async for key in redis.scan_iter(match=pattern, count=100):
            await redis.delete(key)
            deleted += 1
        if deleted > 0:
            logger.debug("cache_invalidated", prefix=prefix, count=deleted)
        return deleted
    except Exception as e:
        logger.debug("cache_invalidate_error", error=str(e))
        return 0


async def invalidate_all_cache() -> int:
    """Invalidate all Nexus cache entries."""
    try:
        redis = await get_redis()
        deleted = 0
        async for key in redis.scan_iter(match="nexus:cache:*", count=100):
            await redis.delete(key)
            deleted += 1
        logger.info("cache_all_invalidated", count=deleted)
        return deleted
    except Exception as e:
        logger.debug("cache_invalidate_all_error", error=str(e))
        return 0


def cache_response(prefix: str, ttl: Optional[int] = None):
    """Decorator to cache FastAPI endpoint responses in Redis.

    Caches the JSON-serializable return value keyed by
    the prefix + all function arguments.

    Example::

        @router.get("/sessions")
        @cache_response(prefix="sessions", ttl=15)
        async def list_sessions(limit: int = 50, db = Depends(get_db)):
            ...
    """

    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            # Build params dict from kwargs (skip non-serializable like db sessions)
            cache_params = {}
            for k, v in kwargs.items():
                if isinstance(v, (str, int, float, bool, type(None))):
                    cache_params[k] = v

            # Check cache
            cached = await get_cached(prefix, cache_params)
            if cached is not None:
                return cached

            # Execute original function
            result = await func(*args, **kwargs)

            # Cache the result (must be JSON-serializable)
            try:
                await set_cached(prefix, cache_params, result, ttl)
            except Exception:
                pass  # Don't fail request if caching fails

            return result

        return wrapper

    return decorator
