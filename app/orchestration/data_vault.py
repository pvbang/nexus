"""
MekongAI Nexus - Data Vault.

FIX GAP-1 & GAP-3: Persistent key-value store for full artifacts across turns.

Problem: Ledger only stores 1-2 sentence summaries. Agents after turn 30+ lose
access to full data (URLs, code, structured findings) discovered in early turns.

Solution: DataVault stores full artifacts keyed by (turn, agent, topic).
Agents can store/retrieve without budget constraints. Unlike the 12K-capped
previous_context, vault queries return FULL data on demand via the
`data_vault_store` and `data_vault_retrieve` tools.

Architecture:
- VAULT.jsonl: append-only, 1 entry per artifact
- In-memory index: by key, by turn, by agent, by tags (for fast lookup)
- Size-aware: tracks total bytes, can evict LRU if approaching disk limits
- Tool integration: agents call data_vault_store/data_vault_retrieve directly
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

import structlog

logger = structlog.get_logger(__name__)

_WORKSPACE_BASE = Path(os.environ.get("WORKSPACE_DIR", "workspace_data"))
_MAX_VAULT_BYTES = 10 * 1024 * 1024  # 10MB per task vault
_MAX_ENTRY_BYTES = 512 * 1024  # 512KB per single entry


@dataclass
class VaultEntry:
    """Single artifact stored in the vault."""

    key: str
    content: str
    turn: int
    agent_role: str
    agent_name: str
    tags: list[str] = field(default_factory=list)
    timestamp: float = field(default_factory=time.time)
    content_type: str = "text"  # text, json, code, url_list
    size_bytes: int = 0

    def __post_init__(self):
        if not self.size_bytes:
            self.size_bytes = len(self.content.encode("utf-8"))


class DataVault:
    """Persistent artifact store for cross-turn data access.

    Agents store full findings here (not just summaries). Other agents
    can retrieve by key, tags, turn range, or agent role — getting the
    COMPLETE data without budget truncation.

    Thread-safe for single-writer (one task at a time). Concurrent reads safe.
    """

    def __init__(self, task_id: str) -> None:
        from app.core import task_paths
        self._task_id = task_id
        self._context_dir = task_paths.context_dir(task_id)
        self._vault_path = self._context_dir / "VAULT.jsonl"
        self._entries: list[VaultEntry] = []
        self._index_by_key: dict[str, int] = {}
        self._index_by_tag: dict[str, list[int]] = {}
        self._index_by_role: dict[str, list[int]] = {}
        self._total_bytes: int = 0
        self._load_existing()

    def _load_existing(self) -> None:
        """Load vault from disk on init (cold start)."""
        if not self._vault_path.exists():
            return
        try:
            with open(self._vault_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                        entry = VaultEntry(**data)
                        self._add_to_index(entry)
                    except Exception:
                        continue
        except Exception as e:
            logger.warning("vault_load_failed", task=self._task_id, error=str(e)[:100])

    def _add_to_index(self, entry: VaultEntry) -> None:
        """Add entry to in-memory indexes."""
        idx = len(self._entries)
        self._entries.append(entry)
        self._index_by_key[entry.key] = idx
        for tag in entry.tags:
            self._index_by_tag.setdefault(tag, []).append(idx)
        self._index_by_role.setdefault(entry.agent_role, []).append(idx)
        self._total_bytes += entry.size_bytes

    def store(
        self,
        key: str,
        content: str,
        turn: int,
        agent_role: str,
        agent_name: str = "",
        tags: list[str] | None = None,
        content_type: str = "text",
    ) -> dict[str, Any]:
        """Store an artifact in the vault.

        Returns status dict with success/error info.
        Overwrites if same key exists (update semantics).
        """
        if not key or not content:
            return {"success": False, "error": "key and content are required"}

        # CC-6: scrub secrets before the artifact is persisted to VAULT.jsonl
        # (durable; cannot be redacted afterward). Done before the size check so
        # accounting reflects what actually lands on disk.
        try:
            from app.core.secret_redaction import redact_secrets
            content = redact_secrets(content)
        except Exception:
            pass

        content_bytes = len(content.encode("utf-8"))
        if content_bytes > _MAX_ENTRY_BYTES:
            return {
                "success": False,
                "error": f"Entry too large: {content_bytes} bytes (max {_MAX_ENTRY_BYTES})",
            }

        if self._total_bytes + content_bytes > _MAX_VAULT_BYTES:
            evicted = self._evict_lru(content_bytes)
            if self._total_bytes + content_bytes > _MAX_VAULT_BYTES:
                return {
                    "success": False,
                    "error": f"Vault full ({self._total_bytes} bytes), evicted {evicted} but still insufficient",
                }

        entry = VaultEntry(
            key=key,
            content=content,
            turn=turn,
            agent_role=agent_role,
            agent_name=agent_name,
            tags=tags or [],
            content_type=content_type,
        )

        # Overwrite existing key
        if key in self._index_by_key:
            old_idx = self._index_by_key[key]
            old_entry = self._entries[old_idx]
            self._total_bytes -= old_entry.size_bytes
            self._entries[old_idx] = entry
            entry_idx = old_idx
        else:
            entry_idx = len(self._entries)
            self._entries.append(entry)
            self._index_by_key[key] = entry_idx

        # Update tag/role indexes
        for tag in entry.tags:
            tag_list = self._index_by_tag.setdefault(tag, [])
            if entry_idx not in tag_list:
                tag_list.append(entry_idx)
        role_list = self._index_by_role.setdefault(entry.agent_role, [])
        if entry_idx not in role_list:
            role_list.append(entry_idx)
        self._total_bytes += entry.size_bytes

        # Persist to disk
        try:
            with open(self._vault_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(asdict(entry), ensure_ascii=False) + "\n")
        except Exception as e:
            logger.warning("vault_write_failed", error=str(e)[:100])

        return {
            "success": True,
            "key": key,
            "size_bytes": entry.size_bytes,
            "total_vault_bytes": self._total_bytes,
            "total_entries": len(self._entries),
        }

    def retrieve(
        self,
        key: str | None = None,
        tags: list[str] | None = None,
        agent_role: str | None = None,
        turn_range: tuple[int, int] | None = None,
        limit: int = 10,
        max_chars: int = 0,
    ) -> list[dict[str, Any]]:
        """Retrieve artifacts from the vault.

        Supports multiple query modes (can combine):
        - By exact key
        - By tags (entries matching ANY tag)
        - By agent_role
        - By turn range (inclusive)

        Returns list of entry dicts with full content (NO truncation by default).
        If max_chars > 0, truncates individual content fields.
        """
        candidates: set[int] | None = None

        if key:
            idx = self._index_by_key.get(key)
            if idx is not None:
                candidates = {idx}
            else:
                return []

        if tags:
            tag_matches: set[int] = set()
            for tag in tags:
                tag_matches.update(self._index_by_tag.get(tag, []))
            candidates = tag_matches if candidates is None else candidates & tag_matches

        if agent_role:
            role_matches = set(self._index_by_role.get(agent_role, []))
            candidates = role_matches if candidates is None else candidates & role_matches

        if candidates is None:
            candidates = set(range(len(self._entries)))

        # Filter by turn range
        results = []
        for idx in sorted(candidates, reverse=True):  # newest first
            entry = self._entries[idx]
            if turn_range:
                if entry.turn < turn_range[0] or entry.turn > turn_range[1]:
                    continue
            results.append(entry)
            if len(results) >= limit:
                break

        # Format output
        output = []
        for entry in results:
            content = entry.content
            if max_chars and len(content) > max_chars:
                content = content[:max_chars] + f"\n... [truncated, full size: {entry.size_bytes} bytes]"
            output.append({
                "key": entry.key,
                "content": content,
                "turn": entry.turn,
                "agent_role": entry.agent_role,
                "agent_name": entry.agent_name,
                "tags": entry.tags,
                "content_type": entry.content_type,
                "size_bytes": entry.size_bytes,
                "timestamp": entry.timestamp,
            })

        return output

    def list_keys(self, prefix: str = "") -> list[dict[str, Any]]:
        """List all keys in the vault (metadata only, no content)."""
        results = []
        for entry in self._entries:
            if prefix and not entry.key.startswith(prefix):
                continue
            results.append({
                "key": entry.key,
                "turn": entry.turn,
                "agent_role": entry.agent_role,
                "tags": entry.tags,
                "content_type": entry.content_type,
                "size_bytes": entry.size_bytes,
            })
        return results

    def build_recent_digest(self, n: int = 3, max_chars: int = 2000) -> str:
        """Auto-injectable digest of the N most recent vault records (W-7/C-9).

        The vault holds full-fidelity memory (verdicts, exact numbers, error
        locations) but was pull-only — a weak model never called retrieve, so the
        memory slept. This lets the context builder push the freshest high-value
        records into the agent prompt. Newest first; budget-bounded; never raises.
        """
        if not self._entries or n <= 0:
            return ""
        try:
            recent = sorted(self._entries, key=lambda e: e.turn, reverse=True)[:n]
            recent = [e for e in recent if e.content]  # skip evicted
            if not recent:
                return ""
            parts = ["## SAVED FACTS (from the vault — authoritative, do NOT recompute)"]
            per = max(200, (max_chars - len(parts[0])) // max(1, len(recent)))
            for e in recent:
                body = e.content if len(e.content) <= per else (e.content[:per] + "…")
                parts.append(f"- [T{e.turn} {e.agent_role}] {e.key}: {body}")
            return "\n".join(parts)[:max_chars]
        except Exception:
            return ""

    def get_stats(self) -> dict[str, Any]:
        """Get vault statistics."""
        return {
            "total_entries": len(self._entries),
            "total_bytes": self._total_bytes,
            "max_bytes": _MAX_VAULT_BYTES,
            "usage_pct": round(self._total_bytes / _MAX_VAULT_BYTES * 100, 1),
            "unique_keys": len(self._index_by_key),
            "roles": list(self._index_by_role.keys()),
            "tags": list(self._index_by_tag.keys()),
        }

    def _evict_lru(self, needed_bytes: int) -> int:
        """Evict oldest entries until we have space. Returns count evicted."""
        evicted = 0
        for i, entry in enumerate(self._entries):
            if self._total_bytes + needed_bytes <= _MAX_VAULT_BYTES:
                break
            if entry.content:  # Not already evicted
                self._total_bytes -= entry.size_bytes
                self._entries[i] = VaultEntry(
                    key=entry.key, content="[EVICTED]", turn=entry.turn,
                    agent_role=entry.agent_role, agent_name=entry.agent_name,
                    tags=entry.tags, size_bytes=0,
                )
                evicted += 1
        return evicted


# Tool schemas for agent use
DATA_VAULT_STORE_SCHEMA = {
    "name": "data_vault_store",
    "description": (
        "Store important data/findings in the persistent Data Vault for cross-turn access. "
        "Use this when you find critical information (URLs, code snippets, API responses, "
        "structured data) that other agents or future turns will need. Unlike your regular "
        "output (which gets summarized), vault data is preserved IN FULL across all turns."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "key": {
                "type": "string",
                "description": "Unique key for this artifact (e.g. 'target_urls', 'api_endpoints', 'credentials_found')",
            },
            "content": {
                "type": "string",
                "description": "The full data to store (URLs, code, JSON, findings). Will be stored without truncation.",
            },
            "tags": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Tags for categorization (e.g. ['recon', 'urls'], ['code', 'python'])",
            },
            "content_type": {
                "type": "string",
                "enum": ["text", "json", "code", "url_list"],
                "description": "Type of content being stored",
            },
        },
        "required": ["key", "content"],
    },
}

DATA_VAULT_RETRIEVE_SCHEMA = {
    "name": "data_vault_retrieve",
    "description": (
        "Retrieve data from the persistent Data Vault. Returns FULL content without "
        "truncation. Use when you need specific data from earlier turns — "
        "search by key, tags, agent role, or turn range."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "key": {
                "type": "string",
                "description": "Exact key to retrieve (fastest lookup)",
            },
            "tags": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Retrieve entries matching any of these tags",
            },
            "agent_role": {
                "type": "string",
                "description": "Filter by agent role that stored the data",
            },
            "turn_min": {
                "type": "integer",
                "description": "Minimum turn number (inclusive)",
            },
            "turn_max": {
                "type": "integer",
                "description": "Maximum turn number (inclusive)",
            },
            "list_keys_only": {
                "type": "boolean",
                "description": "If true, return only key metadata without content (for browsing)",
            },
        },
    },
}
