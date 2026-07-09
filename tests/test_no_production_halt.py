"""Barren-turn honest-halt gate (E7).

All three audited runs wasted 5–9 consecutive turns where NO agent landed a
single successful tool call — budget-death (agents died before their first tool
call), verification theater (prose "done" with zero tools), and phantom-write
(tool_call markup that never executed). Every existing loop gate keyed on
progress/hash/self-reported-severity, so a turn that produced only PROSE still
looked like activity and none of them tripped.

`should_halt_for_barren_turns(consecutive_barren_turns)` is an independent,
LLM-agnostic backstop: after N consecutive turns with zero successful tool calls
across the whole team, force an HONEST halt (report real progress, hand off what
exists) instead of dispatching a 6th, 7th, 8th barren turn. A single successful
tool call — of ANY kind, including read/search — resets the streak, so a
research/analysis phase that legitimately reads before writing is never killed.
"""

from __future__ import annotations

from app.orchestration.termination import (
    _BARREN_TURNS_HALT_AT,
    should_halt_for_barren_turns,
)


def test_no_halt_below_threshold():
    assert should_halt_for_barren_turns(0) is False
    assert should_halt_for_barren_turns(1) is False
    assert should_halt_for_barren_turns(_BARREN_TURNS_HALT_AT - 1) is False


def test_halt_at_threshold():
    assert should_halt_for_barren_turns(_BARREN_TURNS_HALT_AT) is True


def test_halt_above_threshold():
    assert should_halt_for_barren_turns(_BARREN_TURNS_HALT_AT + 3) is True


def test_threshold_is_sane():
    # Must be high enough to allow a genuine multi-turn read/plan phase, low
    # enough to not waste the 5–9 barren turns the audited runs burned.
    assert 3 <= _BARREN_TURNS_HALT_AT <= 5
