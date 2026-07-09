"""A SPECIFIC multi-segment Location must be checked strictly (R2-A / G2-4).

Root cause R2-A (tri-run audit round-2, 2026-07-03, task-095fc772 gasifier). The
dir-hint fallback in ``_artifact_present_on_disk`` (workspace_reader.py:472-473)
counted a location present if ``_work/`` held ANY file anywhere. That was meant for
a made-up 1-segment GROUP name the planner invents (run 68c276ac:
``biomass_gasifier_design/`` while files were flattened into ``_work/`` root).

But the gasifier CONTRACT declared 8 SPECIFIC multi-segment sub-paths
(``_work/gasifier/02_cad/dxf/``, ``.../03_simulation/``, ...). Only 3 of the 8 dirs
existed on disk (no .dxf/.step/.stl at all), yet the fallback reported ALL 8
present because ``01_design_basis/design_basis.py`` existed somewhere under _work/.
present=8=declared=8 → the artifact-delivery cap read 100% AND the completion veto
(present<declared) never fired → the task "completed" at 13% with 5/8 deliverables
missing.

Fix: the "any file under _work/" fallback applies ONLY to a bare 1-segment group
name. A location that names a specific sub-path (2+ segments after prefix
stripping) must be verified at (or under) that exact path — no global fallback.
"""

from __future__ import annotations

from pathlib import Path

from app.orchestration.workspace_reader import _artifact_present_on_disk


# ── the R2-A regression: specific sub-path must NOT be faked present ──────────

def test_specific_subpath_absent_when_dir_missing(tmp_path):
    """A deep CONTRACT sub-path that doesn't exist is ABSENT even though _work/ has
    other files (the gasifier bug: 02_cad/dxf/ empty but design_basis.py exists)."""
    work = tmp_path / "_work"
    (work / "gasifier" / "01_design_basis").mkdir(parents=True)
    (work / "gasifier" / "01_design_basis" / "design_basis.py").write_text("x=1", encoding="utf-8")
    # 02_cad/dxf never created, no .dxf anywhere.
    assert _artifact_present_on_disk(work, "_work/gasifier/02_cad/dxf/") is False


def test_specific_subpath_present_when_it_has_a_file(tmp_path):
    work = tmp_path / "_work"
    (work / "gasifier" / "02_cad" / "dxf").mkdir(parents=True)
    (work / "gasifier" / "02_cad" / "dxf" / "ga.dxf").write_text("0\nSECTION", encoding="utf-8")
    assert _artifact_present_on_disk(work, "_work/gasifier/02_cad/dxf/") is True


def test_gasifier_partial_delivery_counts_only_real_dirs(tmp_path):
    """The exact gasifier shape: 3 of 8 dirs exist → the 5 missing ones are ABSENT."""
    work = tmp_path / "_work" / "gasifier"
    for present_dir, fname in [
        ("01_design_basis", "design_basis.py"),
        ("02_cad", "geometry.json"),
        ("06_dossier", "DOSSIER.md"),
    ]:
        (work / present_dir).mkdir(parents=True)
        (work / present_dir / fname).write_text("x", encoding="utf-8")
    root = tmp_path / "_work"
    present = [
        _artifact_present_on_disk(root, f"_work/gasifier/{d}/")
        for d in ["01_design_basis", "02_cad/dxf", "02_cad/3d", "02_cad/img",
                  "03_simulation", "04_manufacturing", "05_safety_electrical", "06_dossier"]
    ]
    # Only 01_design_basis, 06_dossier are real dirs with files; 02_cad itself has a
    # file but 02_cad/dxf|3d|img do NOT exist.
    assert sum(present) == 2, f"expected 2 present (01, 06), got {sum(present)}: {present}"


# ── the 68c276ac case that the fallback legitimately serves — must STILL pass ──

def test_one_segment_group_name_still_uses_fallback(tmp_path):
    """A made-up 1-segment GROUP name with files flattened into _work/ root still
    counts present (the original dir-hint fallback purpose)."""
    work = tmp_path / "_work"
    work.mkdir()
    (work / "technical_report.md").write_text("# report", encoding="utf-8")
    assert _artifact_present_on_disk(work, "workspace_data/biomass_gasifier_design/") is True


def test_one_segment_group_matching_nested_dir_still_present(tmp_path):
    work = tmp_path / "_work"
    (work / "sim" / "biomass_gasifier_design").mkdir(parents=True)
    (work / "sim" / "biomass_gasifier_design" / "run.py").write_text("x=1", encoding="utf-8")
    assert _artifact_present_on_disk(work, "biomass_gasifier_design/") is True


def test_empty_workdir_still_absent(tmp_path):
    work = tmp_path / "_work"
    work.mkdir()
    assert _artifact_present_on_disk(work, "workspace_data/biomass_gasifier_design/") is False
    assert _artifact_present_on_disk(work, "_work/gasifier/02_cad/dxf/") is False


# ── a specific FILE location keeps its basename fallback (Opus 4.6 flatten case) ──

def test_specific_file_keeps_basename_fallback(tmp_path):
    """A FILE location still resolves by basename anywhere (agent wrote it flat)."""
    work = tmp_path / "_work"
    work.mkdir()
    (work / "phase1_recon.md").write_text("data", encoding="utf-8")
    assert _artifact_present_on_disk(work, "workspace_data/pln_redteam/phase1_recon.md") is True
