"""
Nexus - Centralized Model Context Limit Detection.

Auto-detects the context window size for any LLM model.

Resolution order (first match wins):
1. User override via CONTEXT_WINDOW_OVERRIDE env var
2. litellm registry (700+ models, auto-updated with package versions)
3. Minimal family-level fallback (only when litellm unavailable or model unknown)

The builtin fallback is intentionally MINIMAL — just 5 family patterns.
litellm is the source of truth and handles all edge cases.

Usage:
    from app.core.model_context import get_model_context_limit

    limit = get_model_context_limit()  # Auto-detect from settings
    limit = get_model_context_limit("claude-opus-4-6")  # 200K via litellm
    limit = get_model_context_limit("gemini-2.0-flash")  # 1M via litellm
"""

import threading
from typing import Optional

import structlog

logger = structlog.get_logger(__name__)

# ── Minimal family-level fallback (ONLY used when litellm is unavailable) ──
# Intentionally small — litellm handles all specific models.
# These are conservative estimates for model FAMILIES, not specific versions.
# IMPORTANT: Order matters — more specific patterns FIRST (longest match wins).
_FAMILY_FALLBACKS: list[tuple[str, int]] = [
    # Specific models that differ from family default (MUST be before family entry)
    ("claude-opus-4", 1_000_000),   # Claude Opus 4: 1M (Bedrock cross-region)
    ("claude-sonnet-4", 200_000),   # Claude Sonnet 4: 200K
    # Model families (broad patterns)
    ("gemini", 1_000_000),  # Most Gemini models: 1M+
    ("claude", 200_000),    # All other Claude 3+ models: 200K
    ("gpt-4.1", 1_000_000), # GPT-4.1: 1M
    ("gpt-4o", 128_000),    # GPT-4o family: 128K
    ("gpt-4", 128_000),     # GPT-4 turbo+: 128K
    ("o1", 200_000),        # OpenAI reasoning models: 200K
    ("o3", 200_000),        # OpenAI o3: 200K
    ("o4", 200_000),        # OpenAI o4: 200K
    ("deepseek", 128_000),  # DeepSeek: 128K
    ("llama", 128_000),     # Meta Llama 3+: 128K
    ("qwen", 128_000),      # Qwen 2+: 128K
    ("mistral", 128_000),   # Mistral large: 128K
    ("command-r", 128_000), # Cohere: 128K
]

# Default when absolutely nothing matches (safe minimum)
_DEFAULT_CONTEXT_LIMIT = 128_000

# Thread-safe cache
_cache: dict[str, int] = {}
_cache_lock = threading.Lock()


def get_model_context_limit(model: Optional[str] = None) -> int:
    """Get the context window limit for a model.

    Resolution order:
    1. User override (CONTEXT_WINDOW_OVERRIDE env)
    2. litellm.get_model_info() — accurate, 700+ models
    3. Family-level fallback (5 patterns) — only if litellm fails

    Args:
        model: Model name/identifier. If None, reads from app settings.

    Returns:
        Context window size in tokens.
    """
    if not model:
        model = _get_configured_model()

    if not model:
        return _DEFAULT_CONTEXT_LIMIT

    model_lower = model.lower()

    # Check cache first
    with _cache_lock:
        if model_lower in _cache:
            return _cache[model_lower]

    # 1. Check for explicit user override
    override = _get_override_from_settings()
    if override and override > 0:
        with _cache_lock:
            _cache[model_lower] = override
        return override

    # 2. Try litellm (primary source — handles 700+ models accurately)
    litellm_limit = _try_litellm_lookup(model)
    if litellm_limit:
        with _cache_lock:
            _cache[model_lower] = litellm_limit
        return litellm_limit

    # 3. Family-level fallback (minimal, only when litellm unavailable)
    family_limit = _lookup_family_fallback(model_lower)
    with _cache_lock:
        _cache[model_lower] = family_limit

    if family_limit != _DEFAULT_CONTEXT_LIMIT:
        logger.info(
            "model_context_family_fallback",
            model=model,
            limit=family_limit,
            note="litellm lookup failed, using family-level estimate",
        )
    else:
        logger.warning(
            "model_context_unknown_model",
            model=model,
            limit=_DEFAULT_CONTEXT_LIMIT,
            note="Model not found in litellm or family patterns. Using default 128K.",
        )

    return family_limit


def _get_configured_model() -> str:
    """Get the currently configured core model from app settings."""
    try:
        from app.config import get_settings
        return get_settings().core_llm_model or ""
    except Exception:
        return ""


def _get_override_from_settings() -> Optional[int]:
    """Check if user has explicitly set CONTEXT_WINDOW_OVERRIDE."""
    try:
        from app.config import get_settings
        settings = get_settings()
        override = getattr(settings, "context_window_override", None)
        if override and isinstance(override, int) and override > 0:
            return override
    except Exception:
        pass
    return None


def _try_litellm_lookup(model: str) -> Optional[int]:
    """Try to get context window from litellm's model registry.

    litellm maintains an up-to-date registry of 700+ models with
    accurate context window sizes. This is the authoritative source.

    Tries multiple model name variants to handle:
    - Provider-prefixed names: "bedrock/us.anthropic.claude-opus-4-6-v1"
    - Bare names: "claude-opus-4-6"
    - Full names: "anthropic/claude-opus-4-6-20250514"
    """
    try:
        import litellm

        # Build list of name variants to try
        model_variants = [model]

        # Strip known provider prefixes for registry lookup
        _stripped = model
        for prefix in ("bedrock/", "openrouter/", "together/", "anyscale/", "azure/"):
            if model.lower().startswith(prefix):
                _stripped = model[len(prefix):]
                model_variants.append(_stripped)
                break

        # Strip region prefixes (AWS Bedrock: "us.", "eu.")
        for region_prefix in ("us.", "eu.", "ap."):
            if _stripped.lower().startswith(region_prefix):
                _stripped = _stripped[len(region_prefix):]
                model_variants.append(_stripped)
                break

        # Strip "anthropic." prefix (Bedrock format)
        if _stripped.lower().startswith("anthropic."):
            _stripped = _stripped[len("anthropic."):]
            model_variants.append(_stripped)

        # Try adding common provider prefixes if bare name
        if "/" not in model:
            if "claude" in model.lower():
                model_variants.extend([f"anthropic/{model}", f"bedrock/{model}"])
            elif "gpt" in model.lower() or model.lower().startswith("o"):
                model_variants.append(f"openai/{model}")
            elif "gemini" in model.lower():
                model_variants.extend([f"gemini/{model}", f"vertex_ai/{model}"])

        # De-duplicate while preserving order
        seen = set()
        unique_variants = []
        for v in model_variants:
            if v.lower() not in seen:
                seen.add(v.lower())
                unique_variants.append(v)

        for variant in unique_variants:
            try:
                info = litellm.get_model_info(variant)
                if info:
                    # litellm returns max_input_tokens or max_tokens
                    limit = (
                        info.get("max_input_tokens")
                        or info.get("max_tokens")
                        or 0
                    )
                    if limit and limit > 0:
                        logger.debug(
                            "model_context_from_litellm",
                            model=model,
                            variant=variant,
                            limit=limit,
                        )
                        return limit
            except Exception:
                continue

    except ImportError:
        logger.debug("litellm_not_installed", note="Install litellm for accurate model context detection")
    except Exception as e:
        logger.debug("litellm_lookup_failed", model=model, error=str(e))

    return None


def _lookup_family_fallback(model_lower: str) -> int:
    """Last-resort family-level pattern matching.

    Only used when litellm is not installed or doesn't recognize the model.
    Matches against broad model family patterns (claude, gemini, gpt, etc.)
    """
    # Strip all provider/region prefixes for clean matching
    clean = model_lower
    for prefix in ("bedrock/", "openrouter/", "together/", "us.", "eu.", "ap.", "anthropic."):
        if clean.startswith(prefix):
            clean = clean[len(prefix):]

    for pattern, limit in _FAMILY_FALLBACKS:
        if pattern in clean:
            return limit

    return _DEFAULT_CONTEXT_LIMIT


def invalidate_cache(model: Optional[str] = None) -> None:
    """Invalidate cached context limit (e.g. after model switch).

    Args:
        model: Specific model to invalidate. None = clear all.
    """
    with _cache_lock:
        if model:
            _cache.pop(model.lower(), None)
        else:
            _cache.clear()
