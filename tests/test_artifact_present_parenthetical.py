"""_artifact_present_on_disk must handle Location with a trailing "(file-list)".

Root cause E-R4 (tri-run audit 2026-07-02, task-9328b219 gasifier). build_contract
wrote each Location as a DIR + a parenthetical enumeration of the expected files,
e.g.:

    _work/design_basis/ (design_basis.py, results.json, power_chain.csv)

The parenthetical broke BOTH resolution branches:
  - ``loc.endswith("/")`` was False (it ends with ")") → is_dir_hint=False, so the
    dir-scan branch that would have matched never ran;
  - the basename fallback computed ``Path(loc).name`` = "(design_basis.py, ...)"
    → rglob of that literal name matched nothing.

Result: all 7 declared artifacts returned present=0 → artifact_delivery_cap pinned
at 10% for the whole run even though every file existed on disk (reproduced in
workspace_data/tasks_audit_2026-07-02/_repro_cap.py).

The resolver must strip a trailing parenthetical before resolving, then behave as
if the bare path had been given.
"""

from __future__ import annotations

from pathlib import Path

from app.orchestration.workspace_reader import _artifact_present_on_disk


def test_dir_location_with_parenthetical_filelist_counts_present(tmp_path: Path) -> None:
    # Exact shape of the gasifier CONTRACT Location line.
    work = tmp_path / "_work"
    (work / "design_basis").mkdir(parents=True)
    (work / "design_basis" / "design_basis.py").write_text("x=1", encoding="utf-8")
    (work / "design_basis" / "results.json").write_text("{}", encoding="utf-8")

    loc = "_work/design_basis/ (design_basis.py, results.json, power_chain.csv)"
    assert _artifact_present_on_disk(work, loc) is True


def test_nested_dir_location_with_parenthetical_counts_present(tmp_path: Path) -> None:
    # cad/dxf/ nested dir + parenthetical list (another gasifier line shape).
    work = tmp_path / "_work"
    (work / "cad" / "dxf").mkdir(parents=True)
    (work / "cad" / "dxf" / "gasifier_ga.dxf").write_text("0\nSECTION", encoding="utf-8")

    loc = "_work/cad/dxf/ (gasifier_ga.dxf, throat_hearth.dxf, cyclone.dxf)"
    assert _artifact_present_on_disk(work, loc) is True


def test_parenthetical_dir_with_no_files_is_absent(tmp_path: Path) -> None:
    # Stripping the parenthetical must NOT make an empty scaffold count present
    # (task 3c44a93f bug #1 doctrine still holds).
    work = tmp_path / "_work"
    (work / "design_basis").mkdir(parents=True)  # empty

    loc = "_work/design_basis/ (design_basis.py, results.json)"
    assert _artifact_present_on_disk(work, loc) is False


def test_plain_location_without_parenthetical_still_works(tmp_path: Path) -> None:
    # Regression guard: the ordinary (no-parenthetical) path must be unaffected.
    work = tmp_path / "_work"
    work.mkdir()
    (work / "report.md").write_text("hello", encoding="utf-8")
    assert _artifact_present_on_disk(work, "workspace_data/pln/report.md") is True
    assert _artifact_present_on_disk(work, "missing.md") is False
