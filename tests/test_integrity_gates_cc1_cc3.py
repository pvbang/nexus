"""P2-A/B integration: re-exec mismatch (CC-1) + numeric conflict (CC-3) gates.

Pins that `_postprocess_evaluation` vetoes completion when an artifact re-execution
mismatch or an unreconciled numeric conflict is present, and that both are skipped
at the hard ceiling (the absolute turn/stagnation limit must still win).
"""

from __future__ import annotations

from app.orchestration.artifact_reexecutor import ReExecResult, ReExecScan
from app.orchestration.numeric_integrity import NumericFact, NumericIntegrityReport
from app.orchestration.models import EvaluationResult, TurnResult
from app.orchestration.pipeline import NexusExecutionPipeline


def _pipe():
    return NexusExecutionPipeline(llm=None, tool_executor=None, task_id="t-int")


def _complete():
    return EvaluationResult(turn_number=5, progress_pct=100, is_complete=True)


def _mismatch_scan():
    return ReExecScan(results=[ReExecResult(
        rel_path="calc.py", status="mismatch",
        claimed_numbers={488.0}, observed_numbers={58.0},
        detail="claimed 488 but output 58",
    )])


def _conflict_report():
    af = NumericFact(label="power_kwe", value=20.0, source="calc.json")
    pf = NumericFact(label="power_kwe", value=14.3, source="report:prose")
    return NumericIntegrityReport(facts=[af], conflicts=[(af, pf)], has_structured=True)


# --- CC-1 re-exec mismatch gate --------------------------------------------

def test_reexec_mismatch_blocks_completion():
    ev = _pipe()._postprocess_evaluation(
        TurnResult(turn_number=5), _complete(), reexec_scan=_mismatch_scan(),
    )
    assert ev.is_complete is False
    assert any("calc.py" in m for m in ev.missing_items)


def test_reexec_mismatch_skipped_at_hard_ceiling():
    ev = _pipe()._postprocess_evaluation(
        TurnResult(turn_number=5), _complete(), reexec_scan=_mismatch_scan(),
        hard_ceiling_reached=True,
    )
    assert ev.is_complete is True


def test_reexec_unverified_does_not_block():
    scan = ReExecScan(results=[ReExecResult(rel_path="calc.py", status="unverified",
                                            detail="no sandbox")])
    ev = _pipe()._postprocess_evaluation(
        TurnResult(turn_number=5), _complete(), reexec_scan=scan,
    )
    assert ev.is_complete is True


# --- CC-3 numeric conflict gate --------------------------------------------

def test_numeric_conflict_blocks_completion():
    ev = _pipe()._postprocess_evaluation(
        TurnResult(turn_number=5), _complete(), numeric_scan=_conflict_report(),
    )
    assert ev.is_complete is False
    assert any("power_kwe" in m for m in ev.missing_items)


def test_numeric_conflict_skipped_at_hard_ceiling():
    ev = _pipe()._postprocess_evaluation(
        TurnResult(turn_number=5), _complete(), numeric_scan=_conflict_report(),
        hard_ceiling_reached=True,
    )
    assert ev.is_complete is True


def test_no_scans_completes_normally():
    ev = _pipe()._postprocess_evaluation(TurnResult(turn_number=5), _complete())
    assert ev.is_complete is True
