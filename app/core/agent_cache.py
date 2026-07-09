"""
MekongAI Nexus - Agent/Orchestrator Instance Cache.

LRU cache for orchestrator instances per session, avoiding expensive
re-creation on every message. Hermes-agent inspired pattern.

Features:
- Cache key = session_key (user_id:conversation_id)
- Max size from settings (default 100)
- TTL eviction on idle (default 30 minutes)
- Thread-safe with asyncio.Lock
"""

import asyncio
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Generic, Optional, TypeVar

import structlog

logger = structlog.get_logger(__name__)

T = TypeVar("T")


@dataclass
class CacheEntry(Generic[T]):
    """Single cache entry with TTL tracking."""

    value: T
    created_at: float = field(default_factory=time.monotonic)
    last_accessed: float = field(default_factory=time.monotonic)
    access_count: int = 0

    def touch(self) -> None:
        """Update last access time."""
        self.last_accessed = time.monotonic()
        self.access_count += 1

    def is_expired(self, ttl_seconds: float) -> bool:
        """Check if entry has been idle longer than TTL."""
        return (time.monotonic() - self.last_accessed) > ttl_seconds


class AgentCache(Generic[T]):
    """
    LRU cache for agent/orchestrator instances with TTL eviction.

    Thread-safe via asyncio.Lock. Supports:
    - get_cached(key) → Optional[T]
    - put(key, value) → None
    - evict(key) → None
    - evict_all() → None
    - cleanup_expired() → int (count evicted)

    Usage:
        cache = AgentCache[CoreOrchestrator](max_size=100, ttl_seconds=1800)
        orch = await cache.get_cached(session_key)
        if orch is None:
            orch = CoreOrchestrator(db)
            await cache.put(session_key, orch)
    """

    def __init__(
        self,
        max_size: int = 100,
        ttl_seconds: float = 1800.0,  # 30 minutes default
    ) -> None:
        self._max_size = max_size
        self._ttl_seconds = ttl_seconds
        self._cache: OrderedDict[str, CacheEntry[T]] = OrderedDict()
        self._lock = asyncio.Lock()
        self._stats = CacheStats()

    @property
    def size(self) -> int:
        """Current number of cached entries."""
        return len(self._cache)

    @property
    def max_size(self) -> int:
        """Maximum cache capacity."""
        return self._max_size

    @property
    def stats(self) -> "CacheStats":
        """Access cache statistics."""
        return self._stats

    async def get_cached(self, key: str) -> Optional[T]:
        """
        Get a cached instance by key.

        Returns None if not found or expired.
        Moves entry to end of OrderedDict (LRU update).
        """
        async with self._lock:
            entry = self._cache.get(key)
            if entry is None:
                self._stats.misses += 1
                return None

            # Check TTL expiration
            if entry.is_expired(self._ttl_seconds):
                del self._cache[key]
                self._stats.evictions_ttl += 1
                self._stats.misses += 1
                logger.debug(
                    "agent_cache_ttl_evict",
                    key=key,
                    idle_seconds=round(time.monotonic() - entry.last_accessed, 1),
                )
                return None

            # LRU: move to end (most recently used)
            self._cache.move_to_end(key)
            entry.touch()
            self._stats.hits += 1
            return entry.value

    async def put(self, key: str, value: T) -> None:
        """
        Store an instance in cache.

        If cache is full, evicts the least recently used entry.
        If key already exists, updates value and moves to end.
        """
        async with self._lock:
            # Update existing entry
            if key in self._cache:
                self._cache[key] = CacheEntry(value=value)
                self._cache.move_to_end(key)
                return

            # Evict LRU if at capacity
            while len(self._cache) >= self._max_size:
                evicted_key, evicted_entry = self._cache.popitem(last=False)
                self._stats.evictions_lru += 1
                logger.debug(
                    "agent_cache_lru_evict",
                    evicted_key=evicted_key,
                    access_count=evicted_entry.access_count,
                )

            self._cache[key] = CacheEntry(value=value)

    async def evict(self, key: str) -> bool:
        """
        Explicitly evict a specific entry.

        Returns True if entry was found and removed.
        """
        async with self._lock:
            if key in self._cache:
                del self._cache[key]
                self._stats.evictions_explicit += 1
                logger.debug("agent_cache_explicit_evict", key=key)
                return True
            return False

    async def evict_all(self) -> int:
        """
        Evict all entries. Returns count of entries cleared.
        """
        async with self._lock:
            count = len(self._cache)
            self._cache.clear()
            self._stats.evictions_explicit += count
            logger.info("agent_cache_evict_all", count=count)
            return count

    async def cleanup_expired(self) -> int:
        """
        Remove all expired entries (idle > TTL).

        Should be called periodically (e.g., every 5 minutes).
        Returns number of entries evicted.
        """
        async with self._lock:
            expired_keys: list[str] = []
            for key, entry in self._cache.items():
                if entry.is_expired(self._ttl_seconds):
                    expired_keys.append(key)

            for key in expired_keys:
                del self._cache[key]
                self._stats.evictions_ttl += 1

            if expired_keys:
                logger.info(
                    "agent_cache_cleanup_expired",
                    evicted=len(expired_keys),
                    remaining=len(self._cache),
                )
            return len(expired_keys)

    async def keys(self) -> list[str]:
        """Get all cache keys (snapshot)."""
        async with self._lock:
            return list(self._cache.keys())

    async def contains(self, key: str) -> bool:
        """Check if key exists in cache (does NOT update LRU or check TTL)."""
        async with self._lock:
            return key in self._cache


@dataclass
class CacheStats:
    """Cache performance statistics."""

    hits: int = 0
    misses: int = 0
    evictions_lru: int = 0
    evictions_ttl: int = 0
    evictions_explicit: int = 0

    @property
    def total_requests(self) -> int:
        return self.hits + self.misses

    @property
    def hit_rate(self) -> float:
        """Hit rate as a percentage (0-100)."""
        total = self.total_requests
        if total == 0:
            return 0.0
        return (self.hits / total) * 100.0

    @property
    def total_evictions(self) -> int:
        return self.evictions_lru + self.evictions_ttl + self.evictions_explicit

    def to_dict(self) -> dict[str, Any]:
        return {
            "hits": self.hits,
            "misses": self.misses,
            "hit_rate": round(self.hit_rate, 1),
            "evictions_lru": self.evictions_lru,
            "evictions_ttl": self.evictions_ttl,
            "evictions_explicit": self.evictions_explicit,
            "total_requests": self.total_requests,
        }


# ── Module-level singleton ──

_global_agent_cache: Optional[AgentCache[Any]] = None


def get_agent_cache(
    max_size: int = 100,
    ttl_seconds: float = 1800.0,
) -> AgentCache[Any]:
    """Get or create the global agent cache singleton."""
    global _global_agent_cache
    if _global_agent_cache is None:
        _global_agent_cache = AgentCache(
            max_size=max_size,
            ttl_seconds=ttl_seconds,
        )
    return _global_agent_cache
