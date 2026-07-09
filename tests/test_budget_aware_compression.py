"""Compress context EARLIER as the task budget runs low (budget-aware nén).

User question (2026-07-03): "does it auto-compress as budget runs out?" The task
budget is a cumulative-effort ceiling — hitting it HALTS (by design; it stops a
looping task from burning tokens forever). We do NOT raise that ceiling. But we can
make the remaining turns CHEAPER: as the task budget crosses warn/hard-stop, lower
the compression threshold so each turn ships a smaller context and the same ceiling
buys more useful turns. This is a pure multiplier — no ceiling bypass.
"""

from __future__ import annotations

from app.orchestration.agent_worker import budget_pressure_factor


def test_ok_budget_no_extra_compression():
    assert budget_pressure_factor("ok") == 1.0


def test_warn_budget_compresses_earlier():
    f = budget_pressure_factor("warn")
    assert 0.0 < f < 1.0, "warn must lower the threshold (compress sooner)"


def test_hard_stop_compresses_most():
    assert budget_pressure_factor("hard_stop") < budget_pressure_factor("warn")


def test_unknown_level_is_safe_default():
    assert budget_pressure_factor("") == 1.0
    assert budget_pressure_factor(None) == 1.0


def test_factor_never_zero_or_negative():
    # A zero/negative factor would force pathological compression every call.
    for lvl in ("ok", "warn", "hard_stop", "weird"):
        assert budget_pressure_factor(lvl) > 0.0
