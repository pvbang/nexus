"""_force_synthesis must keep the TAIL of a long tool output — the final result
of a command often lives at the end, and this step writes the agent's conclusion
(audit tri-model 2026-07-01: N3 react-loop already keeps the tail; N4 did not)."""

from __future__ import annotations

from app.orchestration.text_budget import keep_head_tail


def test_keep_head_tail_preserves_final_result_line():
    output = "RUN LOG START\n" + ("step\n" * 2000) + "FINAL RESULT: efficiency = 23.4%"
    kept = keep_head_tail(output, head=4000, tail=4000)
    assert "RUN LOG START" in kept
    assert "FINAL RESULT: efficiency = 23.4%" in kept
