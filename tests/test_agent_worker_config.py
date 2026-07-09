"""Tests for M3: agent-execution tunables sourced from config (single source of truth).

Audit §3 M3 — magic numbers were scattered as module constants in agent_worker
(_TOOL_TIMEOUT_SECONDS, _TOOL_OUTPUT_CAP_CHARS, _CONTEXT_COMPRESS_THRESHOLD_CHARS).
Worse, the tool timeout drifted from config: agent_worker hardcoded 6000 while
Settings.tool_timeout=8000 (used by parallel_tools / tool_executor) — two values
for one concept. These now resolve from Settings, with the worker reading them
in __init__ so a single config value drives every tool-call path.
"""

from __future__ import annotations

from app.config import Settings


def _worker(**overrides):
    from app.orchestration.agent_worker import AgentWorker

    return AgentWorker(agent_name="T", agent_role="developer", **overrides)


# ───────────────────────── config fields exist ─────────────────────────


def test_settings_expose_agent_execution_tunables():
    s = Settings()
    # tool_timeout already existed; the two new ones must have sane defaults.
    assert s.tool_timeout == 8000
    assert s.agent_tool_output_cap_chars == 20000
    assert s.context_compress_threshold_chars == 60000


# ─────────────── worker resolves tunables from settings ───────────────


def test_worker_resolves_tunables_from_settings():
    w = _worker()
    assert w._tool_timeout == 8000          # was hardcoded 6000 — drift fixed
    assert w._tool_output_cap == 20000
    assert w._compress_threshold == 60000


def test_worker_picks_up_overridden_settings(monkeypatch):
    import app.config as cfg
    monkeypatch.setenv("TOOL_TIMEOUT", "1234")
    monkeypatch.setenv("AGENT_TOOL_OUTPUT_CAP_CHARS", "5000")
    monkeypatch.setenv("CONTEXT_COMPRESS_THRESHOLD_CHARS", "7000")
    cfg.get_settings.cache_clear()
    try:
        w = _worker()
        assert w._tool_timeout == 1234
        assert w._tool_output_cap == 5000
        assert w._compress_threshold == 7000
    finally:
        cfg.get_settings.cache_clear()
