"""The agent's "STILL MISSING" (what's left) must be captured, not dropped (W-1/W-2).

Context-arch audit (2026-07-03): extract_did_what DELIBERATELY keeps only the
ACCOMPLISHED section (correct — did_what is "what was done"). But the STILL MISSING
section (the handoff: what's left / next step) was routed NOWHERE — blockers only
received result.error (a crash string). So "còn nợ gì / cần làm gì tiếp" evaporated
at the write layer. extract_still_missing captures it so the ledger's blockers field
carries the real outstanding work for the next turn.
"""

from __future__ import annotations

from app.orchestration.ledger_extract import extract_still_missing


def test_extracts_still_missing_section():
    out = """## ACCOMPLISHED
Wrote design_basis.py, ran it, closure=4.2%.

## STILL MISSING
- CAD .dxf files not generated yet
- pytest suite not written
- BOM missing VN suppliers
"""
    got = extract_still_missing(out)
    assert "CAD" in got and "dxf" in got
    assert "pytest" in got
    assert "closure=4.2" not in got  # must NOT pull the accomplished section


def test_vietnamese_heading():
    out = "## Kết quả\nĐã tính xong.\n\n## Còn thiếu\n- Chưa vẽ sơ đồ điện\n- Chưa có HAZOP"
    got = extract_still_missing(out)
    assert "sơ đồ điện" in got or "HAZOP" in got


def test_next_focus_alias():
    out = "## ACCOMPLISHED\ndone A\n\n## Next Step\nFix the KeyError at line 405 then rerun"
    got = extract_still_missing(out)
    assert "405" in got


def test_empty_when_no_missing_section():
    assert extract_still_missing("## ACCOMPLISHED\nall done, nothing left") == ""
    assert extract_still_missing("") == ""
    assert extract_still_missing(None) == ""


def test_bounded_length():
    out = "## STILL MISSING\n" + ("- item long detail here\n" * 200)
    assert len(extract_still_missing(out)) <= 600
