"""Tests for MCP capability self-acquisition (audit #1, phase 2).

Drives the connect/bridge loop with fake manager/registry doubles — no network.
Covers every outcome branch: no_match, needs_env, proposed, connected, error.
"""

from __future__ import annotations

import pytest

from app.mcp.acquire import acquire_capability, describe_catalog


class _FakeManager:
    def __init__(self, connect_ok=True, tools=None, clients=None):
        self._connect_ok = connect_ok
        self._tools = tools or []
        self.connected = []
        self.clients = clients or {}

    async def connect_server(self, config):
        self.connected.append(config.name)
        self.clients[config.name] = object()
        return self._connect_ok

    def get_all_tools(self):
        return self._tools


class _FakeRegistry:
    def __init__(self):
        self.registered = {}

    def register(self, name, toolset, **kw):
        self.registered[name] = kw


@pytest.mark.asyncio
async def test_no_match_returns_no_match():
    res = await acquire_capability("xyzzy frobnicate plugh", _FakeManager(), _FakeRegistry())
    assert res["status"] == "no_match"


@pytest.mark.asyncio
async def test_needs_env_when_required_creds_absent(monkeypatch):
    # github catalog entry requires GITHUB_TOKEN — ensure it's unset.
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    res = await acquire_capability(
        "open a pull request in the github repo", _FakeManager(), _FakeRegistry()
    )
    assert res["status"] == "needs_env"
    assert res["server"] == "github"
    assert "GITHUB_TOKEN" in res["missing"]


@pytest.mark.asyncio
async def test_proposed_when_auto_connect_off():
    res = await acquire_capability(
        "read and write files on disk", _FakeManager(), _FakeRegistry(),
        auto_connect=False,
    )
    assert res["status"] == "proposed"
    assert res["server"] == "filesystem"
    assert "package" in res


@pytest.mark.asyncio
async def test_connected_bridges_tools():
    mgr = _FakeManager(
        connect_ok=True,
        tools=[{"server": "filesystem", "name": "read_file",
                "inputSchema": {"properties": {"path": {"type": "string"}}}}],
    )
    reg = _FakeRegistry()
    res = await acquire_capability("read and write files on disk", mgr, reg)
    assert res["status"] == "connected"
    assert res["server"] == "filesystem"
    assert res["tools"] == 1
    assert "filesystem" in mgr.connected
    assert "mcp__filesystem__read_file" in reg.registered


@pytest.mark.asyncio
async def test_idempotent_skips_reconnect_when_already_connected():
    # Server already in manager.clients → must NOT call connect_server again
    # (would spawn a duplicate subprocess), just re-bridge.
    mgr = _FakeManager(
        connect_ok=True,
        tools=[{"server": "filesystem", "name": "read_file"}],
        clients={"filesystem": object()},
    )
    res = await acquire_capability("read and write files on disk", mgr, _FakeRegistry())
    assert res["status"] == "connected"
    assert res["reused"] is True
    assert mgr.connected == []  # no reconnect


@pytest.mark.asyncio
async def test_error_when_connect_fails():
    mgr = _FakeManager(connect_ok=False)
    res = await acquire_capability("read and write files on disk", mgr, _FakeRegistry())
    assert res["status"] == "error"
    assert res["error"] == "connect_failed"


def test_describe_catalog_lists_servers():
    text = describe_catalog()
    assert "filesystem" in text
    assert "github" in text
    # Env requirement surfaced for the operator.
    assert "GITHUB_TOKEN" in text
