"""next_turn_focus must not be hard-cut mid-word; at the raised cap both the
corrective directive and the trailing prior-focus survive (audit 2026-07-01, H1)."""

from __future__ import annotations

from app.orchestration.text_budget import smart_truncate

_NEXT_FOCUS_CAP = 800


def test_directive_plus_focus_fits_and_is_word_boundary():
    directive = "[RESOLVE CONTRADICTION] Settle with ONE ground-truth call: LHV 5.30 vs 4.87. "
    focus = "Then finalise the reactor sizing section and the BOM table."
    out = smart_truncate(directive + focus, _NEXT_FOCUS_CAP)
    assert "RESOLVE CONTRADICTION" in out
    assert "BOM table" in out           # trailing focus preserved at 800
    assert not out.endswith(" ")


def test_overlong_focus_cut_on_word_boundary():
    out = smart_truncate("[PIVOT] " + "word " * 400, _NEXT_FOCUS_CAP)
    assert len(out) <= _NEXT_FOCUS_CAP + 1
    assert out.endswith("…")
