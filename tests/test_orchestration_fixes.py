"""Tests for the orchestration fixes surfaced by task 2dc763a5.

Each test targets the root-cause logic of one issue:

1. Telegram getUpdates 409 — background poll loop must be gated (single consumer).
2. Duplicate agent work — intra-turn assignment dedup.
3. Inability to idle — terminal detection when all remaining work is external-blocked.
4. Snapshot bloat — content-addressed dedup across turns.
5. Contradiction handling — any unresolved contradiction blocks completion.
6. Anti-hallucination — failed verification blocks completion.

These are pure-function / filesystem tests (no LLM, no network) so they run fast
and deterministically.
"""

from __future__ import annotations

import importlib

import pytest

# ───────────────────────── Issue #1: Telegram polling gate ─────────────────────────

def test_telegram_polling_disabled_by_default():
    """Explicit None + settings flag False → do NOT start the competing poll loop."""
    from app.channels.telegram import TelegramChannel

    # Default settings flag is False → no background consumer.
    assert TelegramChannel._resolve_should_poll(explicit=None, settings_flag=False) is False


def test_telegram_polling_explicit_overrides_settings():
    from app.channels.telegram import TelegramChannel

    # Explicit True wins even if settings flag is False.
    assert TelegramChannel._resolve_should_poll(explicit=True, settings_flag=False) is True
    # Explicit False wins even if settings flag is True.
    assert TelegramChannel._resolve_should_poll(explicit=False, settings_flag=True) is False
    # Explicit None falls back to settings flag.
    assert TelegramChannel._resolve_should_poll(explicit=None, settings_flag=True) is True


def test_telegram_polling_config_flag_exists():
    """The gate flag must exist on Settings, default False (mirrors email_channel_enabled)."""
    from app.config import Settings

    s = Settings()
    assert hasattr(s, "telegram_polling_enabled")
    assert s.telegram_polling_enabled is False


# ───────────────────────── Issue #2: assignment dedup ─────────────────────────

def _mk_assignment(role: str, desc: str):
    from app.orchestration.models import AgentAssignment
    return AgentAssignment(agent_role=role, agent_name=role.title(), task_description=desc)


def test_dedupe_drops_near_identical_tasks_different_roles():
    """money_maker and revenue_strategist given the SAME escalation work → keep one."""
    from app.orchestration.nexus_orchestrator import dedupe_assignments

    a = _mk_assignment(
        "money_maker",
        "Send a crisp credential-request escalation to the owner via Telegram and email "
        "asking for NPM_TOKEN or Stripe KYC. Verify owner reply.",
    )
    b = _mk_assignment(
        "revenue_strategist",
        "Send a crisp credential request escalation to owner via Telegram + email "
        "asking for NPM_TOKEN or Stripe KYC completion. Verify owner reply.",
    )
    kept, dropped = dedupe_assignments([a, b])
    assert len(kept) == 1
    assert len(dropped) == 1
    assert kept[0].agent_role == "money_maker"  # first occurrence wins


def test_dedupe_keeps_distinct_tasks():
    from app.orchestration.nexus_orchestrator import dedupe_assignments

    a = _mk_assignment(
        "developer",
        "Fork dsn-converter and fix the rotate-token parse offset in get-pin-number.ts",
    )
    b = _mk_assignment(
        "writer",
        "Write a Dev.to article about building safe infinite iterators in TypeScript",
    )
    kept, dropped = dedupe_assignments([a, b])
    assert len(kept) == 2
    assert dropped == []


def test_dedupe_ignores_injected_prefixes():
    """An injected [LOOP DETECTED ...] prefix must not make two same tasks look different."""
    from app.orchestration.nexus_orchestrator import dedupe_assignments

    base = "Poll telegram_get_updates and IMAP UNSEEN once for an owner reply containing a token."
    a = _mk_assignment("account_manager", base)
    b = _mk_assignment(
        "money_maker",
        "[LOOP DETECTED: use a different approach]\n\n" + base,
    )
    kept, dropped = dedupe_assignments([a, b])
    assert len(kept) == 1


# ───────────────────────── Issue #3: terminal/idle detection ─────────────────────────

def test_external_blocked_terminal_true_when_stagnant_and_external():
    from app.orchestration.nexus_orchestrator import is_externally_blocked_terminal

    assert is_externally_blocked_terminal(
        missing_items=[
            "Owner reply with NPM_TOKEN or Stripe KYC (outside agent control — pending)",
            "PR #487 merge (awaiting third-party maintainer)",
        ],
        next_turn_focus="Monitor-only: idle-poll for owner credential. Do NOT re-probe dead rails.",
        stagnant=True,
    ) is True


def test_external_blocked_terminal_false_when_actionable():
    from app.orchestration.nexus_orchestrator import is_externally_blocked_terminal

    # An actionable in-team step exists → NOT terminal, keep working.
    assert is_externally_blocked_terminal(
        missing_items=["Fix the 3 failing CI checks on PR #487"],
        next_turn_focus="Fix biome format + type errors and push to the fork.",
        stagnant=True,
    ) is False


def test_external_blocked_terminal_false_when_not_stagnant():
    from app.orchestration.nexus_orchestrator import is_externally_blocked_terminal

    # Even if blockers look external, progress is still moving → don't give up.
    assert is_externally_blocked_terminal(
        missing_items=["Owner reply pending (outside agent control)"],
        next_turn_focus="Awaiting owner credential",
        stagnant=False,
    ) is False


# ───── Issue #3 integration: real plan_next_turn returns None at terminal state ─────

async def test_plan_next_turn_returns_none_when_externally_blocked_and_stagnant():
    """Drives the real orchestrator: stagnant history + all-external blockers → terminal (None)."""
    from app.orchestration.models import EvaluationResult, ExecutionState, TurnResult
    from app.orchestration.nexus_orchestrator import NexusOrchestrator

    orch = NexusOrchestrator(llm=None)
    state = ExecutionState(task_id="cafebabe0001", total_agents=["money_maker"])
    # 3 completed turns → turn_count == 3.
    state.turns_completed = [TurnResult(turn_number=i) for i in range(1, 4)]
    # Flat progress history (range 0 < 3) → stagnant. Stored single value stays 0
    # so the metacognition branch (needs LLM) is skipped (delta 50 >= 3).
    orch._progress_history_by_task["cafebabe0001"] = [50, 50, 50]

    evaluation = EvaluationResult(
        turn_number=3,
        progress_pct=50,
        is_complete=False,
        missing_items=[
            "Owner reply with NPM_TOKEN or Stripe KYC (outside agent control — pending)",
        ],
        next_turn_focus="Monitor-only: idle-poll for the owner credential.",
        next_assignments=[{"agent_role": "money_maker", "task": "keep waiting"}],
    )

    plan = await orch.plan_next_turn(state, evaluation)
    assert plan is None


async def test_plan_next_turn_continues_when_actionable_work_remains():
    """Same stagnation, but an actionable in-team step exists → keep going (not None)."""
    from app.orchestration.models import EvaluationResult, ExecutionState, TurnResult
    from app.orchestration.nexus_orchestrator import NexusOrchestrator

    orch = NexusOrchestrator(llm=None)
    state = ExecutionState(task_id="cafebabe0002", total_agents=["developer"])
    state.turns_completed = [TurnResult(turn_number=i) for i in range(1, 4)]
    orch._progress_history_by_task["cafebabe0002"] = [50, 50, 50]

    evaluation = EvaluationResult(
        turn_number=3,
        progress_pct=50,
        is_complete=False,
        missing_items=["Fix the 3 failing CI checks on PR #487"],
        next_turn_focus="Fix biome format + type errors and push to the fork.",
        next_assignments=[{"agent_role": "developer", "task": "fix CI"}],
    )

    plan = await orch.plan_next_turn(state, evaluation)
    assert plan is not None
    assert len(plan.assignments) >= 1


# ───────────────────────── Issue #5: contradiction blocks completion ─────────────────────────

def test_contradiction_blocks_completion():
    from app.orchestration.nexus_orchestrator import contradictions_block_completion

    # Plain factual contradiction (no "constraint/invariant" keyword) must STILL block.
    assert contradictions_block_completion(
        ["AICrowd key is LIVE (money_maker) vs DEAD/401 (revenue_strategist) — unresolved"],
        llm_decision="complete",
    ) is True


def test_no_contradiction_allows_completion():
    from app.orchestration.nexus_orchestrator import contradictions_block_completion

    assert contradictions_block_completion([], llm_decision="complete") is False
    # Contradiction present but LLM already chose continue → nothing to override.
    assert contradictions_block_completion(["x vs y"], llm_decision="continue") is False


# ───────────────────────── Issue #6: verification gate ─────────────────────────

def test_verification_blocks_completion_on_failed_claims():
    from app.orchestration.ledger_verifier import verification_blocks_completion

    assert verification_blocks_completion(claims_failed=2) is True


def test_verification_allows_completion_when_clean():
    from app.orchestration.ledger_verifier import verification_blocks_completion

    assert verification_blocks_completion(claims_failed=0) is False


# ───────────────────────── Issue #4: snapshot content dedup ─────────────────────────

@pytest.fixture
def snapshot_env(tmp_path, monkeypatch):
    """Reload task_paths + workspace_snapshot against a temp WORKSPACE_DIR."""
    monkeypatch.setenv("WORKSPACE_DIR", str(tmp_path))
    import app.core.task_paths as tp
    importlib.reload(tp)
    tp._parent_cache.clear()
    import app.orchestration.workspace_snapshot as ws
    importlib.reload(ws)
    yield tp, ws
    tp._parent_cache.clear()


def test_snapshot_dedupes_unchanged_files_across_turns(snapshot_env):
    tp, ws = snapshot_env
    task_id = "deadbeefcafe"
    work = tp.work_dir(task_id)
    work.mkdir(parents=True, exist_ok=True)

    # 3 frozen docs that never change across turns.
    for i in range(3):
        (work / f"doc{i}.md").write_text("frozen content " * 50, encoding="utf-8")

    snap = ws.WorkspaceSnapshot(task_id)
    snap.take_snapshot(1)
    bytes_after_t1 = snap._total_snapshot_bytes

    # 4 more turns, same frozen files (the exact bloat pattern from task 2dc763a5).
    for turn in range(2, 6):
        snap.take_snapshot(turn)

    bytes_after_t5 = snap._total_snapshot_bytes

    # With content-addressing, unchanged files must NOT re-consume storage each turn.
    # Allow tiny overhead but it must be far below 5x (the old behavior).
    assert bytes_after_t5 <= bytes_after_t1 * 2, (
        f"snapshot storage grew {bytes_after_t5 / max(bytes_after_t1, 1):.1f}x — "
        "unchanged files are being recopied (bloat not fixed)"
    )


def test_snapshot_rollback_still_restores_after_dedup(snapshot_env):
    """Dedup must not break rollback — the core purpose of snapshots."""
    tp, ws = snapshot_env
    task_id = "feedfacef00d"
    work = tp.work_dir(task_id)
    work.mkdir(parents=True, exist_ok=True)

    target = work / "important.md"
    target.write_text("original good content", encoding="utf-8")

    snap = ws.WorkspaceSnapshot(task_id)
    pre = snap.take_snapshot(1)

    # Agent corrupts the file.
    target.write_text("CORRUPTED", encoding="utf-8")

    result = snap.rollback_to(pre)
    assert result["success"] is True
    assert "important.md" in result["restored"]
    assert target.read_text(encoding="utf-8") == "original good content"


def test_snapshot_changed_file_creates_new_blob(snapshot_env):
    """A file whose content changes must be backed up separately (not aliased to old)."""
    tp, ws = snapshot_env
    task_id = "0123456789ab"
    work = tp.work_dir(task_id)
    work.mkdir(parents=True, exist_ok=True)

    f = work / "evolving.md"
    f.write_text("version 1", encoding="utf-8")
    snap = ws.WorkspaceSnapshot(task_id)
    s1 = snap.take_snapshot(1)

    f.write_text("version 2 different", encoding="utf-8")
    s2 = snap.take_snapshot(2)

    # Rolling back to s1 must restore version 1, not version 2.
    f.write_text("scratch", encoding="utf-8")
    snap.rollback_to(s1)
    assert f.read_text(encoding="utf-8") == "version 1"

    f.write_text("scratch2", encoding="utf-8")
    snap.rollback_to(s2)
    assert f.read_text(encoding="utf-8") == "version 2 different"
