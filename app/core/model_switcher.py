"""
Model Switching — change LLM model mid-session.

Cho phép user chuyển đổi model LLM trong phiên chat qua /model command
hoặc programmatic API.

Session state stored in memory dict (session_id -> {model, provider}).
Available models read from app/config.py settings.
"""

from __future__ import annotations

import asyncio
from typing import Any, Optional

import structlog

from app.config import get_settings

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Default available models (used when config doesn't specify any)
# ---------------------------------------------------------------------------

_DEFAULT_AVAILABLE_MODELS: list[dict[str, str]] = [
    {"name": "us.anthropic.claude-opus-4-6-v1", "provider": "bedrock", "description": "Claude Opus 4 — most capable"},
    {"name": "gpt-4.1", "provider": "openai", "description": "GPT-4.1 — latest OpenAI flagship"},
    {"name": "gpt-4.1-mini", "provider": "openai", "description": "GPT-4.1 Mini — fast & efficient"},
    {"name": "kilo-auto/free", "provider": "kilo", "description": "Kilo Gateway auto-free (anonymous, no API key)"},
    {"name": "nvidia/nemotron-3-super-120b-a12b:free", "provider": "kilo", "description": "Kilo Nemotron 3 Super 120B (anonymous)"},
    {"name": "poolside/laguna-m.1:free", "provider": "kilo", "description": "Kilo Gateway Laguna (anonymous)"},
    {"name": "openai", "provider": "pollinations", "description": "Pollinations AI - free anonymous OpenAI models"},
    {"name": "gpt-4o", "provider": "g4f", "description": "GPT-4o via GPT4Free library"},
    {"name": "gpt-4", "provider": "g4f", "description": "GPT-4 via GPT4Free library"},
    {"name": "claude", "provider": "g4f", "description": "Claude via GPT4Free library"},
    {"name": "llama-3", "provider": "g4f", "description": "Llama 3 via GPT4Free library"},
]

# Short aliases for convenient /model command usage
_MODEL_ALIASES: dict[str, str] = {
    "claude-opus": "us.anthropic.claude-opus-4-6-v1",
    "opus": "us.anthropic.claude-opus-4-6-v1",
    "bedrock": "us.anthropic.claude-opus-4-6-v1",
    "gpt-4.1": "gpt-4.1",
    "gpt4.1": "gpt-4.1",
    "gpt-4.1-mini": "gpt-4.1-mini",
    "gpt4.1-mini": "gpt-4.1-mini",
    "nemotron": "nvidia/nemotron-3-super-120b-a12b:free",
    "free": "kilo-auto/free",
    "kilo-auto": "kilo-auto/free",
    "auto-free": "kilo-auto/free",
    "laguna": "poolside/laguna-m.1:free",
}


class ModelSwitcher:
    """
    Manages per-session model selection.

    Thread-safe via asyncio.Lock for mutable state.
    """

    def __init__(self) -> None:
        self._session_models: dict[str, dict[str, str]] = {}
        self._lock = asyncio.Lock()
        self._available_models: list[dict[str, str]] = self._load_available_models()

    @staticmethod
    def _load_available_models() -> list[dict[str, str]]:
        """Load available models from config or use defaults."""
        settings = get_settings()
        available_models_str = getattr(settings, "available_models", "")

        if available_models_str:
            # Parse JSON string from config
            import json
            try:
                models = json.loads(available_models_str)
                if isinstance(models, list):
                    return models
            except (json.JSONDecodeError, TypeError):
                logger.warning("invalid_available_models_config", raw=available_models_str)

        # Build from current config settings
        models: list[dict[str, str]] = [
            {
                "name": settings.core_llm_model,
                "provider": settings.core_llm_provider,
                "description": "Core LLM (configured)",
            },
        ]
        if settings.agent_llm_model != settings.core_llm_model:
            models.append({
                "name": settings.agent_llm_model,
                "provider": settings.agent_llm_provider,
                "description": "Agent LLM (configured)",
            })
        if settings.local_llm_enabled:
            models.append({
                "name": settings.local_llm_model,
                "provider": "local",
                "description": "Local LLM",
            })

        # Merge with defaults (avoid duplicates by name)
        existing_names = {m["name"] for m in models}
        for default_model in _DEFAULT_AVAILABLE_MODELS:
            if default_model["name"] not in existing_names:
                models.append(default_model)

        return models

    # ------ Model switching ------

    async def switch_model(
        self,
        session_id: str,
        model_name: str,
        provider: Optional[str] = None,
    ) -> dict[str, Any]:
        """
        Switch the model for a session.

        Returns dict with session_id, model, provider, and status.
        Raises ValueError if model is not valid.
        """
        # Resolve alias
        resolved = _MODEL_ALIASES.get(model_name.lower(), model_name)

        if not self.validate_model(resolved):
            available_names = [m["name"] for m in self._available_models]
            alias_list = list(_MODEL_ALIASES.keys())
            raise ValueError(
                f"Model '{model_name}' not found. "
                f"Available models: {', '.join(available_names[:6])}. "
                f"Aliases: {', '.join(alias_list[:8])}"
            )

        # Auto-detect provider if not specified
        if provider is None:
            provider = self._detect_provider(resolved)

        async with self._lock:
            self._session_models[session_id] = {
                "model": resolved,
                "provider": provider,
            }

        logger.info(
            "model_switched",
            session_id=session_id,
            model=resolved,
            provider=provider,
        )

        # Invalidate model_context cache so context limit is re-detected
        # for the new model (e.g. switching from Claude 200K to Gemini 1M)
        try:
            from app.core.model_context import invalidate_cache
            invalidate_cache()
        except Exception:
            pass  # model_context module may not be available

        return {
            "session_id": session_id,
            "model": resolved,
            "provider": provider,
            "status": "switched",
        }

    def get_current_model(self, session_id: str) -> dict[str, str]:
        """
        Get the current model for a session.

        Falls back to core LLM settings if no override.
        """
        if session_id in self._session_models:
            return self._session_models[session_id]

        settings = get_settings()
        return {
            "model": settings.core_llm_model,
            "provider": settings.core_llm_provider,
        }

    def list_available_models(self) -> list[dict[str, str]]:
        """Return all available models."""
        return list(self._available_models)

    def validate_model(self, model_name: str) -> bool:
        """Check if a model name is in the available list or aliases."""
        resolved = _MODEL_ALIASES.get(model_name.lower(), model_name)
        available_names = {m["name"] for m in self._available_models}
        return resolved in available_names

    def _detect_provider(self, model_name: str) -> str:
        """Auto-detect provider from model name using available models list."""
        for m in self._available_models:
            if m["name"] == model_name:
                return m.get("provider", "openrouter")

        # Heuristic fallback
        if "anthropic" in model_name or "claude" in model_name:
            return "bedrock"
        if "gpt" in model_name:
            return "openai"
        if "gemini" in model_name:
            return "gemini"
        if "kilo-auto" in model_name or "kilo/" in model_name:
            return "kilo"
        if "poolside" in model_name or "laguna" in model_name:
            return "kilo"
        if ":free" in model_name and ("nvidia" in model_name or "nemotron" in model_name):
            return "kilo"
        if "gpt-4o" in model_name or "gpt-4" in model_name or "claude" in model_name or "llama-3" in model_name:
            return "g4f"
        return "openrouter"

    # ------ Command parsing ------

    @staticmethod
    def parse_model_command(message: str) -> Optional[dict[str, Any]]:
        """
        Parse /model or /models command from user message.

        Returns:
        - {"action": "switch", "model": "<name>"} for "/model gpt-4o"
        - {"action": "show"} for "/model" (show current)
        - {"action": "list"} for "/models" (list available)
        - None if not a model command
        """
        stripped = message.strip()

        # /models (list all)
        if stripped == "/models":
            return {"action": "list"}

        # /model ...
        if not stripped.startswith("/model"):
            return None

        # Avoid matching /models as /model + s
        if stripped.startswith("/models") and len(stripped) > 7:
            return None

        parts = stripped.split(maxsplit=1)
        cmd = parts[0]

        # Exact /model
        if cmd == "/model" and len(parts) == 1:
            return {"action": "show"}

        if cmd == "/model" and len(parts) == 2:
            model_arg = parts[1].strip()
            # Check for optional provider: "/model openai:gpt-4o"
            if ":" in model_arg:
                provider_part, model_part = model_arg.split(":", 1)
                return {
                    "action": "switch",
                    "model": model_part.strip(),
                    "provider": provider_part.strip(),
                }
            return {"action": "switch", "model": model_arg}

        return None


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_model_switcher: Optional[ModelSwitcher] = None


def get_model_switcher() -> ModelSwitcher:
    """Return the global ModelSwitcher singleton."""
    global _model_switcher  # noqa: PLW0603
    if _model_switcher is None:
        _model_switcher = ModelSwitcher()
    return _model_switcher
