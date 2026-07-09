"""Tests for P3: circuit-breaker on consecutive all-failed tool rounds.

Audit §5 P3 — agents could run up to their role's max_rounds (default 500) with
no early stop when every tool call keeps failing. The existing
`_consecutive_error_calls` counter only *nudges* the reflection engine (and does
nothing when no reflection engine is attached). This adds a hard circuit-breaker:
after N consecutive rounds where the agent issued tool calls and ALL of them
failed, force-stop — the agent is thrashing, not progressing.

Pure decision functions: no LLM, no IO.
"""

from __future__ import annotations

from app.orchestration.failure_breaker import (
    MAX_CONSECUTIVE_FAILED_ROUNDS,
    is_failed_round,
    update_failed_round_streak,
)

# ───────────────────────── is_failed_round ─────────────────────────


def test_round_with_all_failed_calls_is_failed():
    calls = [{"success": False}, {"success": False}]
    assert is_failed_round(calls) is True


def test_round_with_one_success_is_not_failed():
    calls = [{"success": False}, {"success": True}]
    assert is_failed_round(calls) is False


def test_empty_round_is_not_failed():
    # No tool calls this round (e.g. agent produced text only) → not a failure round.
    assert is_failed_round([]) is False


def test_missing_success_key_treated_as_success():
    # Defensive: a record without an explicit success flag defaults to success
    # (mirrors tc.get("success", True) elsewhere) → not counted as failure.
    assert is_failed_round([{"name": "x"}]) is False


# ─────────────────── update_failed_round_streak ───────────────────


def test_streak_increments_on_failed_round():
    assert update_failed_round_streak(0, [{"success": False}]) == 1
    assert update_failed_round_streak(2, [{"success": False}]) == 3


def test_streak_resets_on_any_success():
    assert update_failed_round_streak(4, [{"success": True}]) == 0
    assert update_failed_round_streak(4, [{"success": False}, {"success": True}]) == 0


def test_streak_unchanged_on_empty_round():
    # A round with no tool calls neither advances nor resets the failure streak.
    assert update_failed_round_streak(3, []) == 3


# ─────────────────── breaker threshold is sane ───────────────────


def test_threshold_is_a_small_positive_int():
    assert isinstance(MAX_CONSECUTIVE_FAILED_ROUNDS, int)
    assert 2 <= MAX_CONSECUTIVE_FAILED_ROUNDS <= 10


# ──────────── integration: breaker fires inside the real loop ────────────


class _AlwaysToolCallLLM:
    """Fake LLM that requests the same failing tool every round, forever."""

    def __init__(self):
        self.calls = 0

    async def chat(self, messages, tools=None, **kwargs):
        # Only emit a tool call when tools are offered (the wrap-up/synthesis
        # calls pass no tools and should just return text).
        if not tools:
            return {"content": "done", "tool_calls": []}
        self.calls += 1
        return {
            "content": "trying again",
            "tool_calls": [
                {
                    "id": f"call_{self.calls}",
                    "function": {"name": "broken_tool", "arguments": "{}"},
                }
            ],
        }


class _AlwaysFailExecutor:
    async def execute(self, tool_name, tool_args):
        return {"success": False, "output": "boom: permanent failure"}


async def test_breaker_force_stops_thrashing_agent():
    from app.orchestration.agent_worker import AgentWorker
    from app.orchestration.models import AgentAssignment

    llm = _AlwaysToolCallLLM()
    worker = AgentWorker(
        agent_name="Tester",
        agent_role="developer",
        llm=llm,
        tool_executor=_AlwaysFailExecutor(),
        tool_schemas=[{"name": "broken_tool", "description": "x", "parameters": {}}],
    )

    result = await worker.execute(
        assignment=AgentAssignment(
            agent_role="developer",
            agent_name="Tester",
            task_description="do a thing that only fails",
        ),
        shared_context="",
    )

    assert not result.success
    assert "FORCE-STOPPED" in result.output
    # Must stop near the breaker threshold, NOT run to the role's max rounds (~500).
    assert llm.calls <= MAX_CONSECUTIVE_FAILED_ROUNDS + 1


class _SucceedsThenLoopsLLM:
    """Succeeds once, then thrashes on the same failing tool until a loop-stop.

    Guards that the loop-hard-stop "no successful tools" force-fail does NOT
    over-fire: an agent that produced real work before looping must keep its
    wrap-up summary and report success.
    """

    def __init__(self):
        self.calls = 0

    async def chat(self, messages, tools=None, **kwargs):
        if not tools:
            return {"content": "Summary: wrote the file, then got stuck.", "tool_calls": []}
        self.calls += 1
        name = "good_tool" if self.calls == 1 else "broken_tool"
        return {
            "content": "working",
            "tool_calls": [
                {"id": f"call_{self.calls}", "function": {"name": name, "arguments": "{}"}}
            ],
        }


class _OneSuccessThenFailExecutor:
    async def execute(self, tool_name, tool_args):
        if tool_name == "good_tool":
            return {"success": True, "output": "did real work"}
        return {"success": False, "output": "boom: permanent failure"}


async def test_loop_stop_after_real_work_is_not_force_failed():
    from app.orchestration.agent_worker import AgentWorker
    from app.orchestration.models import AgentAssignment

    llm = _SucceedsThenLoopsLLM()
    worker = AgentWorker(
        agent_name="Worker",
        agent_role="developer",
        llm=llm,
        tool_executor=_OneSuccessThenFailExecutor(),
        tool_schemas=[
            {"name": "good_tool", "description": "x", "parameters": {}},
            {"name": "broken_tool", "description": "x", "parameters": {}},
        ],
    )

    result = await worker.execute(
        assignment=AgentAssignment(
            agent_role="developer",
            agent_name="Worker",
            task_description="do work then maybe loop",
        ),
        shared_context="",
    )

    # It had a successful tool call → the loop-stop is a conclusion, not a thrash.
    assert "FORCE-STOPPED" not in result.output
    assert result.success is True
