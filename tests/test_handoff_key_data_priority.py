"""Cross-role handoff must keep the ACTION facts, not just the first 3 (C-1/C-2).

Context-continuity audit (2026-07-03, task-095fc772). "Teammates' Results" showed
only key_data[:3]. Agents write their achievements (✅) first and the facts the NEXT
agent actually needs — error locations ("main() dòng 405"), unfinished status
("❌ CHƯA sửa"), blockers — LAST. So [:3] delivered "all looks done" and dropped the
handoff. Turn 6 engineer then re-searched for a line number Turn 5 had named
explicitly → wasted turn, task closed at 13%.

prioritize_handoff_key_data sorts items carrying an action/handoff marker
(❌, CHƯA, dòng N, line N, KeyError, FAIL, blocker, crash, TODO, pending, must)
to the FRONT before truncating, so a wider-but-bounded slice keeps what matters.
"""

from __future__ import annotations

from app.orchestration.context_ledger import prioritize_handoff_key_data


def test_action_markers_float_to_front():
    kd = [
        "✅ Edit 1: gỡ gasification_loss_factor khỏi INPUT",
        "✅ Edit 2: thêm CGE forward",
        "✅ định nghĩa hằng số dòng 59-66",
        "Đã đọc results.json",
        "Ghi refutation_report.md 6.9KB",
        "❌ CHƯA sửa main() dòng 405 KeyError 'CGE_ideal'",   # the crucial handoff
    ]
    out = prioritize_handoff_key_data(kd, limit=3)
    joined = " ".join(out)
    # The action fact MUST survive a limit=3 slice now.
    assert "dòng 405" in joined
    assert "CHƯA" in joined


def test_line_number_marker_prioritized():
    kd = ["done A", "done B", "done C", "done D", "tautology at line 344 self-admits"]
    out = prioritize_handoff_key_data(kd, limit=2)
    assert any("line 344" in x for x in out)


def test_keeps_order_among_equal_priority():
    kd = ["fact A", "fact B", "fact C", "fact D"]
    out = prioritize_handoff_key_data(kd, limit=2)
    assert out == ["fact A", "fact B"]  # stable when no markers


def test_limit_respected():
    kd = [f"❌ blocker {i} dòng {i}" for i in range(20)]
    assert len(prioritize_handoff_key_data(kd, limit=8)) == 8


def test_empty_and_none_safe():
    assert prioritize_handoff_key_data([], limit=8) == []
    assert prioritize_handoff_key_data(None, limit=8) == []


def test_error_handoff_beats_numbers_in_number_heavy_task(tmp_path=None):
    """X-1 (arch review): W-4 added numbers to the marker set, but in a number-heavy
    task (gasifier) that let 6 result-numbers crowd out the ❌ error-handoff — the
    exact C-1/C-2 failure the prioritizer was built to fix. Error/unfinished handoff
    facts must outrank plain result numbers: 2-tier priority."""
    kd = [
        "CGE = 62.93% forward-computed",
        "closure = 4.197% within tolerance",
        "net = 20.0 kW after parasitic",
        "d_throat = 115.8 mm in range",
        "LHV = 5.14 MJ/Nm3",
        "ER = 0.30 equivalence ratio",
        "❌ CHƯA sửa main() dòng 405 KeyError CGE_ideal",
    ]
    out = prioritize_handoff_key_data(kd, limit=3)
    assert any("405" in x or "CHƯA" in x for x in out), (
        f"error-handoff crowded out by numbers (X-1 regression): {out}"
    )


def test_numeric_result_facts_prioritized():
    """R-6/W-4: a computed-number fact (CGE=62.93%, closure 4.2%, net 20 kW,
    d_throat=115.8mm) is a result the next agent needs — it has no ❌/error marker,
    so it used to sink into 'rest' and get dropped from the cross-role slice. Numbers
    with '=' or a unit must be treated as handoff-worthy."""
    kd = [
        "Loaded the biomass sizing skill",
        "Read input_data.json",
        "Consulted the reference tables",
        "Reviewed prior assumptions",
        "CGE = 62.93% (cold gas efficiency, forward-computed)",
    ]
    out = prioritize_handoff_key_data(kd, limit=2)
    assert any("62.93" in x for x in out), f"numeric result dropped: {out}"


def test_unit_number_prioritized():
    kd = ["did a", "did b", "did c", "d_throat = 115.8 mm within [80,120]"]
    out = prioritize_handoff_key_data(kd, limit=1)
    assert out == ["d_throat = 115.8 mm within [80,120]"]


def test_english_and_vietnamese_markers():
    for marker_kd in [
        "must fix the parser",
        "pending: write the CAD file",
        "FAILED to converge",
        "còn nợ: chưa chạy script",
    ]:
        kd = ["ok1", "ok2", "ok3", marker_kd]
        out = prioritize_handoff_key_data(kd, limit=1)
        assert out == [marker_kd], f"{marker_kd!r} not prioritized: {out}"
