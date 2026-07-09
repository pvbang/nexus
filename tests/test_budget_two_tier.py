"""Two-tier token budget architecture (E1 redesign, audit tri-model 2026-07-01).

The engine has TWO distinct cumulative-effort ceilings, and conflating them was
the budget-death root cause:

  - PER-TURN / per-react-loop  (config.agent_token_budget, default 400k): the
    effort ONE execute() may spend across its tool rounds. Reset at the start of
    every turn — a worker is reused across turns, so an un-reset per-turn tracker
    accumulates and trips "before the first tool call" on later turns.
  - PER-TASK / whole-task       (config.task_token_budget, default 2M): the total
    effort the whole task may spend, held in a process-global registry keyed by
    task_id. NEVER reset per turn — it is the lifetime ceiling that drives the
    orchestrator's one-synthesis-turn-then-halt.

These tests pin the boundary so a future change can't silently merge the tiers
(reset the task budget, or let the turn budget accumulate across turns again).
"""

from __future__ import annotations

from app.core.token_governor import (
    BudgetTracker,
    clear_task_budget,
    get_task_budget,
)


def test_turn_budget_reset_is_independent_of_task_budget():
    # Simulate the two trackers fed from the same usage stream (as _account_usage
    # does): the per-turn tracker is reset each turn, the per-task one is not.
    clear_task_budget("twotier01")
    turn = BudgetTracker(limit=400_000)
    task = get_task_budget("twotier01", limit=2_000_000)

    for _turn in range(6):
        turn.reset()                       # per-turn scoping
        turn.add({"total_tokens": 70_000})
        task.add({"total_tokens": 70_000})  # task budget keeps accumulating

    assert turn.used == 70_000                # only the LAST turn's effort
    assert turn.status().level == "ok"        # never trips on a healthy per-turn spend
    assert task.used == 420_000               # 6 × 70k accumulated for the task
    clear_task_budget("twotier01")


def test_task_budget_survives_a_turn_budget_reset_via_registry():
    # reset() on a freshly-fetched per-turn tracker must NOT touch the registry's
    # per-task tracker — they are different instances by construction.
    clear_task_budget("twotier02")
    task = get_task_budget("twotier02", limit=2_000_000)
    task.add({"total_tokens": 500_000})

    turn = BudgetTracker(limit=400_000)
    turn.reset()

    assert get_task_budget("twotier02", limit=2_000_000).used == 500_000
    clear_task_budget("twotier02")
