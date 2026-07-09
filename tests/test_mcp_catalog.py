"""Tests for the MCP server discovery catalog (audit #1, phase 2).

Pure matching logic — no network. Verifies need→server ranking, determinism,
config shape, and lookup.
"""

from __future__ import annotations

from app.mcp.mcp_catalog import (
    CatalogEntry,
    find_servers_for_need,
    get_catalog_entry,
)


def test_finds_filesystem_for_file_need():
    matches = find_servers_for_need("I need to read and write files on disk")
    assert matches, "should find at least one server"
    assert matches[0].entry.name == "filesystem"


def test_finds_github_for_repo_need():
    matches = find_servers_for_need("create a pull request and comment on the issue in the repo")
    assert matches[0].entry.name == "github"


def test_finds_postgres_for_db_need():
    matches = find_servers_for_need("run a SQL query against the postgres database")
    assert matches[0].entry.name == "postgres"


def test_no_match_returns_empty():
    # A need with no catalog keywords → empty (caller then falls back / asks user).
    assert find_servers_for_need("xyzzy plugh frobnicate") == []


def test_empty_need_returns_empty():
    assert find_servers_for_need("") == []
    assert find_servers_for_need("   ") == []


def test_score_reflects_keyword_overlap():
    # "file" + "directory" + "write" → filesystem should score >= 3.
    matches = find_servers_for_need("write a file into the directory")
    fs = next(m for m in matches if m.entry.name == "filesystem")
    assert fs.score >= 3


def test_result_is_limited_and_sorted():
    matches = find_servers_for_need("file web github sql slack memory fetch", limit=2)
    assert len(matches) == 2
    # Sorted by score descending.
    assert matches[0].score >= matches[1].score


def test_deterministic_tie_break_by_catalog_order():
    # Two runs of the same query must return identical ordering.
    q = "web page screenshot fetch url"
    a = [m.entry.name for m in find_servers_for_need(q)]
    b = [m.entry.name for m in find_servers_for_need(q)]
    assert a == b


def test_matching_is_whole_token_not_substring():
    # "profile" contains "file" as a substring but must NOT match filesystem,
    # because matching is on whole tokens.
    matches = find_servers_for_need("update my user profile settings")
    assert all(m.entry.name != "filesystem" for m in matches)


def test_to_server_config_shape():
    entry = CatalogEntry(
        name="x", package="@scope/server-x", description="d", keywords=("k",),
    )
    cfg = entry.to_server_config()
    assert cfg["name"] == "x"
    assert cfg["command"] == "npx"
    assert cfg["args"] == ["-y", "@scope/server-x"]
    assert cfg["enabled"] is True


def test_get_catalog_entry():
    assert get_catalog_entry("github").package == "@modelcontextprotocol/server-github"
    assert get_catalog_entry("nonexistent") is None
