"""Artifact present-on-disk resolution must survive CONTRACT location drift.

Root cause S0 (cross-session audit 2026-06-30): the planner authors a CONTRACT
``Location`` string FREELY, so it often does not literally match where the agent
wrote the file:

  - deepseek run: Location ``workspace_data/recon/`` while files live at
    ``_work/recon/*.txt``.
  - Opus 4.6 run: Location ``workspace_data/pln_redteam/phase1_recon.md`` while
    the file lives FLAT at ``_work/phase1_recon.md`` (no ``pln_redteam/`` dir).

``_artifact_present_on_disk`` only stripped a leading ``_work/`` prefix, so both
forms resolved to a non-existent path → present=0 → ``artifact_delivery_cap``
pinned progress at 10% forever → stagnation → fabrication / verification-theater.

These tests pin the resolver to the REAL location forms observed in those runs.
"""

from __future__ import annotations

import importlib

import pytest


@pytest.fixture
def wr():
    import app.orchestration.workspace_reader as _wr
    importlib.reload(_wr)
    return _wr


# ── deepseek run: bare "workspace_data/<dir>/" location, files at _work/<dir>/ ──

def test_dir_location_with_workspace_data_prefix_is_found(wr, tmp_path):
    """`workspace_data/recon/` must resolve to `_work/recon/` (files exist there)."""
    work = tmp_path / "_work"
    (work / "recon").mkdir(parents=True)
    (work / "recon" / "phase1_dns.txt").write_text("real data", encoding="utf-8")

    assert wr._artifact_present_on_disk(work, "workspace_data/recon/") is True


def test_missing_file_with_workspace_data_prefix_is_absent(wr, tmp_path):
    """`workspace_data/pln_redteam_report.md` with no such file anywhere → absent."""
    work = tmp_path / "_work"
    work.mkdir(parents=True)
    # A differently-named report exists; the declared one does not.
    (work / "phase1_consolidated_report.md").write_text("x", encoding="utf-8")

    assert wr._artifact_present_on_disk(work, "workspace_data/pln_redteam_report.md") is False


def test_empty_scaffold_dir_with_workspace_data_prefix_is_absent(wr, tmp_path):
    """`workspace_data/pocs/` never created → absent (no empty-dir false positive)."""
    work = tmp_path / "_work"
    work.mkdir(parents=True)
    assert wr._artifact_present_on_disk(work, "workspace_data/pocs/") is False


# ── Opus 4.6 run: nested location, file written FLAT (path drift) ──

def test_file_found_by_basename_when_planner_dir_differs(wr, tmp_path):
    """`workspace_data/pln_redteam/phase1_recon.md` matches flat `_work/phase1_recon.md`."""
    work = tmp_path / "_work"
    work.mkdir(parents=True)
    (work / "phase1_recon.md").write_text("real recon", encoding="utf-8")

    assert wr._artifact_present_on_disk(
        work, "workspace_data/pln_redteam/phase1_recon.md"
    ) is True


def test_basename_fallback_requires_nonempty_file(wr, tmp_path):
    """A basename match that is an EMPTY file must not count as present."""
    work = tmp_path / "_work"
    work.mkdir(parents=True)
    (work / "phase1_recon.md").write_text("", encoding="utf-8")

    assert wr._artifact_present_on_disk(
        work, "workspace_data/pln_redteam/phase1_recon.md"
    ) is False


# ── Regression: the already-supported forms must keep working ──

def test_underscore_work_prefix_still_resolves(wr, tmp_path):
    work = tmp_path / "_work"
    (work / "redteam" / "phase_1").mkdir(parents=True)
    (work / "redteam" / "phase_1" / "recon.md").write_text("c", encoding="utf-8")

    assert wr._artifact_present_on_disk(work, "_work/redteam/phase_1/recon.md") is True


def test_full_workspace_tasks_path_with_embedded_work_resolves(wr, tmp_path):
    work = tmp_path / "_work"
    (work / "recon").mkdir(parents=True)
    (work / "recon" / "x.txt").write_text("c", encoding="utf-8")

    assert wr._artifact_present_on_disk(
        work, "workspace_data/tasks/0040db41/_work/recon/"
    ) is True
