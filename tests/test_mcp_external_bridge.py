"""Tests for bridging external MCP tools into the registry (audit #1).

Uses fake manager/registry doubles — no network, no real MCP server. Verifies
namespacing, schema conversion, handler routing to the right server, and the
late-binding-closure trap (each handler must call ITS OWN server/tool).
"""

from __future__ import annotations

import pytest

from app.mcp.external_bridge import (
    MCP_TOOLSET,
    mcp_tool_name,
    register_mcp_tools,
    split_mcp_tool_name,
)


# ───────────────────────── naming round-trip ─────────────────────────


def test_name_round_trip():
    name = mcp_tool_name("github", "create_issue")
    assert name == "mcp__github__create_issue"
    assert split_mcp_tool_name(name) == ("github", "create_issue")


def test_split_preserves_tool_with_double_underscore():
    # Tool name itself contains '__' — only the first split separates server.
    name = mcp_tool_name("srv", "weird__tool")
    assert split_mcp_tool_name(name) == ("srv", "weird__tool")


def test_split_rejects_foreign_names():
    assert split_mcp_tool_name("read_file") is None
    assert split_mcp_tool_name("mcp__onlyserver") is None
    assert split_mcp_tool_name("mcp__") is None


# ───────────────────────── registration ─────────────────────────


class _FakeRegistry:
    def __init__(self):
        self.registered: dict[str, dict] = {}

    def register(self, name, toolset, schema=None, handler=None, is_async=False,
                 description="", emoji="", category="", **kw):
        self.registered[name] = {
            "toolset": toolset, "schema": schema, "handler": handler,
            "is_async": is_async, "description": description, "category": category,
        }


class _FakeManager:
    def __init__(self, tools):
        self._tools = tools
        self.calls = []

    def get_all_tools(self):
        return self._tools

    async def call_tool(self, server, tool, args):
        self.calls.append((server, tool, args))
        return f"result:{server}:{tool}:{args}"


def _sample_tools():
    return [
        {
            "server": "github", "name": "create_issue",
            "description": "Create a GitHub issue",
            "inputSchema": {"properties": {"title": {"type": "string"}}, "required": ["title"]},
        },
        {
            "server": "fs", "name": "read",
            "description": "Read a file",
            "inputSchema": {"properties": {"path": {"type": "string"}}},
        },
    ]


def test_register_counts_and_names():
    reg = _FakeRegistry()
    mgr = _FakeManager(_sample_tools())
    n = register_mcp_tools(mgr, reg)
    assert n == 2
    assert "mcp__github__create_issue" in reg.registered
    assert "mcp__fs__read" in reg.registered
    assert reg.registered["mcp__fs__read"]["toolset"] == MCP_TOOLSET
    assert reg.registered["mcp__fs__read"]["is_async"] is True


def test_schema_converted_from_input_schema():
    reg = _FakeRegistry()
    register_mcp_tools(_FakeManager(_sample_tools()), reg)
    schema = reg.registered["mcp__github__create_issue"]["schema"]
    assert schema["type"] == "object"
    assert "title" in schema["properties"]
    assert schema["required"] == ["title"]


def test_missing_input_schema_defaults_to_empty_object():
    reg = _FakeRegistry()
    register_mcp_tools(_FakeManager([{"server": "s", "name": "t"}]), reg)
    schema = reg.registered["mcp__s__t"]["schema"]
    assert schema == {"type": "object", "properties": {}, "required": []}


def test_bridge_invalidates_schema_cache(monkeypatch):
    # Bridging must invalidate the LLM-facing schema cache so re-bridged tools
    # (same count) still refresh — parity with self_evolve scaffolding.
    calls = {"n": 0}
    import app.mcp.tool_executor as te
    monkeypatch.setattr(te.ToolExecutor, "invalidate_schema_cache",
                        staticmethod(lambda: calls.__setitem__("n", calls["n"] + 1)))
    register_mcp_tools(_FakeManager(_sample_tools()), _FakeRegistry())
    assert calls["n"] == 1


def test_bridge_no_tools_skips_invalidate(monkeypatch):
    calls = {"n": 0}
    import app.mcp.tool_executor as te
    monkeypatch.setattr(te.ToolExecutor, "invalidate_schema_cache",
                        staticmethod(lambda: calls.__setitem__("n", calls["n"] + 1)))
    register_mcp_tools(_FakeManager([]), _FakeRegistry())  # nothing bridged
    assert calls["n"] == 0  # no invalidate when count == 0


def test_skips_tools_missing_server_or_name():
    reg = _FakeRegistry()
    bad = [{"server": "", "name": "x"}, {"server": "s", "name": ""}, {"name": "y"}]
    assert register_mcp_tools(_FakeManager(bad), reg) == 0
    assert reg.registered == {}


# ───────────────────────── handler routing (closure trap) ─────────────────────────


@pytest.mark.asyncio
async def test_each_handler_routes_to_its_own_server():
    reg = _FakeRegistry()
    mgr = _FakeManager(_sample_tools())
    register_mcp_tools(mgr, reg)

    gh_handler = reg.registered["mcp__github__create_issue"]["handler"]
    fs_handler = reg.registered["mcp__fs__read"]["handler"]

    # Call handlers EXACTLY as ToolRegistry.dispatch does: args as the first
    # POSITIONAL dict. This is what caught the original signature bug — a kwargs
    # call would have hidden it.
    await gh_handler({"title": "Bug"})
    await fs_handler({"path": "/etc/hosts"})

    # Each handler must have called ITS OWN server/tool — not the last one
    # registered (the classic late-binding closure bug).
    assert ("github", "create_issue", {"title": "Bug"}) in mgr.calls
    assert ("fs", "read", {"path": "/etc/hosts"}) in mgr.calls


@pytest.mark.asyncio
async def test_handler_matches_registry_dispatch_calling_convention():
    """Guard: handler must accept the positional args-dict that dispatch passes.

    ToolRegistry.dispatch does `await entry.handler(args, **kwargs)`. If the bridge
    handler ever regresses to kwargs-only, this fails loudly.
    """
    reg = _FakeRegistry()
    mgr = _FakeManager([{"server": "s", "name": "t",
                         "inputSchema": {"properties": {"x": {"type": "string"}}}}])
    register_mcp_tools(mgr, reg)
    handler = reg.registered["mcp__s__t"]["handler"]

    out = await handler({"x": "1"})  # positional dict, mirrors dispatch
    assert out == "result:s:t:{'x': '1'}"
    assert mgr.calls == [("s", "t", {"x": "1"})]
