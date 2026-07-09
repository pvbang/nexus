"""
MekongAI Nexus - Pluggable Memory Provider Abstraction.

Supports: local (file/DB), mem0, honcho.
Inspired by: hermes-agent pluggable memory providers

Usage::

    from app.memory.providers import MemoryProviderFactory

    provider = MemoryProviderFactory.create()  # uses config.memory_provider
    await provider.store("key1", "user prefers dark mode", {"tag": "preference"})
    results = await provider.recall("dark mode", limit=5)
"""

from __future__ import annotations

import abc
import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import structlog

logger = structlog.get_logger(__name__)

# Local file-based storage directory
_LOCAL_STORE_DIR = Path("workspace_data/.memory_store")
_LOCAL_STORE_DIR.mkdir(parents=True, exist_ok=True)


# ======================================================================
# Abstract base
# ======================================================================


class MemoryProvider(abc.ABC):
    """Abstract interface for all memory backends.

    Every provider must implement *store*, *recall*, and *delete*.
    """

    @abc.abstractmethod
    async def store(self, key: str, content: str, metadata: Optional[dict[str, Any]] = None) -> None:
        """Persist a memory entry identified by *key*."""

    @abc.abstractmethod
    async def recall(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        """Return memories matching *query*, up to *limit* results."""

    @abc.abstractmethod
    async def delete(self, key: str) -> bool:
        """Remove a memory entry by *key*. Return True if deleted."""


# ======================================================================
# Local (file/JSON) provider
# ======================================================================


class LocalMemoryProvider(MemoryProvider):
    """Simple file-based memory provider using a JSON store.

    Each memory is a JSON object stored in a single JSONL file under
    ``workspace_data/.memory_store/``.  Recall performs a case-insensitive
    substring search over stored content — no vector DB required.
    """

    def __init__(self, namespace: str = "default") -> None:
        self.namespace = namespace
        self.store_file = _LOCAL_STORE_DIR / f"{namespace}.jsonl"
        if not self.store_file.exists():
            self.store_file.touch()

    async def store(self, key: str, content: str, metadata: Optional[dict[str, Any]] = None) -> None:
        entry = {
            "id": str(uuid.uuid4()),
            "key": key,
            "content": content,
            "metadata": metadata or {},
            "created_at": datetime.utcnow().isoformat(),
        }
        with open(self.store_file, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        logger.debug("local_memory_stored", key=key, namespace=self.namespace)

    async def recall(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        if not self.store_file.exists():
            return []

        query_lower = query.lower()
        results: list[dict[str, Any]] = []

        for line in self.store_file.read_text(encoding="utf-8").strip().split("\n"):
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue

            # Simple substring match on content + key
            text = f"{entry.get('key', '')} {entry.get('content', '')}".lower()
            if query_lower in text:
                results.append(entry)
                if len(results) >= limit:
                    break

        return results

    async def delete(self, key: str) -> bool:
        if not self.store_file.exists():
            return False

        lines = self.store_file.read_text(encoding="utf-8").strip().split("\n")
        kept: list[str] = []
        deleted = False

        for line in lines:
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                kept.append(line)
                continue
            if entry.get("key") == key:
                deleted = True
            else:
                kept.append(line)

        self.store_file.write_text("\n".join(kept) + ("\n" if kept else ""), encoding="utf-8")
        return deleted


# ======================================================================
# Mem0 provider (optional)
# ======================================================================


class Mem0Provider(MemoryProvider):
    """Memory provider wrapping the `mem0` library (https://github.com/mem0ai/mem0).

    Requires ``pip install mem0ai``.  Falls back gracefully if not installed.
    """

    def __init__(self, api_key: str = "", user_id: str = "default") -> None:
        self._client: Any = None
        self._user_id = user_id
        self._api_key = api_key
        self._available = False

        try:
            from mem0 import MemoryClient  # type: ignore[import-untyped]

            if api_key:
                self._client = MemoryClient(api_key=api_key)
            else:
                # Use local Mem0 (requires additional setup)
                from mem0 import Memory  # type: ignore[import-untyped]

                self._client = Memory()
            self._available = True
            logger.info("mem0_provider_initialized")
        except ImportError:
            logger.warning("mem0_not_installed", hint="pip install mem0ai")
        except Exception as exc:
            logger.warning("mem0_init_failed", error=str(exc))

    async def store(self, key: str, content: str, metadata: Optional[dict[str, Any]] = None) -> None:
        if not self._available or self._client is None:
            logger.warning("mem0_not_available_store")
            return

        combined = f"[{key}] {content}"
        try:
            self._client.add(combined, user_id=self._user_id, metadata=metadata or {})
            logger.debug("mem0_stored", key=key)
        except Exception as exc:
            logger.error("mem0_store_error", error=str(exc))

    async def recall(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        if not self._available or self._client is None:
            return []

        try:
            results = self._client.search(query, user_id=self._user_id, limit=limit)
            # Normalize to standard format
            memories: list[dict[str, Any]] = []
            raw_list = results if isinstance(results, list) else results.get("results", [])
            for item in raw_list:
                memories.append({
                    "id": item.get("id", ""),
                    "key": "",
                    "content": item.get("memory", item.get("content", "")),
                    "metadata": item.get("metadata", {}),
                    "score": item.get("score", 0.0),
                })
            return memories[:limit]
        except Exception as exc:
            logger.error("mem0_recall_error", error=str(exc))
            return []

    async def delete(self, key: str) -> bool:
        if not self._available or self._client is None:
            return False

        try:
            # mem0 deletes by memory id; we search first
            results = await self.recall(key, limit=1)
            if results and results[0].get("id"):
                self._client.delete(results[0]["id"])
                return True
        except Exception as exc:
            logger.error("mem0_delete_error", error=str(exc))
        return False


# ======================================================================
# Factory
# ======================================================================


class MemoryProviderFactory:
    """Create the appropriate :class:`MemoryProvider` based on configuration."""

    _registry: dict[str, type[MemoryProvider]] = {
        "local": LocalMemoryProvider,
        "mem0": Mem0Provider,
    }

    @classmethod
    def register(cls, name: str, provider_class: type[MemoryProvider]) -> None:
        """Register a custom provider (e.g. honcho, zep)."""
        cls._registry[name] = provider_class
        logger.info("memory_provider_registered", name=name)

    @classmethod
    def create(
        cls,
        provider_name: Optional[str] = None,
        **kwargs: Any,
    ) -> MemoryProvider:
        """Instantiate a provider.

        Parameters
        ----------
        provider_name:
            One of ``"local"`` | ``"mem0"``.  When *None*, reads from
            ``settings.memory_provider``.
        **kwargs:
            Extra keyword arguments forwarded to the provider constructor.
        """
        if provider_name is None:
            from app.config import get_settings

            provider_name = get_settings().memory_provider

        provider_class = cls._registry.get(provider_name)
        if provider_class is None:
            logger.warning(
                "unknown_memory_provider",
                name=provider_name,
                available=list(cls._registry.keys()),
            )
            # Fallback to local
            provider_class = LocalMemoryProvider

        return provider_class(**kwargs)

    @classmethod
    def available_providers(cls) -> list[str]:
        """Return names of all registered providers."""
        return sorted(cls._registry.keys())
