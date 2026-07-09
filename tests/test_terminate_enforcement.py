"""Evolution TERMINATE enforcement + budget over-ratio (task 3c44a93f bug #4a).

Two gaps the 3c44a93f run exposed:

1. Evolution recommended TERMINATE (severity 0.8, stagnating) from turn 7 on —
   6 consecutive times — but that was ADVISORY text the orchestrator ignored,
   dispatching empty turns anyway. There was no machine-readable "N consecutive
   terminate signals ⇒ stop" rule.

2. The run sat at 116% of budget, but the only budget ceiling fired at the
   absolute hard_stop ratio off a setting that could be larger than actual use.

These pin the pure-gate behaviour: a run of consecutive terminate signals forces
an HONEST halt (HALTED, not a fake 100% complete).
"""

from __future__ import annotations

from app.orchestration.termination import (
    _CONSECUTIVE_TERMINATE_LIMIT,
    evaluate_completion,
)


def _base(**kw):
    args = dict(
        llm_decision="continue",
        progress_pct=20,
        turn_count=10,
        progress_history=[18, 19, 20],
        prev_progress=20,
        is_security_task=True,
    )
    args.update(kw)
    return evaluate_completion(**args)


def test_consecutive_terminate_signals_force_halt():
    """At the limit, the loop stops with an honest 'evolution_terminate' reason."""
    d = _base(consecutive_terminate_signals=_CONSECUTIVE_TERMINATE_LIMIT)
    assert d.complete is True
    assert d.reason == "evolution_terminate"


def test_below_terminate_limit_does_not_force_halt():
    """One below the limit, a 'continue' is still respected (no premature stop)."""
    d = _base(consecutive_terminate_signals=_CONSECUTIVE_TERMINATE_LIMIT - 1)
    assert d.complete is False


def test_terminate_signal_default_is_inert():
    """Existing callers that don't pass the new arg keep their behaviour."""
    d = _base()  # no consecutive_terminate_signals
    assert d.complete is False
    assert d.reason == "llm_decision"


def test_evolution_terminate_is_not_a_fake_complete():
    """The forced halt must be flagged as a terminate reason (so update_todo
    writes an HONEST HALTED handoff), never reason 'llm_decision'."""
    d = _base(consecutive_terminate_signals=_CONSECUTIVE_TERMINATE_LIMIT + 2)
    assert d.reason == "evolution_terminate"
