"""_artifact_present_on_disk must resolve DIR-hint locations (E6, run 68c276ac).

The planner authors CONTRACT locations freely. In run 68c276ac it declared
``workspace_data/biomass_gasifier_design/`` (a directory hint with a made-up
group name and NO ``_work/`` prefix), but the agents wrote the real files flat
under ``_work/`` (technical_report.md, gasifier_specifications.json, ...).

The prior resolver only ran the basename fallback for FILE locations
(``not is_dir_hint``), so a DIR-hint that didn't match a literal subdir returned
present=0 — pinning ``artifact_delivery_cap`` at 10% even though 8 real files
existed on disk. This is the exact ceiling seen in the audit.

A DIR-hint should count as present when either (a) a directory of that name
exists anywhere under _work/ and holds a file, or (b) _work/ holds real
deliverables at all (the location is a logical grouping the agent flattened).
Empty scaffold dirs still must NOT count (task 3c44a93f bug #1).
"""

from __future__ import annotations

from pathlib import Path

from app.orchestration.workspace_reader import _artifact_present_on_disk


def test_dir_hint_flat_files_counts_present(tmp_path: Path) -> None:
    # Agent wrote files FLAT in _work/, planner declared a made-up group dir.
    work = tmp_path / "_work"
    work.mkdir()
    (work / "technical_report.md").write_text("# report", encoding="utf-8")
    (work / "gasifier_specifications.json").write_text("{}", encoding="utf-8")

    assert _artifact_present_on_disk(work, "workspace_data/biomass_gasifier_design/") is True


def test_dir_hint_matching_subdir_with_file_counts_present(tmp_path: Path) -> None:
    # Agent DID create a subdir of the hinted name (nested elsewhere) with a file.
    work = tmp_path / "_work"
    (work / "sim" / "biomass_gasifier_design").mkdir(parents=True)
    (work / "sim" / "biomass_gasifier_design" / "run.py").write_text("x=1", encoding="utf-8")

    assert _artifact_present_on_disk(work, "biomass_gasifier_design/") is True


def test_empty_workdir_dir_hint_is_absent(tmp_path: Path) -> None:
    # No deliverables at all → a DIR-hint must NOT be counted present.
    work = tmp_path / "_work"
    work.mkdir()

    assert _artifact_present_on_disk(work, "workspace_data/biomass_gasifier_design/") is False


def test_dir_hint_only_empty_scaffold_is_absent(tmp_path: Path) -> None:
    # Only an empty scaffold dir exists → still absent (bug #1 doctrine).
    work = tmp_path / "_work"
    (work / "biomass_gasifier_design").mkdir(parents=True)  # empty

    assert _artifact_present_on_disk(work, "biomass_gasifier_design/") is False


def test_existing_file_location_still_works(tmp_path: Path) -> None:
    # Regression guard: the pre-existing FILE resolution must be unaffected.
    work = tmp_path / "_work"
    work.mkdir()
    (work / "report.md").write_text("hello", encoding="utf-8")

    assert _artifact_present_on_disk(work, "workspace_data/pln/report.md") is True
    assert _artifact_present_on_disk(work, "missing.md") is False
