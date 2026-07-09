"""Tests for P2: partial parallelism within a write-conflicting turn.

Audit §5 P2 — when ≥2 agents in a turn use write tools, the executor downgraded
the ENTIRE turn to sequential, so read-only agents that could safely run
concurrently were serialized too (~20-30% wasted wall-clock). The fix partitions
a downgraded turn: pure read-only agents (no write tools, no dependencies, not
depending on a peer's role) run concurrently first; everything else runs
sequentially afterward so writers still see the readers' ledger entries.

Pure function: no LLM, no IO.
"""

from __future__ import annotations

from app.orchestration.models import AgentAssignment
from app.orchestration.turn_executor import partition_assignments


def _mk(role, desc="do work", tools=None, deps=None):
    return AgentAssignment(
        agent_role=role,
        agent_name=role.title(),
        task_description=desc,
        tools_hint=tools or [],
        dependencies=deps or [],
    )


def test_readonly_agents_go_to_parallel_batch():
    a = _mk("researcher", tools=["web_search", "read_file"])
    b = _mk("analyst", tools=["read_file", "fetch_url"])
    parallel, sequential = partition_assignments([a, b])
    assert {x.agent_role for x in parallel} == {"researcher", "analyst"}
    assert sequential == []


def test_writers_go_to_sequential_batch():
    reader = _mk("researcher", tools=["web_search"])
    writer1 = _mk("dev", tools=["write_file"])
    writer2 = _mk("docs", tools=["task_doc_write"])
    parallel, sequential = partition_assignments([reader, writer1, writer2])
    assert [x.agent_role for x in parallel] == ["researcher"]
    assert {x.agent_role for x in sequential} == {"dev", "docs"}


def test_agent_with_dependency_is_not_parallel_safe():
    # Depends on another agent's output → must wait → sequential.
    a = _mk("reader", tools=["read_file"])
    b = _mk("summarizer", tools=["read_file"], deps=["reader"])
    parallel, sequential = partition_assignments([a, b])
    assert [x.agent_role for x in parallel] == ["reader"]
    assert [x.agent_role for x in sequential] == ["summarizer"]


def test_agent_referencing_peer_role_is_not_parallel_safe():
    # Implicit dependency: description mentions another agent's role.
    a = _mk("scraper", tools=["web_search"])
    b = _mk("writer", desc="combine the scraper findings", tools=["read_file"])
    parallel, sequential = partition_assignments([a, b])
    assert [x.agent_role for x in parallel] == ["scraper"]
    assert [x.agent_role for x in sequential] == ["writer"]


def test_parallel_batch_preserves_input_order():
    a = _mk("a", tools=["read_file"])
    b = _mk("b", tools=["read_file"])
    c = _mk("c", tools=["read_file"])
    parallel, _ = partition_assignments([a, b, c])
    assert [x.agent_role for x in parallel] == ["a", "b", "c"]


def test_all_writers_yields_empty_parallel_batch():
    w1 = _mk("dev1", tools=["write_file"])
    w2 = _mk("dev2", tools=["edit_file"])
    parallel, sequential = partition_assignments([w1, w2])
    assert parallel == []
    assert {x.agent_role for x in sequential} == {"dev1", "dev2"}


# ──────── integration: _execution_phase uses the partition when downgrading ────────


class _RecordingWorker:
    """Fake worker that records when it starts, and lets us observe concurrency."""

    def __init__(self, role, started, release_event=None):
        self.agent_role = role
        self.agent_name = role.title()
        self._started = started
        self._release = release_event

    def set_cancel_check(self, _):
        pass

    async def execute(self, assignment, shared_context, **kwargs):
        import asyncio as _a

        from app.orchestration.models import AgentResult

        self._started.append(self.agent_role)
        # Readers (parallel batch) hold here until released, proving that BOTH
        # readers are in-flight simultaneously rather than serialized.
        if self._release is not None:
            await _a.wait_for(self._release.wait(), timeout=2.0)
        else:
            await _a.sleep(0)
        return AgentResult(
            agent_name=self.agent_name, agent_role=self.agent_role,
            output="ok", success=True,
        )


def _executor_with(workers):
    from app.orchestration.activity_logger import ActivityLogger
    from app.orchestration.context_sharing import ContextSharing
    from app.orchestration.turn_executor import TurnExecutor

    return TurnExecutor(
        workers={w.agent_role: w for w in workers},
        context_sharing=ContextSharing(),
        activity_logger=ActivityLogger(task_id=""),
    )


async def test_execution_phase_runs_readers_parallel_writers_sequential():
    import asyncio

    from app.orchestration.models import TurnPlan

    started: list[str] = []
    gate = asyncio.Event()
    workers = [
        _RecordingWorker("reader1", started, release_event=gate),
        _RecordingWorker("reader2", started, release_event=gate),
        _RecordingWorker("writer1", started),  # no gate — runs after release
        _RecordingWorker("writer2", started),
    ]
    ex = _executor_with(workers)
    plan = TurnPlan(
        turn_number=1,
        objective="mixed turn",
        execution_mode="parallel",
        assignments=[
            _mk("reader1", tools=["read_file"]),
            _mk("reader2", tools=["web_search"]),
            _mk("writer1", tools=["write_file"]),
            _mk("writer2", tools=["edit_file"]),
        ],
    )

    async def _drive():
        # Let both readers reach the gate (proving concurrency) before releasing.
        await asyncio.sleep(0.05)
        assert set(started) == {"reader1", "reader2"}, started
        gate.set()

    results, _ = await asyncio.gather(ex._execution_phase(plan, ""), _drive())

    # Both readers started before any writer; writers ran after readers finished.
    assert started[:2] == ["reader1", "reader2"] or set(started[:2]) == {"reader1", "reader2"}
    assert set(started[2:]) == {"writer1", "writer2"}
    assert {r.agent_role for r in results} == {"reader1", "reader2", "writer1", "writer2"}
