"""Per-task progress tracking for the orchestrator.

Extracted from ``NexusOrchestrator`` (audit C1: split the god-object; C2:
consolidate the fragmented progress state). Previously progress lived in
separate dicts on the orchestrator — current % and full history — mutated from
several call sites with no single source of truth.

``ProgressTracker`` owns those maps behind one object with:
  - ``current(task_id)`` / ``history(task_id)`` reads,
  - ``record(task_id, pct)`` — the ONE write that updates current + appends
    history together (they previously drifted because callers updated them in
    two separate statements),
  - ``to_dict`` / ``from_dict`` for durability across restarts. The orchestrator
    keeps the maps in-memory as the source of truth during a task's lifetime and
    flushes/hydrates them at the existing checkpoint boundary (async), so the
    synchronous read paths (e.g. the completion gate) stay synchronous.

Design note: the maps are kept as plain dicts so the orchestrator can expose
them via backward-compatible properties; existing ``.get`` / ``[k]=`` /
``.setdefault(k, []).append`` call sites keep working unchanged.
"""

from __future__ import annotations


class ProgressTracker:
    """Owns the three per-task progress maps with a single write path."""

    def __init__(self) -> None:
        # task_id → latest progress percentage (0-100).
        self.current_map: dict[str, int] = {}
        # task_id → list of every recorded progress percentage, in turn order.
        self.history_map: dict[str, list[int]] = {}

    # ── reads ──────────────────────────────────────────────────────────────

    def current(self, task_id: str) -> int:
        """Latest progress % for the task (0 if never recorded)."""
        return self.current_map.get(task_id, 0)

    def history(self, task_id: str) -> list[int]:
        """Full progress history for the task ([] if none)."""
        return self.history_map.get(task_id, [])

    # ── single write path ───────────────────────────────────────────────────

    def record(self, task_id: str, progress_pct: int) -> None:
        """Set current progress AND append it to history atomically.

        This replaces the previous two-statement update (set current, then
        ``setdefault(...).append(...)`` elsewhere) that allowed the current
        value and the history tail to diverge.
        """
        self.current_map[task_id] = progress_pct
        self.history_map.setdefault(task_id, []).append(progress_pct)

    # ── durability ───────────────────────────────────────────────────────────

    def to_dict(self, task_id: str) -> dict:
        """Serialise one task's progress for checkpointing."""
        return {
            "current": self.current_map.get(task_id, 0),
            "history": list(self.history_map.get(task_id, [])),
        }

    def load(self, task_id: str, data: dict | None) -> None:
        """Hydrate one task's progress from a checkpoint dict (no-op if empty)."""
        if not data:
            return
        if "current" in data:
            self.current_map[task_id] = int(data["current"])
        if data.get("history") is not None:
            self.history_map[task_id] = [int(p) for p in data["history"]]

    def hydrate_from_turn_map(self, task_id: str, turn_to_pct: dict | None) -> None:
        """Rebuild current + history from an ``ExecutionState.progress_history``.

        That map is ``{turn_number: progress_pct}`` and is already persisted and
        restored via the task checkpoint. On resume the orchestrator (and thus
        this tracker) is recreated empty, which would lose the stagnation /
        completion history; rebuilding from the restored turn-map reunifies the
        two progress stores (audit C2).

        Note: a JSON round-trip turns the integer turn keys into strings, so the
        keys are coerced to ``int`` before ordering (otherwise "10" sorts before
        "2" and history order is wrong).
        """
        if not turn_to_pct:
            return
        # Resume is the path that most needs to be robust: a corrupt/legacy
        # checkpoint with a non-numeric turn key (or non-int pct) must not crash
        # the restore — skip the bad entries rather than raise.
        clean: list[tuple[int, int]] = []
        for turn, pct in turn_to_pct.items():
            try:
                clean.append((int(turn), int(pct)))
            except (TypeError, ValueError):
                continue
        if not clean:
            return
        clean.sort(key=lambda kv: kv[0])
        self.history_map[task_id] = [pct for _turn, pct in clean]
        self.current_map[task_id] = clean[-1][1]
