"""Tests for detect_stuck — the agent loop-detection heuristic (audit C1/C3).

This 14-pattern detector previously lived inline in AgentWorker with ZERO tests.
Extracted to a pure function, it can now be driven directly. These pin the key
patterns and — importantly — the not-stuck baseline, so future M2 work (replacing
the keyword matching with structured signals) has a behavioural anchor.
"""

from __future__ import annotations

from app.orchestration.stuck_detector import detect_stuck


def _call(name="t", args_hash="h", success=True, output_snippet="", output="", elapsed_s=0):
    return {
        "name": name, "args_hash": args_hash, "success": success,
        "output_snippet": output_snippet, "output": output, "elapsed_s": elapsed_s,
    }


# ───────────────────────── not stuck (baseline) ─────────────────────────


def test_empty_or_short_history_not_stuck():
    assert detect_stuck([]) == ""
    assert detect_stuck([_call()]) == ""


def test_healthy_varied_progress_not_stuck():
    # Varied tools, distinct outputs, successes → not stuck.
    recent = [
        _call(name=f"tool_{i}", args_hash=f"h{i}", output_snippet=f"unique result {i} " * 20)
        for i in range(12)
    ]
    assert detect_stuck(recent) == ""


# ───────────────────────── structural patterns ─────────────────────────


def test_pattern1_identical_call_15x():
    recent = [_call(name="scan", args_hash="SAME") for _ in range(15)]
    msg = detect_stuck(recent)
    assert "Repeated identical call" in msg


def test_pattern4_alternating_ab():
    recent = [_call(name="a"), _call(name="b"), _call(name="a"), _call(name="b")]
    assert "Alternating" in detect_stuck(recent)


def test_pattern5_all_failed_20x():
    # Distinct args so pattern 1 doesn't fire first; all failures.
    recent = [_call(name="probe", args_hash=f"h{i}", success=False) for i in range(20)]
    msg = detect_stuck(recent)
    assert "all failed" in msg or "failure rate" in msg.lower()


# ───────────────────────── semantic patterns ─────────────────────────


def test_pattern6_error_indicator_loop():
    recent = [_call(name=f"req{i}", args_hash=f"h{i}", output_snippet="503 service unavailable")
              for i in range(6)]
    assert "errors" in detect_stuck(recent).lower()


def test_pattern7_excessive_waiting():
    recent = [_call(name=f"wait{i}", args_hash=f"h{i}", elapsed_s=40) for i in range(5)]
    assert "waiting" in detect_stuck(recent).lower() or "sleeping" in detect_stuck(recent).lower()


def test_pattern12_analysis_paralysis():
    # 10 reads, zero produces → paralysis. Vary args/output so earlier patterns
    # (identical-call, polling, semantic-repetition) don't fire first.
    recent = [_call(name="read_file", args_hash=f"h{i}", output_snippet=f"file content {i} " * 30)
              for i in range(10)]
    assert "paralysis" in detect_stuck(recent).lower()


def test_produce_tool_breaks_paralysis():
    recent = [_call(name="read_file", args_hash=f"h{i}", output_snippet=f"content {i} " * 30)
              for i in range(9)]
    recent.append(_call(name="execute_code", args_hash="x", output_snippet="ran a script " * 30))
    # With a produce tool present, analysis-paralysis must NOT fire.
    assert "paralysis" not in detect_stuck(recent).lower()


# ───────────────────────── defensive: partial records ─────────────────────────


def test_partial_records_do_not_raise():
    # Records missing most keys must not crash (defensive .get throughout).
    recent = [{"name": "x"}, {"name": "y"}, {}]
    assert isinstance(detect_stuck(recent), str)
