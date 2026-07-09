"""AWS credential refresh enablement (explicit flag + auto-detect default).

Today the refresher starts iff the provider is bedrock (main.py) — there is no
way to turn it off while still using bedrock with a STATIC/long-lived IAM key
(no session token to rotate), and no way to keep it for a non-default setup.

`should_run_aws_refresh(settings)` centralizes the decision as a pure function:
- aws_token_refresh_enabled is True  → always on (operator opt-in)
- aws_token_refresh_enabled is False → always off (e.g. static key / not bedrock)
- aws_token_refresh_enabled is None  → AUTO: on only when a bedrock provider is
  in use AND the credentials look like a rotating STS session token
  (aws_session_token present). Static access+secret keys are not rotated.

Priority order (reset key from refresh-aws.sh / env over .env) is unchanged —
this only decides WHETHER the background rotation loop runs.
"""

from __future__ import annotations

import types

from app.core.aws_refresher import should_run_aws_refresh


def _settings(**kw):
    base = dict(
        core_llm_provider="bedrock",
        agent_llm_provider="bedrock",
        aws_token_refresh_enabled=None,
        aws_session_token="STS-TEMP-TOKEN",
    )
    base.update(kw)
    return types.SimpleNamespace(**base)


# ── explicit flag wins ──────────────────────────────────────────────────────

def test_explicit_true_forces_on_even_for_non_bedrock():
    s = _settings(core_llm_provider="openai", agent_llm_provider="openai",
                  aws_token_refresh_enabled=True, aws_session_token="")
    assert should_run_aws_refresh(s) is True


def test_explicit_false_forces_off_even_for_bedrock_with_session_token():
    s = _settings(aws_token_refresh_enabled=False)
    assert should_run_aws_refresh(s) is False


# ── auto-detect (flag unset / None) ─────────────────────────────────────────

def test_auto_on_when_bedrock_and_session_token_present():
    s = _settings()  # bedrock + session token + flag None
    assert should_run_aws_refresh(s) is True


def test_auto_on_when_only_agent_provider_is_bedrock():
    s = _settings(core_llm_provider="openai")
    assert should_run_aws_refresh(s) is True


def test_auto_off_when_not_bedrock():
    s = _settings(core_llm_provider="openai", agent_llm_provider="gemini")
    assert should_run_aws_refresh(s) is False


def test_auto_off_when_bedrock_but_static_key_no_session_token():
    # Static/long-lived IAM key: access+secret but no rotating session token →
    # nothing to refresh, so AUTO must NOT start the loop.
    s = _settings(aws_session_token="")
    assert should_run_aws_refresh(s) is False
