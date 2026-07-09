"""Admin endpoint to reload .env config at runtime (operator-triggered).

Exposes reload_settings() over the system admin router so an operator who edits
.env (switch LLM provider/model, rotate a feature key) can apply it without
restarting the whole app. Tests call the handler directly (the pure endpoint
function) — no live DB/LLM needed.
"""

from __future__ import annotations

import pytest

import app.config as cfg
from app.api.v1.endpoints import system as system_ep


@pytest.mark.asyncio
async def test_reload_config_endpoint_returns_ok_and_current_models():
    resp = await system_ep.reload_config()
    assert resp["status"] == "reloaded"
    # Reports the (post-reload) core/agent models so the operator can confirm
    # what is now live.
    assert "core_llm_model" in resp
    assert "agent_llm_model" in resp


@pytest.mark.asyncio
async def test_reload_config_picks_up_edited_env(tmp_path, monkeypatch):
    """End-to-end-ish: edit the .env source, hit the endpoint, see the change."""
    env_file = tmp_path / ".env"
    env_file.write_text("CORE_LLM_MODEL=model-one\n", encoding="utf-8")

    import dotenv
    real_load = dotenv.load_dotenv
    monkeypatch.setattr(
        dotenv, "load_dotenv",
        lambda *a, **k: real_load(dotenv_path=str(env_file), override=k.get("override", False)),
    )

    await system_ep.reload_config()
    assert cfg.get_settings().core_llm_model == "model-one"

    env_file.write_text("CORE_LLM_MODEL=model-two\n", encoding="utf-8")
    resp = await system_ep.reload_config()
    assert resp["core_llm_model"] == "model-two"
    assert cfg.get_settings().core_llm_model == "model-two"
