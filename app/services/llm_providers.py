"""
Nexus - OpenAI-compatible provider registry (single source of truth).

Every provider listed here speaks the OpenAI ``/chat/completions`` protocol, so
the SAME ``AsyncOpenAI`` client works for all of them — only the ``base_url`` and
the API-key environment variable differ.

This registry is consumed in three places, which is why it lives in its own
leaf module (imported by both, importing neither — no circular deps):
  1. ``llm_client._resolve_api_key``          → which env var holds the key
  2. ``llm_client._create_client_for_provider`` → the default base_url
  3. ``prompted_tools._NATIVE_TOOL_PROVIDERS``  → whether native tool-calling works

Adding a new OpenAI-compatible provider = ONE entry here. No other code changes.

Base URLs were verified reachable (a dummy key returns 401/400, i.e. the endpoint
exists and only rejects auth) and match each vendor's official OpenAI-compat docs.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProviderSpec:
    """Static description of one OpenAI-compatible LLM provider."""

    base_url: str
    # Env vars checked IN ORDER for the API key (first non-empty wins).
    env_keys: tuple[str, ...]
    # True if the provider implements OpenAI-style native function-calling.
    # False → the agent drives it via prompted (text) tool-calling instead.
    native_tools: bool = True
    # Human-facing docs URL (surfaced in .env.example / error hints).
    docs: str = ""


# provider name (lowercase, = value of CORE_LLM_PROVIDER) → spec
OPENAI_COMPATIBLE_PROVIDERS: dict[str, ProviderSpec] = {
    "deepseek": ProviderSpec(
        base_url="https://api.deepseek.com",
        env_keys=("DEEPSEEK_API_KEY",),
        docs="https://api-docs.deepseek.com",
    ),
    "groq": ProviderSpec(
        base_url="https://api.groq.com/openai/v1",
        env_keys=("GROQ_API_KEY",),
        docs="https://console.groq.com/docs/models",
    ),
    "xai": ProviderSpec(
        base_url="https://api.x.ai/v1",
        env_keys=("XAI_API_KEY", "GROK_API_KEY"),
        docs="https://docs.x.ai/docs/models",
    ),
    "mistral": ProviderSpec(
        base_url="https://api.mistral.ai/v1",
        env_keys=("MISTRAL_API_KEY",),
        docs="https://docs.mistral.ai/getting-started/models/models_overview",
    ),
    "together": ProviderSpec(
        base_url="https://api.together.xyz/v1",
        env_keys=("TOGETHER_API_KEY",),
        docs="https://docs.together.ai/docs/serverless-models",
    ),
    "fireworks": ProviderSpec(
        base_url="https://api.fireworks.ai/inference/v1",
        env_keys=("FIREWORKS_API_KEY",),
        docs="https://docs.fireworks.ai/getting-started/introduction",
    ),
    "cerebras": ProviderSpec(
        base_url="https://api.cerebras.ai/v1",
        env_keys=("CEREBRAS_API_KEY",),
        docs="https://inference-docs.cerebras.ai/models/overview",
    ),
    "moonshot": ProviderSpec(
        base_url="https://api.moonshot.ai/v1",
        env_keys=("MOONSHOT_API_KEY",),
        docs="https://platform.moonshot.ai/docs",
    ),
    "deepinfra": ProviderSpec(
        base_url="https://api.deepinfra.com/v1/openai",
        env_keys=("DEEPINFRA_API_KEY",),
        docs="https://deepinfra.com/docs/openai_api",
    ),
    "dashscope": ProviderSpec(
        base_url="https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
        env_keys=("DASHSCOPE_API_KEY",),
        docs="https://www.alibabacloud.com/help/en/model-studio/compatibility-of-openai-with-dashscope",
    ),
}


def get_provider_spec(provider: str) -> ProviderSpec | None:
    """Return the spec for a provider name (case-insensitive), or None."""
    return OPENAI_COMPATIBLE_PROVIDERS.get((provider or "").lower().strip())
