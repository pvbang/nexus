"""
Nexus - Shared Dependencies (DI).

Database engine, Redis connection, và các shared services.
"""

import json
from typing import AsyncGenerator, Optional

import redis.asyncio as aioredis
import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import get_settings
from app.core.credential_pool import CredentialPool
from app.core.provider_fallback import (
    FallbackLLMClient,
    ProviderConfig,
    ProviderFallbackChain,
)

logger = structlog.get_logger(__name__)
settings = get_settings()

# --- Database ---
_engine = None
_session_factory = None


async def init_db_engine():
    """Initialize async database engine and session factory."""
    global _engine, _session_factory
    _engine = create_async_engine(
        settings.database_url,
        pool_size=settings.database_pool_size,
        max_overflow=settings.database_max_overflow,
        echo=False,  # PERF-3: Disable SQL echo by default (was echo=app_debug → massive log noise)
        pool_pre_ping=True,  # C1-FIX: Detect stale connections from long-running SSE streams
        pool_recycle=1800,  # Recycle connections after 30 minutes
    )
    _session_factory = async_sessionmaker(
        bind=_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )


async def close_db_engine():
    """Dispose database engine."""
    global _engine
    if _engine:
        await _engine.dispose()
        _engine = None


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Dependency: yields an async database session.

    BUG-4 fix: Removed auto-commit. Callers (orchestrator, endpoints)
    are responsible for explicit commit(). This prevents double-commit
    and partial-state commit issues.
    """
    if _session_factory is None:
        raise RuntimeError("Database not initialized. Call init_db_engine() first.")
    async with _session_factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


# --- Redis ---
_redis: Optional[aioredis.Redis] = None


async def get_redis() -> aioredis.Redis:
    """Get or create Redis connection."""
    global _redis
    if _redis is None:
        _redis = aioredis.from_url(
            settings.redis_url,
            encoding="utf-8",
            decode_responses=True,
        )
    return _redis


async def close_redis():
    """Close Redis connection."""
    global _redis
    if _redis:
        await _redis.aclose()
        _redis = None


# --- Credential Pool ---
_credential_pool: Optional[CredentialPool] = None


def get_credential_pool() -> Optional[CredentialPool]:
    """Factory: create or return cached CredentialPool from config.

    Reads LLM_CREDENTIAL_POOL JSON from settings, parses it, and builds
    the CredentialPool with per-provider key lists.

    Returns None if no credential pool is configured.
    """
    global _credential_pool
    if _credential_pool is not None:
        return _credential_pool

    _settings = get_settings()
    raw = _settings.llm_credential_pool.strip()
    if not raw:
        return None

    try:
        pool_config: dict = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as exc:
        logger.error(
            "credential_pool_config_invalid_json",
            error=str(exc),
            hint="LLM_CREDENTIAL_POOL must be valid JSON: {\"provider\": [\"key1\", \"key2\"]}",
        )
        return None

    if not isinstance(pool_config, dict) or not pool_config:
        logger.warning("credential_pool_config_empty")
        return None

    pool = CredentialPool(cooldown_seconds=float(_settings.llm_credential_cooldown))

    for provider, keys in pool_config.items():
        if not isinstance(keys, list):
            logger.warning(
                "credential_pool_provider_invalid",
                provider=provider,
                hint="Value must be a list of API key strings",
            )
            continue
        str_keys = [str(k) for k in keys if k]
        if str_keys:
            pool.add_credentials(provider, str_keys)

    _credential_pool = pool
    logger.info(
        "credential_pool_initialized",
        providers=list(pool_config.keys()),
        cooldown=_settings.llm_credential_cooldown,
    )
    return _credential_pool


# --- Provider Fallback LLM ---
_fallback_llm: Optional[FallbackLLMClient] = None


def _parse_fallback_providers(raw: str) -> list[ProviderConfig]:
    """Parse comma-separated provider string into ProviderConfig list.

    Format: "provider:model,provider:model,..."
    Example: "openrouter:anthropic/claude-sonnet-4-20250514,openai:gpt-4o,gemini:gemini-2.0-flash"

    Provider-specific API keys are resolved from settings:
      - openrouter → settings.openrouter_api_key
      - openai    → settings.core_llm_api_key (or agent_llm_api_key)
      - gemini    → settings.core_llm_api_key
      - bedrock   → "" (uses AWS credentials)
      - local     → "not-needed"
    """
    if not raw.strip():
        return []

    _settings = get_settings()
    # Map provider → default API key from settings
    _api_key_map = {
        "openrouter": _settings.openrouter_api_key,
        # Prefer provider-native keys if present (OPENAI_API_KEY maps to openai_api_key).
        "openai": _settings.core_llm_api_key or _settings.openai_api_key,
        "gemini": _settings.core_llm_api_key,
        "bedrock": "",
        "local": "not-needed",
    }

    providers: list[ProviderConfig] = []
    for idx, entry in enumerate(raw.split(",")):
        entry = entry.strip()
        if not entry:
            continue
        if ":" not in entry:
            logger.warning("fallback_provider_invalid_format", entry=entry)
            continue
        provider, model = entry.split(":", 1)
        provider = provider.strip()
        model = model.strip()
        if not provider or not model:
            logger.warning("fallback_provider_empty", entry=entry)
            continue

        api_key = _api_key_map.get(provider, _settings.core_llm_api_key)
        providers.append(
            ProviderConfig(
                provider=provider,
                model=model,
                api_key=api_key,
                priority=idx,
                max_failures=_settings.llm_fallback_max_failures,
                cooldown_seconds=float(_settings.llm_fallback_cooldown),
            )
        )

    return providers


def get_fallback_llm() -> Optional[FallbackLLMClient]:
    """Factory: create or return cached FallbackLLMClient from config.

    Reads LLM_FALLBACK_PROVIDERS from settings, parses it, and builds
    the ProviderFallbackChain + FallbackLLMClient.

    Returns None if fallback is disabled or no providers are configured.
    """
    global _fallback_llm
    if _fallback_llm is not None:
        return _fallback_llm

    _settings = get_settings()

    if not _settings.llm_fallback_enabled:
        logger.info("fallback_llm_disabled")
        return None

    providers = _parse_fallback_providers(_settings.llm_fallback_providers)
    if not providers:
        logger.info("fallback_llm_no_providers", hint="Set LLM_FALLBACK_PROVIDERS in .env")
        return None

    chain = ProviderFallbackChain(providers=providers)

    # Integrate credential pool if configured
    pool = get_credential_pool()
    _fallback_llm = FallbackLLMClient(fallback_chain=chain, credential_pool=pool)

    logger.info(
        "fallback_llm_initialized",
        providers=[f"{p.provider}:{p.model}" for p in providers],
        max_failures=_settings.llm_fallback_max_failures,
        cooldown=_settings.llm_fallback_cooldown,
        credential_pool_active=pool is not None,
    )
    return _fallback_llm
