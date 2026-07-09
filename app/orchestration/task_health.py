"""TaskHealth — single source of truth for a task's progress condition.

Architecture review 2026-07-03 (root cause A1/A2): "is this task stuck?" was
answered ≥5 different ways across ``termination.py`` / ``nexus_orchestrator.py`` /
``evolution_agent.py``, each re-deriving stagnation/plateau from the same
``progress_history`` with its own window and threshold, and ~14 completion/loop
gates layered on top. No authoritative verdict existed, so every audit added one
more detector ("firewall on firewall").

This module computes EVERY progress-condition ONCE per turn from EXPLICIT inputs and
returns an immutable ``TaskHealth``. It is pure (no orchestrator instance, no I/O,
no ``self``), mirroring the extraction of ``termination.py`` / ``progress_tracker.py``
(audit C1/C2). Consumers read ``health.stagnant`` / ``.plateaued`` / ``.barren`` /
``.severity`` instead of recomputing — so the answer is consistent and there is one
place to change a threshold.

Design contract (equivalence, so it can REPLACE the scattered checks without drift):
- ``stagnant`` uses the SAME rule as the completion gate — range over the last
  ``stagnant_window`` points ``< min_delta`` — but reads the SELF-graded history
  (E-R5: a low-pinning cap must not fake stagnation).
- ``plateaued`` reuses ``termination.turns_since_new_peak`` (the oscillation-catching
  rule 88<->91 that the range check misses).
- ``barren`` mirrors ``termination.should_halt_for_barren_turns``.
The migration is Strangler-Fig: TaskHealth is introduced alongside the existing
gates; consumers move over incrementally, each move guarded by the equivalence tests.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.orchestration.termination import (
    _MIN_PROGRESS_DELTA,
    _MAX_STAGNANT_TURNS,
    _MAX_PLATEAU_TURNS,
    _BARREN_TURNS_HALT_AT,
    is_stagnant_range,
    turns_since_new_peak,
)


@dataclass(frozen=True)
class TaskHealth:
    """One-shot verdict on a task's progress condition for the current turn."""

    current_progress: int          # latest capped progress % (what UI/completion see)
    self_progress: int             # latest self-graded progress % (pre-cap)
    stagnant: bool                 # range over recent self-progress < min_delta
    plateaued: bool                # no new high-water-mark for plateau_limit turns
    barren: bool                   # N consecutive turns produced nothing
    severity: float                # Evolution severity for this turn (0..1)
    stagnant_turns: int            # trailing turns since the last new peak
    turns_completed: int

    def summary(self) -> str:
        """Compact one-line health line for prompts / DECISIONS logging."""
        flags = []
        if self.stagnant:
            flags.append("STAGNANT")
        if self.plateaued:
            flags.append(f"PLATEAUED({self.stagnant_turns}t)")
        if self.barren:
            flags.append("BARREN")
        state = ", ".join(flags) if flags else "healthy"
        return (
            f"progress={self.current_progress}% (self {self.self_progress}%), "
            f"turns={self.turns_completed}, severity={self.severity:.1f} → {state}"
        )


def compute_task_health(
    *,
    self_progress_history: list[int] | None,
    capped_progress_history: list[int] | None,
    stagnant_window: int = _MAX_STAGNANT_TURNS,
    min_delta: int = _MIN_PROGRESS_DELTA,
    plateau_limit: int = _MAX_PLATEAU_TURNS,
    consecutive_barren_turns: int = 0,
    barren_halt_at: int = _BARREN_TURNS_HALT_AT,
    evolution_severity: float = 0.0,
) -> TaskHealth:
    """Compute the whole progress condition ONCE. Pure; never raises.

    ``self_progress_history`` drives stagnation/plateau (pre-cap, E-R5); the capped
    history is kept for reporting and as a fallback when self-history is empty.
    """
    _self = list(self_progress_history or [])
    _capped = list(capped_progress_history or [])
    # Fall back to capped history only when self-progress is unavailable (turn 1 /
    # resume) — same policy as is_progress_stagnant.
    _driver = _self if _self else _capped

    stagnant = is_stagnant_range(_driver, stagnant_window, min_delta)
    since_peak = turns_since_new_peak(_driver)
    plateaued = bool(_driver) and len(_driver) > plateau_limit and since_peak >= plateau_limit
    barren = consecutive_barren_turns >= barren_halt_at

    return TaskHealth(
        current_progress=_capped[-1] if _capped else 0,
        self_progress=_self[-1] if _self else (_capped[-1] if _capped else 0),
        stagnant=stagnant,
        plateaued=plateaued,
        barren=barren,
        severity=float(evolution_severity),
        stagnant_turns=since_peak,
        turns_completed=len(_capped) or len(_self),
    )
