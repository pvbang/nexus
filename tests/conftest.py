"""Shared pytest fixtures for the app/ test suite.

Most orchestration logic under test here is pure-function / in-memory — no LLM,
no network, no live DB. These fixtures provide the small amount of isolation
those tests need (a scratch WORKSPACE_DIR, a fresh orchestrator) so they stay
fast and deterministic.
"""

from __future__ import annotations

import importlib

import pytest


@pytest.fixture
def workspace_dir(tmp_path, monkeypatch):
    """Point WORKSPACE_DIR at a temp dir and reload task_paths against it.

    Any test that touches the on-disk task tree (snapshots, ledger, task docs)
    should depend on this so it never writes into the real workspace.
    """
    monkeypatch.setenv("WORKSPACE_DIR", str(tmp_path))
    import app.core.task_paths as tp

    importlib.reload(tp)
    tp._parent_cache.clear()
    yield tp
    tp._parent_cache.clear()


@pytest.fixture
def orchestrator():
    """A NexusOrchestrator with no LLM wired.

    Pure-function methods (_determine_completion, plan_next_turn terminal paths,
    progress bookkeeping) don't call the LLM, so llm=None is safe and keeps the
    test from ever reaching the network.
    """
    from app.orchestration.nexus_orchestrator import NexusOrchestrator

    return NexusOrchestrator(llm=None)


@pytest.fixture(autouse=True)
def _isolate_pentest_scope(tmp_path_factory, monkeypatch):
    """Isolate every test from an operator-local ``pentest_scope.yaml``.

    The authorization gate reads a real scope file (project root) to decide which
    targets are pre-authorized. A developer's local scope must not change test
    outcomes (a test asserting "unauthorized target → blocked" would flip if the
    local scope happened to cover it). Point the scope at a non-existent file so
    tests see an EMPTY scope by default; a test that needs entries sets
    ``PENTEST_SCOPE_FILE`` itself (its own monkeypatch wins as it runs later).
    """
    empty = tmp_path_factory.mktemp("scope") / "none.yaml"
    monkeypatch.setenv("PENTEST_SCOPE_FILE", str(empty))
    try:
        import app.security.pentest_scope as _ps
        _ps.reload_scope()
    except Exception:
        pass
    yield
