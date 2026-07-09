"""Catalog of known MCP servers for capability discovery (audit #1, phase 2).

Phase 1 wired MCP servers that the operator explicitly listed in ``MCP_SERVERS``.
Phase 2 lets the system *find* a server when an agent needs a capability that no
currently-registered tool provides.

Design choice — a curated static catalog, NOT a live npm/registry search:
  - Auto-running ``npx <arbitrary-package>`` from an online search is a real
    supply-chain risk; a vetted catalog of the official ``@modelcontextprotocol``
    servers (plus operator-supplied entries) keeps "self-discovery" safe.
  - The catalog is plain data, so the matching logic is pure and unit-testable
    with no network. Connecting/spawning (the I/O half) stays in the bridge.

``find_servers_for_need`` ranks catalog entries against a free-text capability
need (e.g. "read and write files on disk", "query postgres") by keyword overlap,
so the orchestrator can propose or auto-connect the best-fit server.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass(frozen=True)
class CatalogEntry:
    """One known MCP server and the capabilities it advertises."""

    name: str
    package: str  # npm package (stdio via npx) or descriptor
    description: str
    keywords: tuple[str, ...]  # capability terms used for matching
    args: tuple[str, ...] = field(default_factory=tuple)
    requires_env: tuple[str, ...] = field(default_factory=tuple)

    def to_server_config(self) -> dict:
        """Build the dict shape MCPServerConfig / parse_mcp_server_configs expects."""
        return {
            "name": self.name,
            "command": "npx",
            "args": ["-y", self.package, *self.args],
            "enabled": True,
        }


# Curated catalog of official / well-known MCP servers. Keywords are the
# capability vocabulary an agent's need is matched against.
KNOWN_MCP_SERVERS: tuple[CatalogEntry, ...] = (
    CatalogEntry(
        name="filesystem",
        package="@modelcontextprotocol/server-filesystem",
        description="Read, write, and list files on the local filesystem.",
        keywords=("file", "files", "filesystem", "read", "write", "directory", "disk", "folder"),
    ),
    CatalogEntry(
        name="github",
        package="@modelcontextprotocol/server-github",
        description="Interact with GitHub: repos, issues, PRs, code search.",
        keywords=("github", "repo", "repository", "issue", "pull", "pr", "commit", "git"),
        requires_env=("GITHUB_TOKEN",),
    ),
    CatalogEntry(
        name="postgres",
        package="@modelcontextprotocol/server-postgres",
        description="Query and inspect PostgreSQL databases.",
        keywords=("postgres", "postgresql", "database", "sql", "query", "db", "table"),
    ),
    CatalogEntry(
        name="puppeteer",
        package="@modelcontextprotocol/server-puppeteer",
        description="Browser automation: navigate, click, screenshot web pages.",
        keywords=("browser", "web", "puppeteer", "screenshot", "navigate", "scrape", "page"),
    ),
    CatalogEntry(
        name="slack",
        package="@modelcontextprotocol/server-slack",
        description="Send and read Slack messages and channels.",
        keywords=("slack", "message", "channel", "chat", "notify"),
        requires_env=("SLACK_BOT_TOKEN",),
    ),
    CatalogEntry(
        name="memory",
        package="@modelcontextprotocol/server-memory",
        description="Persistent knowledge-graph memory across sessions.",
        keywords=("memory", "remember", "knowledge", "graph", "recall", "persist"),
    ),
    CatalogEntry(
        name="fetch",
        package="@modelcontextprotocol/server-fetch",
        description="Fetch and convert web page content to markdown.",
        keywords=("fetch", "http", "url", "web", "download", "request", "page"),
    ),
)


_WORD_RE = re.compile(r"[a-z0-9]+")


def _tokenize(text: str) -> set[str]:
    return set(_WORD_RE.findall((text or "").lower()))


@dataclass(frozen=True)
class CatalogMatch:
    entry: CatalogEntry
    score: int  # number of distinct keyword hits


def find_servers_for_need(
    need: str,
    catalog: tuple[CatalogEntry, ...] = KNOWN_MCP_SERVERS,
    limit: int = 3,
) -> list[CatalogMatch]:
    """Rank catalog entries by how well they match a free-text capability need.

    Scoring = count of the entry's keywords that appear as whole tokens in the
    need text. Entries with zero hits are excluded. Ties break by catalog order
    (stable), so the result is deterministic — important for reproducible runs.
    """
    tokens = _tokenize(need)
    if not tokens:
        return []

    scored: list[CatalogMatch] = []
    for entry in catalog:
        score = sum(1 for kw in entry.keywords if kw in tokens)
        if score > 0:
            scored.append(CatalogMatch(entry=entry, score=score))

    # Sort by score desc; Python's sort is stable so equal scores keep catalog order.
    scored.sort(key=lambda m: m.score, reverse=True)
    return scored[:limit]


def get_catalog_entry(
    name: str,
    catalog: tuple[CatalogEntry, ...] = KNOWN_MCP_SERVERS,
) -> CatalogEntry | None:
    """Look up a catalog entry by its server name."""
    for entry in catalog:
        if entry.name == name:
            return entry
    return None
