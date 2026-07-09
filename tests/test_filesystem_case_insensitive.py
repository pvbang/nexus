"""M2 (audit 2026-06-29): case-insensitive fallback when reading a file.

On a case-sensitive filesystem (Linux/Docker — the real runtime) an agent that
wrote ``authorization_required.md`` and later reads ``AUTHORIZATION_REQUIRED.md``
(the CONTRACT-declared name) gets "File not found", then wrongly reports "no work
completed" though the 8 KB file exists. handle_read_file must, when an exact path
miss occurs, try a unique case-insensitive sibling before giving up.

These exercise the pure resolver helper so the test is OS-independent (it does not
depend on the host FS being case-sensitive).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.mcp.tools.filesystem import _resolve_case_insensitive


def _fs_is_case_sensitive(tmp_path: Path) -> bool:
    probe = tmp_path / "_CaseProbe"
    probe.write_text("x")
    return not (tmp_path / "_caseprobe").exists()


def test_resolves_unique_case_mismatch(tmp_path: Path):
    real = tmp_path / "authorization_required.md"
    real.write_text("x")
    got = _resolve_case_insensitive(tmp_path / "AUTHORIZATION_REQUIRED.md")
    assert got == real


def test_returns_none_when_no_sibling(tmp_path: Path):
    assert _resolve_case_insensitive(tmp_path / "MISSING.md") is None


def test_returns_none_when_ambiguous(tmp_path: Path):
    # Two files differing only by case → ambiguous, do NOT guess. Only meaningful
    # on a case-sensitive FS (Linux/Docker — the real runtime); on a
    # case-insensitive FS the two names ARE one file and exact-match wins.
    if not _fs_is_case_sensitive(tmp_path):
        pytest.skip("case-insensitive filesystem cannot hold two case-variant files")
    (tmp_path / "Report.md").write_text("a")
    (tmp_path / "report.md").write_text("b")
    assert _resolve_case_insensitive(tmp_path / "REPORT.md") is None


def test_exact_match_is_returned_as_is(tmp_path: Path):
    real = tmp_path / "exact.md"
    real.write_text("x")
    assert _resolve_case_insensitive(real) == real


def test_missing_parent_dir_is_safe(tmp_path: Path):
    assert _resolve_case_insensitive(tmp_path / "nope" / "x.md") is None
