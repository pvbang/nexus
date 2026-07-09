"""Ground-truth artifact-delivery progress cap (task 3c44a93f bug #4c).

The orchestrator reported Progress 100% / "✅ ALL TASKS COMPLETE" for a task
whose CONTRACT declared 7 artifacts (6 phase reports + executive_summary) while
only ONE file existed on disk. Root cause: progress = (DONE+BLOCKED)/total and
the LLM marked the never-executed phases DONE — a self-graded number nothing
on disk backed.

Defense that does NOT trust the LLM: cap the reported progress by how many
declared CONTRACT artifacts actually EXIST on disk. A directory counts as
present only if it contains at least one file (an empty scaffold dir is not a
deliverable — synergy with bug #1).
"""

from __future__ import annotations

import importlib

import pytest

from app.orchestration.termination import artifact_delivery_cap


# ── Pure cap function ──────────────────────────────────────────────────────

def test_no_declared_artifacts_means_no_cap():
    assert artifact_delivery_cap(declared_count=0, present_count=0) is None


def test_all_artifacts_present_allows_full_progress():
    # 7/7 present → cap is at/above 100, never restricts a real completion.
    assert artifact_delivery_cap(declared_count=7, present_count=7) >= 100


def test_one_of_seven_present_caps_far_below_100():
    # The 3c44a93f shape: 1 file of 7 declared. Cap must forbid a 100% claim.
    cap = artifact_delivery_cap(declared_count=7, present_count=1)
    assert cap < 100
    assert cap < 50


def test_present_count_clamped_not_negative_or_over():
    assert artifact_delivery_cap(declared_count=3, present_count=99) >= 100
    assert artifact_delivery_cap(declared_count=3, present_count=-5) is not None


# ── Ground-truth artifact counting from CONTRACT.md vs disk ────────────────

@pytest.fixture
def ws(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_DIR", str(tmp_path))
    import app.core.task_paths as tp
    importlib.reload(tp)
    tp._parent_cache.clear()
    import app.orchestration.workspace_reader as wr
    importlib.reload(wr)
    yield wr, tp
    tp._parent_cache.clear()


_CONTRACT = """# Deliverable Contract

## What the user receives
### Artifact 1: Phase 1 report
- **Format**: Markdown
- **Location**: _work/redteam/phase_1/recon.md
- **Verify**: exists

### Artifact 2: Phase 2 report
- **Format**: Markdown
- **Location**: _work/redteam/phase_2/enum.md
- **Verify**: exists

### Artifact 3: Executive summary
- **Format**: md
- **Location**: _work/redteam/executive_summary.md
- **Verify**: exists
"""


def _write_contract(tp, task_id):
    path = tp.context_file(task_id, "CONTRACT.md")
    path.write_text(_CONTRACT, encoding="utf-8")


def test_counts_only_artifacts_that_exist_on_disk(ws):
    wr, tp = ws
    task_id = "artgt001"
    _write_contract(tp, task_id)
    work = tp.work_dir(task_id)
    # Only artifact 1 exists as a real non-empty file.
    (work / "redteam" / "phase_1").mkdir(parents=True)
    (work / "redteam" / "phase_1" / "recon.md").write_text("real content", encoding="utf-8")

    declared, present = wr.count_contract_artifacts(task_id)

    assert declared == 3
    assert present == 1


def test_empty_directory_artifact_is_not_counted_present(ws):
    wr, tp = ws
    task_id = "artgt002"
    _write_contract(tp, task_id)
    work = tp.work_dir(task_id)
    (work / "redteam" / "phase_1").mkdir(parents=True)
    (work / "redteam" / "phase_1" / "recon.md").write_text("real", encoding="utf-8")
    # phase_2 declared location exists but as an EMPTY dir → not a deliverable.
    (work / "redteam" / "phase_2").mkdir(parents=True)

    declared, present = wr.count_contract_artifacts(task_id)

    assert declared == 3
    assert present == 1  # empty phase_2 dir must NOT count


def test_no_contract_file_yields_zero_declared(ws):
    wr, tp = ws
    declared, present = wr.count_contract_artifacts("artgt404")
    assert declared == 0
    assert present == 0
