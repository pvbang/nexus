"""Tests for WorkspaceReader._read_latest_progress marker parsing.

Regression suite for task 3c44a93f bug #2: the reader used
``content.rfind("## Turn ")`` (substring, not line-anchored) to find the
"latest progress" block. Because the Execution Plan in TODO.md uses
``### Turn N: PHASE N`` (3 hashes — a FUTURE plan heading) and the substring
``## Turn `` is contained inside ``### Turn ``, the reader grabbed the plan's
"PHASE 6 — Cleanup" line and mislabelled it "Latest progress". That false
state was injected into the Evolution Agent every turn and made agents believe
the harness had jumped to Phase 6 while really stuck at Phase 1.

The writer (nexus_orchestrator.update_todo) actually emits progress as
``## Current Status — Turn N | Progress: X%`` (rewrite model), so the reader
must look for THAT marker (line-anchored), with the legacy append-log
``## Turn N — ...`` as a line-anchored fallback. A plan-only TODO (no progress
written yet) must yield an EMPTY result — never a plan heading.
"""

from __future__ import annotations

from pathlib import Path

from app.orchestration.workspace_reader import WorkspaceReader


def _reader() -> WorkspaceReader:
    # _read_latest_progress takes the todo path directly and never touches
    # task_paths, so a reader bound to a dummy id is fine for these unit tests.
    return WorkspaceReader.__new__(WorkspaceReader)


# Mirrors the real plan block written by prompts.build_todo for a 6-phase task.
_PLAN_ONLY_TODO = """# Task TODO

## Invariants (MUST maintain throughout all turns)

- Human-in-the-loop bắt buộc: dừng sau MỖI phase

## Execution Plan

### Turn 1: PHASE 1 — Reconnaissance
- **Status**: ⏳ Pending

### Turn 6: PHASE 6 — Cleanup, Actions on Objectives + Reporting + Remediation
- **Status**: ⏳ Pending
"""


def test_plan_only_todo_yields_empty_not_plan_heading(tmp_path: Path) -> None:
    """A TODO with only the Execution Plan (no progress yet) → empty result.

    This is the exact turn-1 state of task 3c44a93f. The buggy fallback
    returned the ``### Turn 6: PHASE 6`` plan heading here.
    """
    todo = tmp_path / "TODO.md"
    todo.write_text(_PLAN_ONLY_TODO, encoding="utf-8")

    result = _reader()._read_latest_progress(todo)

    assert "PHASE 6" not in result
    assert result == ""


def test_rewrite_current_status_marker_is_returned(tmp_path: Path) -> None:
    """The real writer emits ``## Current Status — Turn N`` — reader must find it."""
    todo = tmp_path / "TODO.md"
    todo.write_text(
        _PLAN_ONLY_TODO
        + "\n## Current Status — Turn 12 | Progress: 100%\n\n"
        "### Completed\n- [x] Phase 1\n",
        encoding="utf-8",
    )

    result = _reader()._read_latest_progress(todo)

    assert "Current Status — Turn 12" in result
    assert "PHASE 6" not in result


def test_legacy_append_log_marker_still_supported(tmp_path: Path) -> None:
    """Backward compat: an old-style ``## Turn N — Progress`` block is returned."""
    todo = tmp_path / "TODO.md"
    todo.write_text(
        _PLAN_ONLY_TODO + "\n## Turn 3 — 🔄 Progress: 40%\n- ✅ recon done\n",
        encoding="utf-8",
    )

    result = _reader()._read_latest_progress(todo)

    assert "Turn 3" in result
    assert "40%" in result
    assert "PHASE 6" not in result


def test_progress_block_wins_over_interspersed_plan_heading(tmp_path: Path) -> None:
    """With both a mid-file ``### Turn 6`` plan and a real progress block,
    return the progress block — never the plan heading."""
    todo = tmp_path / "TODO.md"
    todo.write_text(
        _PLAN_ONLY_TODO + "\n## Turn 2 — 🔄 Progress: 20%\n- partial\n",
        encoding="utf-8",
    )

    result = _reader()._read_latest_progress(todo)

    assert "Turn 2" in result
    assert "PHASE 6" not in result


def test_golden_file_task_3c44a93f(tmp_path: Path) -> None:
    """Golden file: the actual TODO.md from task 3c44a93f.

    Its last ``## ...`` heading is ``## Current Status — Turn 12``; the buggy
    reader instead surfaced ``## Turn 6: PHASE 6`` from inside ``### Turn 6:``.
    """
    golden = Path(
        "workspace_data/tasks/3c44a93f/_context/TODO.md"
    )
    if not golden.exists():
        import pytest

        pytest.skip("golden task 3c44a93f TODO.md not present in this checkout")

    result = _reader()._read_latest_progress(golden)

    assert "PHASE 6" not in result
    # The real latest-progress marker in that file is the Current Status block.
    assert "Current Status — Turn 12" in result
