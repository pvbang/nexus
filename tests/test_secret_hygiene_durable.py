"""P1 (CC-6): secrets must be redacted at every DURABLE write point.

Audit 2026-06-22: the redactor's AWS regex was `AKIA...` only — it missed the
`ASIA...` STS prefix, leaking a live temporary AWS key. There was no value-based
redaction, so the gateway password `AgyBedrock2026!` (low entropy, no key=value
form) slipped through. Worst of all, the durable logs (ACTIVITY.md, LEDGER.jsonl,
VAULT.jsonl) never called any redactor — so credentials landed on disk 1119×
across 97 files, where post-hoc redaction can't reach them.

These tests pin: (1) ASIA keys + known env-loaded secret values are redacted;
(2) ActivityLogger / ContextLedger / DataVault scrub before writing to disk.
"""

from __future__ import annotations

import json

import pytest

from app.core import secret_redaction as sr


# --- regex + value-based redaction -----------------------------------------

def test_redacts_asia_sts_key():
    out = sr.redact_secrets("key is ASIAQY3LZUAL6X3F3P7F here")
    assert "ASIAQY3LZUAL6X3F3P7F" not in out
    assert "REDACTED" in out


def test_redacts_akia_key_still():
    out = sr.redact_secrets("key is AKIAIOSFODNN7EXAMPLE here")
    assert "AKIAIOSFODNN7EXAMPLE" not in out


def test_value_based_redaction_of_known_password(monkeypatch):
    # A known credential pulled from the environment must be redacted even though
    # it is a bare token with no key=value form and low entropy.
    monkeypatch.setenv("AWS_TOKEN_REFRESH_AUTH_PASS", "AgyBedrock2026!")
    sr.refresh_known_secrets()
    try:
        out = sr.redact_secrets("login admin:AgyBedrock2026! to the gateway")
        assert "AgyBedrock2026!" not in out
    finally:
        monkeypatch.delenv("AWS_TOKEN_REFRESH_AUTH_PASS", raising=False)
        sr.refresh_known_secrets()


def test_value_based_from_pydantic_settings(monkeypatch):
    # pydantic BaseSettings reads .env into the Settings OBJECT, not os.environ.
    # The value-based redactor must therefore also pull known secrets from
    # get_settings(), not only from os.environ — otherwise a secret configured via
    # .env (e.g. the gateway password) leaks. We simulate a settings value.
    import app.config as cfg

    real = cfg.get_settings()

    class _S:
        def model_dump(self):
            return {"admin_password": "SuperSecretPw123", "core_llm_provider": "bedrock"}

    monkeypatch.setattr(cfg, "get_settings", lambda: _S())
    sr.refresh_known_secrets()
    try:
        out = sr.redact_secrets("the password is SuperSecretPw123 ok")
        assert "SuperSecretPw123" not in out
    finally:
        monkeypatch.setattr(cfg, "get_settings", lambda: real)
        sr.refresh_known_secrets()


def test_url_with_embedded_db_credentials_redacted():
    # A connection string with embedded credentials (any scheme, not just http)
    # must have the password redacted — the host can stay for debuggability.
    s = "connecting to postgresql+asyncpg://nexus:MekongAI2026Secret@db:5432/nexus"
    out = sr.redact_secrets(s)
    assert "MekongAI2026Secret" not in out


def test_value_based_ignores_short_or_empty(monkeypatch):
    # Must not redact trivially short / empty env values (would nuke normal text).
    monkeypatch.setenv("AWS_TOKEN_REFRESH_AUTH_PASS", "x")
    sr.refresh_known_secrets()
    try:
        out = sr.redact_secrets("the quick brown fox jumps over")
        assert out == "the quick brown fox jumps over"
    finally:
        monkeypatch.delenv("AWS_TOKEN_REFRESH_AUTH_PASS", raising=False)
        sr.refresh_known_secrets()


# --- durable writers scrub before persisting -------------------------------

@pytest.mark.asyncio
async def test_activity_logger_scrubs_on_disk(workspace_dir):
    from app.orchestration.activity_logger import ActivityLogger
    from app.orchestration.models import AgentThinking

    log = ActivityLogger("tsk")
    await log.log_agent_thinking(
        "scout",
        AgentThinking(
            agent_name="scout", agent_role="scout",
            thinking="dumping env: AWS_ACCESS_KEY_ID=ASIAQY3LZUAL6X3F3P7F",
        ),
    )
    text = log._file_path.read_text(encoding="utf-8")
    assert "ASIAQY3LZUAL6X3F3P7F" not in text


def test_context_ledger_scrubs_on_disk(workspace_dir):
    from app.orchestration.context_ledger import ContextLedger, LedgerEntry

    led = ContextLedger("tsk")
    led.append(LedgerEntry(
        turn=1, agent_role="scout", agent_name="s",
        did_what="found key ASIAQY3LZUAL6X3F3P7F",
        key_data=["token AKIAIOSFODNN7EXAMPLE in config"],
    ))
    raw = led._ledger_path.read_text(encoding="utf-8")
    assert "ASIAQY3LZUAL6X3F3P7F" not in raw
    assert "AKIAIOSFODNN7EXAMPLE" not in raw


def test_data_vault_scrubs_on_disk(workspace_dir):
    from app.orchestration.data_vault import DataVault

    vault = DataVault("tsk")
    vault.store(key="creds", content="secret ASIAQY3LZUAL6X3F3P7F captured",
                turn=1, agent_role="scout")
    raw = vault._vault_path.read_text(encoding="utf-8")
    assert "ASIAQY3LZUAL6X3F3P7F" not in raw
