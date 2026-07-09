"""did_what must capture DONE-work when the output uses the engine's own schema
(## ACCOMPLISHED / KEY FINDINGS / STILL MISSING) — audit tri-model 2026-07-01.
Previously extract_did_what only knew Summary/Conclusion/Final/Result, so an
ACCOMPLISHED-only output fell through to the tail heuristic and could capture
STILL MISSING (undone work) instead."""

from __future__ import annotations

from app.orchestration.ledger_extract import extract_did_what

_ACCOMPLISHED_OUTPUT = (
    "Preamble reasoning.\n\n"
    "## ACCOMPLISHED\n"
    "- Selected downdraft gasifier type based on high-ash rice husk.\n"
    "- Calculated reactor diameter 0.55 m and height 1.65 m.\n\n"
    "## KEY FINDINGS\n- Downdraft optimal for high-ash biomass.\n\n"
    "## STILL MISSING\n- Detailed mechanical drawing and supplier info.\n"
) + ("x" * 600)  # push over the 500-char short-circuit


def test_did_what_prefers_accomplished_over_still_missing():
    out = extract_did_what(_ACCOMPLISHED_OUTPUT)
    assert "downdraft gasifier" in out or "reactor diameter" in out
    assert "Detailed mechanical drawing" not in out  # must NOT be the STILL MISSING


def test_did_what_vietnamese_conclusion():
    text = (
        "Dài dòng mở đầu.\n\n## Kết quả\n- Đã hoàn thành mô phỏng, hiệu suất 23%.\n"
        + ("y" * 600)
    )
    out = extract_did_what(text)
    assert "hoàn thành mô phỏng" in out


def test_did_what_existing_summary_still_works():
    text = "intro\n\n## Summary\nBuilt and validated the model end to end.\n" + ("z" * 600)
    out = extract_did_what(text)
    assert "Built and validated" in out
