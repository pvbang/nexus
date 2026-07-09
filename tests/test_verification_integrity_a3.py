"""Tests for verification-integrity blocking (audit Gói 2: A3).

The system already DETECTS verification theater (tautology prober, self-attested
verdict extraction) but only injected it as LLM context. These tests pin the new
behaviour: a runtime-CONFIRMED tautology, or a completion resting only on
self-attested verdicts with no independent re-run, must BLOCK completion.

Covers:
  - tautology_detector.count_confirmed_tautologies / confirmed_tautology_lines (pure)
  - ledger_verifier.completion_needs_independent_review (pure)
  - pipeline._postprocess_evaluation veto branches (integration)
"""

from __future__ import annotations

from app.orchestration.tautology_detector import (
    ScanResult,
    TautologySuspect,
    confirmed_tautology_lines,
    count_confirmed_tautologies,
)
from app.orchestration.ledger_verifier import (
    TurnVerification,
    completion_needs_independent_review,
)
from app.orchestration.models import (
    AgentResult,
    EvaluationResult,
    ExecutionState,
    TurnResult,
)
from app.orchestration.pipeline import NexusExecutionPipeline


# ───────────────────────── tautology confirmed counters (pure) ─────────────────────────


def _suspect(confirmed: str, name: str = "closure", line: int = 10) -> TautologySuspect:
    return TautologySuspect(kind="tautology", name=name, line=line, detail="d", confirmed=confirmed)


def test_count_confirmed_only_counts_runtime_confirmed():
    scans = {
        "calc.py": ScanResult(suspects=[_suspect("confirmed"), _suspect("suspected")]),
        "sim.py": ScanResult(suspects=[_suspect("refuted")]),
    }
    # Only the runtime-CONFIRMED one counts — suspected/refuted must not block.
    assert count_confirmed_tautologies(scans) == 1


def test_count_confirmed_zero_when_none_confirmed():
    scans = {"a.py": ScanResult(suspects=[_suspect("suspected"), _suspect("refuted")])}
    assert count_confirmed_tautologies(scans) == 0


def test_confirmed_lines_render_path_and_line():
    scans = {"calc.py": ScanResult(suspects=[_suspect("confirmed", name="energy_closure", line=42)])}
    lines = confirmed_tautology_lines(scans)
    assert len(lines) == 1
    assert "calc.py:42" in lines[0]
    assert "energy_closure" in lines[0]


def test_confirmed_lines_excludes_unconfirmed():
    scans = {"a.py": ScanResult(suspects=[_suspect("suspected"), _suspect("refuted")])}
    assert confirmed_tautology_lines(scans) == []


# ───────────────────────── independent-review gate (pure) ─────────────────────────


def test_block_when_self_attested_and_no_independent_review():
    # LLM wants to complete; the turn rests on self-attested verdicts; nobody
    # independent re-ran them → must block for one independent-review turn.
    assert completion_needs_independent_review(
        is_complete=True, self_attested_recent=3, independent_review_recent=False
    ) is True


def test_no_block_when_independent_review_already_happened():
    assert completion_needs_independent_review(
        is_complete=True, self_attested_recent=3, independent_review_recent=True
    ) is False


def test_no_block_when_no_self_attested_claims():
    assert completion_needs_independent_review(
        is_complete=True, self_attested_recent=0, independent_review_recent=False
    ) is False


def test_no_block_when_not_completing():
    # Mid-task (not completing) → the gate is irrelevant, never blocks.
    assert completion_needs_independent_review(
        is_complete=False, self_attested_recent=5, independent_review_recent=False
    ) is False


# ───────────────────────── _postprocess_evaluation veto branches (integration) ─────────────────────────


def _pipeline():
    return NexusExecutionPipeline(llm=None, tool_executor=None, task_id="t-a3")


def _complete_eval() -> EvaluationResult:
    return EvaluationResult(turn_number=5, progress_pct=100, is_complete=True)


def test_postprocess_blocks_completion_on_confirmed_tautology():
    pipe = _pipeline()
    scans = {"calc.py": ScanResult(suspects=[_suspect("confirmed", name="energy_closure", line=42)])}
    ev = pipe._postprocess_evaluation(
        TurnResult(turn_number=5),
        _complete_eval(),
        turn_verification=TurnVerification(turn=5),
        tautology_scans=scans,
    )
    assert ev.is_complete is False
    blob = " ".join(ev.missing_items).lower()
    assert "calc.py:42" in " ".join(ev.missing_items)
    assert "self-cancelling" in blob or "verify" in blob or "re-derive" in blob


def test_postprocess_allows_completion_when_tautology_only_suspected():
    # A merely STRUCTURALLY-suspected (not runtime-confirmed) tautology must NOT
    # block completion — only proven self-cancelling checks are hard blockers.
    pipe = _pipeline()
    scans = {"calc.py": ScanResult(suspects=[_suspect("suspected")])}
    tv = TurnVerification(turn=5)  # no failed claims, no self-attested
    ev = pipe._postprocess_evaluation(
        TurnResult(turn_number=5),
        _complete_eval(),
        turn_verification=tv,
        tautology_scans=scans,
    )
    assert ev.is_complete is True


def test_postprocess_blocks_completion_on_self_attested_without_review():
    pipe = _pipeline()
    tv = TurnVerification(turn=5)
    tv.self_attested = ["energy closure 0.00% — PASS", "đã xác minh 20 kWe"]
    tv.claims_self_attested = 2
    ev = pipe._postprocess_evaluation(
        TurnResult(turn_number=5),
        _complete_eval(),
        turn_verification=tv,
        tautology_scans={},
        independent_review_recent=False,
    )
    assert ev.is_complete is False
    blob = " ".join(ev.missing_items).lower() + " " + (ev.next_turn_focus or "").lower()
    assert "independent" in blob or "re-execute" in blob or "refute" in blob


def test_postprocess_allows_completion_when_review_already_done():
    pipe = _pipeline()
    tv = TurnVerification(turn=5)
    tv.self_attested = ["energy closure 0.00% — PASS"]
    tv.claims_self_attested = 1
    ev = pipe._postprocess_evaluation(
        TurnResult(turn_number=5),
        _complete_eval(),
        turn_verification=tv,
        tautology_scans={},
        independent_review_recent=True,
    )
    assert ev.is_complete is True


# ── Hard-ceiling override (review G2-2): A3 gates must NOT veto past the absolute
#    turn / stagnation ceiling, else a task whose critic never clears the gate loops
#    forever. ──

def test_postprocess_tautology_does_not_block_at_hard_ceiling():
    pipe = _pipeline()
    scans = {"calc.py": ScanResult(suspects=[_suspect("confirmed")])}
    ev = pipe._postprocess_evaluation(
        TurnResult(turn_number=500),
        _complete_eval(),
        turn_verification=TurnVerification(turn=500),
        tautology_scans=scans,
        hard_ceiling_reached=True,
    )
    assert ev.is_complete is True


def test_postprocess_self_attest_does_not_block_at_hard_ceiling():
    pipe = _pipeline()
    tv = TurnVerification(turn=500)
    tv.self_attested = ["PASS"]
    tv.claims_self_attested = 1
    ev = pipe._postprocess_evaluation(
        TurnResult(turn_number=500),
        _complete_eval(),
        turn_verification=tv,
        tautology_scans={},
        independent_review_recent=False,
        hard_ceiling_reached=True,
    )
    assert ev.is_complete is True


# ── Critic-bypass (review G2-1): the independent-review signal is computed by the
#    pipeline from PRIOR turns only, excluding the current turn — verified at the
#    gate level: a same-turn rubber-stamp must NOT clear the self-attest block. ──

def test_self_attest_block_holds_when_no_prior_independent_review():
    # independent_review_recent=False models "no critic ran in a PRIOR turn"; the
    # block must hold even though (in the real pipeline) a producer self-attested
    # this same turn.
    pipe = _pipeline()
    tv = TurnVerification(turn=3)
    tv.self_attested = ["đã xác minh 20 kWe — PASS"]
    tv.claims_self_attested = 1
    ev = pipe._postprocess_evaluation(
        TurnResult(turn_number=3),
        _complete_eval(),
        turn_verification=tv,
        tautology_scans={},
        independent_review_recent=False,
    )
    assert ev.is_complete is False
    # And a critic is queued for the next turn to do the independent re-run.
    assert any(a.get("agent_role") == "critic" for a in (ev.next_assignments or []))
