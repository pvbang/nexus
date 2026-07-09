"""VAULT high-fidelity memory must be auto-injectable, not pull-only (W-7/C-9).

Context-arch audit: DataVault holds full-fidelity records (verdicts, exact numbers,
error locations) but was PULL-ONLY — usable only if the agent remembered to call
data_vault_retrieve, which weak models never did, so the memory "slept". build_recent_digest
exposes the most recent N records for the context builder to auto-inject.
"""

from __future__ import annotations

from app.orchestration.data_vault import DataVault


def _vault(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_DIR", str(tmp_path))
    import importlib, app.core.task_paths as tp
    importlib.reload(tp)
    tp._parent_cache.clear()
    import app.orchestration.data_vault as dv
    importlib.reload(dv)
    return dv.DataVault("vaulttk1")


def test_recent_digest_returns_latest_entries(tmp_path, monkeypatch):
    v = _vault(tmp_path, monkeypatch)
    for i in range(5):
        v.store(key=f"finding_{i}", content=f"Result {i}: value = {i*10} kW",
                turn=i + 1, agent_role="physicist", agent_name="P")
    digest = v.build_recent_digest(n=2, max_chars=2000)
    # The two most recent must be present, the oldest absent.
    assert "finding_4" in digest or "value = 40" in digest
    assert "finding_3" in digest or "value = 30" in digest
    assert "finding_0" not in digest


def test_recent_digest_empty_vault(tmp_path, monkeypatch):
    v = _vault(tmp_path, monkeypatch)
    assert v.build_recent_digest(n=3, max_chars=2000) == ""


def test_recent_digest_respects_budget(tmp_path, monkeypatch):
    v = _vault(tmp_path, monkeypatch)
    for i in range(6):
        v.store(key=f"k{i}", content="X" * 1000, turn=i + 1,
                agent_role="r", agent_name="R")
    digest = v.build_recent_digest(n=6, max_chars=800)
    assert len(digest) <= 800
