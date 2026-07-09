"""The goal anchor must survive TODO.md injection in previous_context (Bug A).

turn_executor built previous_context by first assigning the goal anchor, then —
when TODO.md exists (turn 2+) — RE-ASSIGNING previous_context to the TODO block,
which clobbered the anchor. So from turn 2 on the agent lost "USER ASKED"
entirely, contradicting the "Goal Anchor: ALWAYS first, NEVER truncated" comment.

The TODO block (and every later section) must APPEND (+=), never reassign. This is
a source-level canary: the assembly is inside a large async method that can't be
unit-isolated cheaply, so we pin the specific regression (a bare reassignment of
previous_context after the goal-anchor line) rather than re-run the whole turn."""

from __future__ import annotations

import re
from pathlib import Path

_SRC = (Path(__file__).resolve().parents[1] / "app/orchestration/turn_executor.py").read_text(
    encoding="utf-8"
)


def test_goal_anchor_set_then_todo_appends():
    # The goal-anchor line assigns previous_context...
    assert 'previous_context = self._goal_anchor + "\\n\\n"' in _SRC
    # ...and the TODO block must APPEND it, not reassign (the Bug A clobber).
    assert "previous_context += _todo_block" in _SRC


def test_no_bare_reassignment_of_previous_context_after_anchor():
    # After the goal-anchor assignment, previous_context must only ever be appended
    # to (+=) — never reassigned with `previous_context = <something else>` inside
    # the assembly (that is exactly what dropped the anchor).
    anchor_idx = _SRC.index('previous_context = self._goal_anchor')
    # Look at the assembly window after the anchor (bounded to the method region).
    window = _SRC[anchor_idx + 1: anchor_idx + 4000]
    bare_reassign = re.findall(r"\n\s*previous_context\s*=\s*(?!=)", window)
    assert bare_reassign == [], (
        f"found {len(bare_reassign)} bare reassignment(s) of previous_context after "
        "the goal anchor — these clobber it (Bug A). Use += instead."
    )
