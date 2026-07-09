"""Manual settings reload (task 3c44a93f follow-up — operator changes .env).

get_settings() is an @lru_cache singleton: .env is read once at first call and
frozen. That is correct for static config, but an operator who edits .env
(switch LLM provider/model, rotate a feature API key) currently must restart the
whole app. reload_settings() lets a deliberate, operator-triggered reload pick
up the new .env WITHOUT a restart:

  - re-loads .env into os.environ (override=True, so the file wins on a manual
    reload — this is the explicit "I changed it, apply it" path),
  - clears the get_settings lru_cache so the next get_settings() rebuilds from
    the new environment,
  - returns the fresh Settings.

It does NOT retroactively update modules that captured ``settings =
get_settings()`` at import time — callers that read get_settings() per use pick
up the change; module-level snapshots need their own re-read. This is a
deliberate, bounded reload, not a magic global mutation.
"""

from __future__ import annotations

import app.config as config_mod
from app.config import get_settings, reload_settings


def test_reload_returns_fresh_settings_after_env_change(tmp_path, monkeypatch):
    """Editing the .env source and calling reload_settings() applies the change.

    Drives the real reload flow but points dotenv at a temp .env we control, so
    the test is deterministic and never depends on the project's real .env.
    """
    env_file = tmp_path / ".env"
    env_file.write_text("CORE_LLM_PROVIDER=bedrock\n", encoding="utf-8")

    import dotenv
    real_load = dotenv.load_dotenv
    monkeypatch.setattr(
        dotenv, "load_dotenv",
        lambda *a, **k: real_load(dotenv_path=str(env_file), override=k.get("override", False)),
    )

    reload_settings()
    assert get_settings().core_llm_provider == "bedrock"

    # Operator edits the .env file: switch provider, then reload.
    env_file.write_text("CORE_LLM_PROVIDER=openai\n", encoding="utf-8")
    fresh = reload_settings()
    assert fresh.core_llm_provider == "openai"
    # And the cache now serves the fresh value too.
    assert get_settings().core_llm_provider == "openai"


def test_reload_clears_lru_cache(monkeypatch):
    get_settings.cache_clear()
    first = get_settings()
    reload_settings()
    second = get_settings()
    # A reload must hand back a DISTINCT object (cache was cleared & rebuilt).
    assert first is not second


def test_reload_returns_a_settings_instance(monkeypatch):
    fresh = reload_settings()
    assert isinstance(fresh, config_mod.Settings)
