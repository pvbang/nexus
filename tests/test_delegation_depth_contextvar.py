"""Tests for per-coroutine delegation-depth isolation.

Salvaged design (stash 2026-06-14): the nested-delegation depth counter was a
module-level `int` mutated via `global` (+= / -=). Under concurrent delegations
(two user requests delegating at the same time) the single shared int cross-
contaminates: request B sees request A's depth, so B can be wrongly refused as
"max depth reached" or, worse, A's `-= 1` in a finally runs while B is mid-flight,
corrupting the count. A `contextvars.ContextVar` gives each async context its own
depth, which is the correct primitive for per-request nesting state.

These tests pin the ContextVar behavior at the variable level (no LLM, no IO).
"""

from __future__ import annotations

import asyncio
import contextvars

from app.mcp.tools import delegate


def test_depth_is_a_contextvar():
    """The depth counter must be a ContextVar, not a bare module-level int."""
    assert isinstance(delegate._current_delegation_depth, contextvars.ContextVar)


def test_depth_defaults_to_zero():
    assert delegate._current_delegation_depth.get(0) == 0


async def test_depth_is_isolated_across_concurrent_contexts():
    """Two concurrent contexts must NOT see each other's depth.

    With a module-level int this fails: one task's increment leaks into the
    other. With a ContextVar each `asyncio.create_task` copies the context, so
    mutations stay local.
    """
    var = delegate._current_delegation_depth
    observed: dict[str, int] = {}
    barrier = asyncio.Event()

    async def worker(name: str, bumps: int):
        # Each worker raises its own depth, then waits so both are "in flight"
        # simultaneously before reading — exposing any shared-state leak.
        for _ in range(bumps):
            var.set(var.get(0) + 1)
        if name == "A":
            barrier.set()
        else:
            await barrier.wait()
        observed[name] = var.get(0)

    # create_task copies the current context per task → isolation expected.
    await asyncio.gather(worker("A", 2), worker("B", 1))

    # Each worker sees ONLY its own bumps, never the sum (which a shared int gives).
    assert observed["A"] == 2
    assert observed["B"] == 1


async def test_depth_mutation_does_not_leak_to_parent_context():
    """A child task bumping depth must not change the parent context's value."""
    var = delegate._current_delegation_depth
    var.set(0)

    async def child():
        var.set(var.get(0) + 1)
        return var.get(0)

    child_value = await asyncio.create_task(child())
    assert child_value == 1
    # Parent context is unchanged by the child's mutation.
    assert var.get(0) == 0
