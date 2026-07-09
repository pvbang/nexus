"""P0-D (CC-11): re-gate the ACTUAL target of an offensive-network tool at runtime.

Audit 2026-06-22: the authorization gate ran ONCE pre-flight on the user's
message. When the user gave no URL ("đây là website của tôi"), the message
classified as NONE → PASS, and the agent then FABRICATED a target
(`hanoi.edu.vn`) and ran live probes against it (`e5029e0d`); similarly
`eb5c09bd` ran live recon on third-party infra after the pre-flight passed.

Fix: at tool-execution time, an offensive-network tool (port_scan / tcp_connect
/ network_probe / ssh_exec) re-classifies the host it is about to hit. If that
host is REAL public-routable infrastructure and the operator has not set
ALLOW_LIVE_OFFENSIVE, the call is HARD-STOPPED (fail-closed) regardless of what
the pre-flight gate saw. Benign/safe targets and non-offensive tools (web
research, DNS, lab/loopback) are unaffected.
"""

from __future__ import annotations

import pytest

from app.security import authorization_gate as ag


# --- pure target-authorization decision ------------------------------------

def test_offensive_tool_targets_real_public_infra_blocked():
    d = ag.evaluate_target_authorization("hanoi.edu.vn", authorized=False)
    assert d.blocked is True
    assert d.target_class is ag.TargetClass.REAL_PUBLIC_INFRA


def test_offensive_tool_real_public_infra_allowed_with_optin():
    d = ag.evaluate_target_authorization("hanoi.edu.vn", authorized=True)
    assert d.blocked is False


@pytest.mark.parametrize("host", ["127.0.0.1", "10.0.0.5", "192.168.1.1",
                                  "box.htb", "localhost", "target.lab"])
def test_offensive_tool_safe_target_allowed(host):
    d = ag.evaluate_target_authorization(host, authorized=False)
    assert d.blocked is False


def test_is_offensive_network_tool_set():
    for t in ("port_scan", "tcp_connect", "network_probe", "ssh_exec"):
        assert ag.is_offensive_network_tool(t) is True
    # passive / ubiquitous tools must NOT be in the offensive set (no regression)
    for t in ("dns_lookup", "http_request", "web_search", "read_file"):
        assert ag.is_offensive_network_tool(t) is False


# --- executor hard-stop integration ----------------------------------------

@pytest.mark.asyncio
async def test_executor_hardstops_probe_on_public_target(monkeypatch):
    from app.mcp.tool_executor import ToolExecutor

    monkeypatch.delenv("ALLOW_LIVE_OFFENSIVE", raising=False)
    # (Scope is isolated to an empty file by the autouse _isolate_pentest_scope
    # fixture, so this exercises the "no authorization at all → block" path.)

    # If the guard fails to stop, the real handler would run; make that loud.
    called = {"hit": False}

    async def _boom_handler(params):
        called["hit"] = True
        return {"success": True, "output": "scanned"}

    # Force the registry to return our spy handler for port_scan.
    import app.mcp.tool_registry as treg

    class _Entry:
        is_async = True
        schema = {}
        handler = staticmethod(_boom_handler)

    monkeypatch.setattr(treg.registry, "get_entry", lambda name: _Entry())

    ex = ToolExecutor()
    result = await ex.execute("port_scan", {"host": "hanoi.edu.vn"})

    assert result.get("blocked") is True
    assert called["hit"] is False
    assert "authorization" in (result.get("blocked_reason", "") + result.get("output", "")).lower()


@pytest.mark.asyncio
async def test_executor_allows_probe_on_safe_target(monkeypatch):
    from app.mcp.tool_executor import ToolExecutor

    monkeypatch.delenv("ALLOW_LIVE_OFFENSIVE", raising=False)
    ran = {"hit": False}

    async def _handler(params):
        ran["hit"] = True
        return {"success": True, "output": "scanned localhost"}

    import app.mcp.tool_registry as treg

    class _Entry:
        is_async = True
        schema = {}
        handler = staticmethod(_handler)

    monkeypatch.setattr(treg.registry, "get_entry", lambda name: _Entry())

    ex = ToolExecutor()
    result = await ex.execute("port_scan", {"host": "127.0.0.1"})
    assert ran["hit"] is True
    assert not result.get("blocked")
