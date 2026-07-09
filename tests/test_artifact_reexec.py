"""P2-A (CC-1, re-execute half): re-run runnable artifacts and compare numbers.

Audit 2026-06-22 (`350d9a50`): the gate never re-ran a deliverable. A report
could claim "488 trades" while the script that allegedly produced it outputs 58.
This module re-executes runnable artifacts (.py/.sh) and compares the numbers the
agent CLAIMED against the numbers the script actually PRINTS.

Fail-closed on a real mismatch (claimed number absent from output) but fail-OPEN
honestly when re-execution is impossible (no sandbox/interpreter) — status
"unverified", which is NOT counted as a blocker and never auto-passes.
"""

from __future__ import annotations

import pytest

from app.orchestration import artifact_reexecutor as rx


class _FakeSandbox:
    """Stub SandboxManager.execute_in_project returning a canned SandboxResult."""

    def __init__(self, success=True, output="", error="", exit_code=0):
        from app.sandbox.manager import SandboxResult
        self._result = SandboxResult(success=success, output=output, error=error,
                                     exit_code=exit_code)
        self.calls = []

    async def execute_in_project(self, project_path, command, timeout=60, env=None):
        self.calls.append(command)
        return self._result


@pytest.mark.asyncio
async def test_mismatch_detected(workspace_dir, tmp_path):
    work = tmp_path / "_work"
    work.mkdir()
    (work / "calc.py").write_text("print('trades:', 58)", encoding="utf-8")
    sandbox = _FakeSandbox(success=True, output="trades: 58")

    # Agent CLAIMED 488 trades — script prints 58 → mismatch.
    scan = await rx.reexecute_runnable_artifacts(
        work_dir=work,
        claimed_outputs=["walk-forward produced 488 trades in the OOS window"],
        sandbox=sandbox,
    )
    assert rx.count_mismatches(scan) >= 1
    assert any(r.status == "mismatch" for r in scan.results)


@pytest.mark.asyncio
async def test_mismatch_when_some_claimed_numbers_coincide(workspace_dir, tmp_path):
    # Realistic case (350d9a50): the agent claims MANY numbers; the script's real
    # output happens to contain a couple of them (win rate, year) but NOT the
    # headline lie (488 trades). The old logic only flagged when ALL claimed were
    # absent → the 488 lie slipped through. A single absent headline must mismatch.
    work = tmp_path / "_work"
    work.mkdir()
    (work / "calc.py").write_text("print('Trades: 58. Win rate: 55. Year 2026.')", encoding="utf-8")
    sandbox = _FakeSandbox(success=True, output="Trades: 58. Win rate: 55. Year 2026.")

    scan = await rx.reexecute_runnable_artifacts(
        work_dir=work,
        claimed_outputs=["488 trades executed in 2026, win rate 55, sharpe 1.5"],
        sandbox=sandbox,
    )
    assert rx.count_mismatches(scan) >= 1, "a claimed headline number absent from output must mismatch"


@pytest.mark.asyncio
async def test_year_like_numbers_do_not_trigger_mismatch(workspace_dir, tmp_path):
    # A 4-digit year present in the claim but absent from output must NOT, on its
    # own, be treated as a contradicted headline (years are not results).
    work = tmp_path / "_work"
    work.mkdir()
    (work / "calc.py").write_text("print('result 14.3')", encoding="utf-8")
    sandbox = _FakeSandbox(success=True, output="result 14.3")
    scan = await rx.reexecute_runnable_artifacts(
        work_dir=work,
        claimed_outputs=["computed 14.3 kWe (run dated 2026)"],
        sandbox=sandbox,
    )
    assert rx.count_mismatches(scan) == 0


@pytest.mark.asyncio
async def test_match_passes(workspace_dir, tmp_path):
    work = tmp_path / "_work"
    work.mkdir()
    (work / "calc.py").write_text("print('result', 14.3)", encoding="utf-8")
    sandbox = _FakeSandbox(success=True, output="result 14.3")

    scan = await rx.reexecute_runnable_artifacts(
        work_dir=work,
        claimed_outputs=["the script computes 14.3 kWe"],
        sandbox=sandbox,
    )
    assert rx.count_mismatches(scan) == 0
    assert all(r.status != "mismatch" for r in scan.results)


@pytest.mark.asyncio
async def test_no_sandbox_is_unverified_not_blocker(workspace_dir, tmp_path):
    work = tmp_path / "_work"
    work.mkdir()
    (work / "calc.py").write_text("print(42)", encoding="utf-8")
    # Sandbox can't run (e.g. interpreter/daemon missing).
    sandbox = _FakeSandbox(success=False, exit_code=-1,
                           error="python3: command not found")

    scan = await rx.reexecute_runnable_artifacts(
        work_dir=work,
        claimed_outputs=["the answer is 99"],
        sandbox=sandbox,
    )
    assert rx.count_mismatches(scan) == 0  # unverified is never a blocker
    assert any(r.status == "unverified" for r in scan.results)
    ctx = rx.build_reexec_context(scan)
    assert "UNVERIFIED" in ctx.upper()


@pytest.mark.asyncio
async def test_no_runnable_artifacts_empty_scan(workspace_dir, tmp_path):
    work = tmp_path / "_work"
    work.mkdir()
    (work / "notes.md").write_text("just prose, no script", encoding="utf-8")
    sandbox = _FakeSandbox()
    scan = await rx.reexecute_runnable_artifacts(
        work_dir=work, claimed_outputs=["something 5"], sandbox=sandbox,
    )
    assert scan.results == []
    assert rx.count_mismatches(scan) == 0


@pytest.mark.asyncio
async def test_definition_only_script_skipped(workspace_dir, tmp_path):
    # A module that only defines functions (no top-level call/print) has nothing
    # to compare — skip it rather than running it for no output.
    work = tmp_path / "_work"
    work.mkdir()
    (work / "lib.py").write_text("def f(x):\n    return x + 1\n", encoding="utf-8")
    sandbox = _FakeSandbox(success=True, output="")
    scan = await rx.reexecute_runnable_artifacts(
        work_dir=work, claimed_outputs=["uses 7"], sandbox=sandbox,
    )
    assert all(r.status != "mismatch" for r in scan.results)
