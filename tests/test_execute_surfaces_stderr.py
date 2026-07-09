"""execute_command / execute_code must surface stderr when a command crashes.

Audit d6c33621: a script printed a couple of header lines to STDOUT and then
crashed with a TypeError traceback on STDERR. Both handlers returned
``result.output or result.error`` — so the non-empty stdout header WON and the
traceback was DISCARDED. The agent saw only "header... EXITCODE:1" with no
traceback, mis-diagnosed the crash location for 6 turns (blamed a function on a
later line than the one that actually threw), and never fixed it.

Fix: on failure, combine stdout AND stderr so the agent sees the actual
traceback. On success, behaviour is unchanged (stdout as before).
"""

from __future__ import annotations

import importlib

import pytest


@pytest.fixture
def term(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKSPACE_DIR", str(tmp_path))
    import app.core.task_paths as tp
    importlib.reload(tp)
    import app.mcp.tools.filesystem as fs
    importlib.reload(fs)
    import app.mcp.tools.terminal as terminal
    importlib.reload(terminal)
    return terminal


class _FakeResult:
    """Mirror of app.sandbox.manager.SandboxResult (stdout/stderr captured apart)."""

    def __init__(self, output, error, exit_code):
        self.success = exit_code == 0
        self.output = output
        self.error = error
        self.exit_code = exit_code
        self.execution_time = 0.0


class _FakeSandbox:
    def __init__(self, result):
        self._result = result

    async def execute_code(self, language, code, timeout=30):
        return self._result


# stdout header + a crash traceback on stderr — the exact d6c33621 shape.
_STDOUT = "=" * 40 + "\nLINK BUDGET — f=9.5 GHz\nPt=2.0 W\n"
_STDERR = (
    "Traceback (most recent call last):\n"
    '  File "main.py", line 66, in <module>\n'
    "    print(f\"B_tx={B_tx/1e6:.0f}\")\n"
    "TypeError: unsupported operand type(s) for /: 'str' and 'float'\n"
)


@pytest.mark.asyncio
async def test_execute_command_surfaces_traceback_on_failure(term, monkeypatch):
    monkeypatch.setattr(term, "_get_sandbox", lambda: _FakeSandbox(
        _FakeResult(_STDOUT, _STDERR, exit_code=1)))
    res = await term.handle_execute_command({"command": "python main.py"})
    assert res["success"] is False
    # The traceback MUST be visible — this is what the agent needs to debug.
    assert "TypeError" in res["output"]
    assert "line 66" in res["output"]
    # The stdout header should still be there too (full picture).
    assert "LINK BUDGET" in res["output"]


@pytest.mark.asyncio
async def test_execute_code_surfaces_traceback_on_failure(term, monkeypatch):
    monkeypatch.setattr(term, "_get_sandbox", lambda: _FakeSandbox(
        _FakeResult(_STDOUT, _STDERR, exit_code=1)))
    res = await term.handle_execute_code({"language": "python", "code": "x=1"})
    assert res["success"] is False
    assert "TypeError" in res["output"]
    assert "line 66" in res["output"]


@pytest.mark.asyncio
async def test_execute_success_output_unchanged(term, monkeypatch):
    # On success, stdout is returned as before (no stderr noise appended).
    monkeypatch.setattr(term, "_get_sandbox", lambda: _FakeSandbox(
        _FakeResult("RESULT OK\n", "", exit_code=0)))
    res = await term.handle_execute_command({"command": "echo ok"})
    assert res["success"] is True
    assert res["output"].strip() == "RESULT OK"


@pytest.mark.asyncio
async def test_execute_failure_with_only_stderr(term, monkeypatch):
    # Crash with NO stdout at all → stderr still surfaces (no regression).
    monkeypatch.setattr(term, "_get_sandbox", lambda: _FakeSandbox(
        _FakeResult("", _STDERR, exit_code=1)))
    res = await term.handle_execute_command({"command": "python x.py"})
    assert res["success"] is False
    assert "TypeError" in res["output"]
