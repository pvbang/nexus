"""Persisted LEDGER/EPOCHS fields must be word-boundary truncated, not hard-cut
mid-word (audit tri-model 2026-07-01 — the surviving variant of the did_what
mid-word bug lived on the orchestrator-notes branch, not the extract fallback)."""

from __future__ import annotations

from app.orchestration.text_budget import smart_truncate


def test_notes_truncation_is_word_boundary():
    notes = "Completed the reactor sizing and validated closure " * 40  # long
    out = smart_truncate(notes, 800)
    assert len(out) <= 801
    assert out.endswith("…")
    assert not out[:-1].rstrip().endswith(" ")  # ends on a whole word
