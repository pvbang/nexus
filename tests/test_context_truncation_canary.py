"""Canary: the specific sites fixed in the context-allocation work must not
regress to a bare `[:N]` on agent-authored content. Targeted (not a blanket ban —
safe preview/log [:N] elsewhere is fine). audit tri-model 2026-07-01."""

from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]


def _src(rel: str) -> str:
    return (_ROOT / rel).read_text(encoding="utf-8")


def test_did_what_notes_not_hard_sliced():
    src = _src("app/orchestration/pipeline.py")
    assert 'ae.get("notes", "")[:_did_what_cap]' not in src
    assert 'smart_truncate(ae.get("notes", ""), _did_what_cap)' in src


def test_synth_text_not_hard_sliced():
    src = _src("app/orchestration/pipeline.py")
    assert "synth_text[:4000]" not in src


def test_epoch_summary_not_hard_sliced():
    src = _src("app/orchestration/context_ledger.py")
    assert 'summary[:4000] + "..."' not in src


def test_todo_uses_recent_slice():
    src = _src("app/orchestration/turn_executor.py")
    assert '.read_text(encoding="utf-8")[:_todo_cap]' not in src
    assert "_todo_context_slice(" in src


def test_force_synthesis_keeps_tail():
    src = _src("app/orchestration/agent_worker.py")
    assert "tc.output[:8000]" not in src
    assert "keep_head_tail(tc.output" in src


def test_next_focus_not_hard_sliced_at_500():
    src = _src("app/orchestration/nexus_orchestrator.py")
    # The goal_anchor read legitimately uses read_text(...)[:500]; the
    # next_turn_focus assignments must NOT. After removing the goal-anchor read,
    # no `)[:500]` may remain.
    residual = src.replace('read_text(encoding="utf-8")[:500]', "")
    assert ")[:500]" not in residual
