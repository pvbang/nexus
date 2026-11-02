"""
Nexus - XSkill Embedding Utility.

Generate vector embeddings for Skills and Experiences to enable
semantic search (pgvector cosine similarity) instead of ILIKE keywords.

Architecture:
- Primary provider: configured via EMBEDDING_PROVIDER (bedrock/openai/openrouter/etc.)
- Fallback (guaranteed): HuggingFace sentence-transformers local (Qwen3-Embedding)
- If primary fails → automatic fallback to local HuggingFace model
- Pipeline NEVER breaks: worst case returns None only if both fail

Eager Init:
- Call init_embedding_model() at app startup to pre-load HuggingFace model
- Ensures first embedding call is fast (no cold-start download delay)

Config env vars (all optional):
- EMBEDDING_PROVIDER: bedrock | openai | openrouter | gemini | local | huggingface
- EMBEDDING_MODEL: Model name (default depends on provider)
- EMBEDDING_API_KEY: Dedicated API key for embedding endpoint
- EMBEDDING_BASE_URL: Custom base URL for embedding endpoint
"""

from __future__ import annotations

import asyncio
import threading
from typing import Optional

import structlog
from openai import AsyncOpenAI

from app.config import get_settings

logger = structlog.get_logger(__name__)

# ============================================
# Constants & Defaults
# ============================================

# Qwen3-Embedding: multilingual (incl. Vietnamese), 1024 dims, ~600MB
# Best balance of quality + size for local container deployment
FALLBACK_HF_MODEL = "Qwen/Qwen3-Embedding-0.6B"

# Default embedding models per provider
DEFAULT_MODELS: dict[str, str] = {
    "openai": "text-embedding-3-small",
    "bedrock": "amazon.titan-embed-text-v2:0",
    "openrouter": "text-embedding-3-small",
    "gemini": "text-embedding-004",
    "local": "nomic-embed-text",
    "huggingface": FALLBACK_HF_MODEL,
}
DEFAULT_EMBEDDING_MODEL = "text-embedding-3-small"

# Default dimensions per model (used when EMBEDDING_DIMENSIONS=0 / auto-detect)
DEFAULT_DIMENSIONS: dict[str, int] = {
    # Bedrock
    "amazon.titan-embed-text-v2:0": 1024,
    "amazon.titan-embed-text-v1": 1536,
    # OpenAI
    "text-embedding-3-small": 1536,
    "text-embedding-3-large": 3072,
    "text-embedding-ada-002": 1536,
    # Google
    "text-embedding-004": 768,
    # Local
    "nomic-embed-text": 768,
    # HuggingFace
    "Qwen/Qwen3-Embedding-0.6B": 1024,
}
# Fallback dimension when model is unknown and no explicit config
DEFAULT_DIMENSION_FALLBACK = 1024

MAX_TEXT_LENGTH = 8000  # Truncate to avoid token limit errors
EMBEDDING_TIMEOUT = 30  # seconds

# ============================================
# Module-level state (eager init)
# ============================================

# Cached HuggingFace model instance (loaded once at startup)
_hf_model_cache: dict[str, object] = {}
_hf_init_done: bool = False
_hf_init_failed: bool = False
_hf_load_lock = threading.Lock()  # Prevent multiple workers from loading concurrently


# ============================================
# Public API
# ============================================


async def init_embedding_model() -> bool:
    """
    Eager-initialize the HuggingFace embedding model at app startup.

    Downloads model from HuggingFace Hub (if not cached on disk) and loads
    into memory. Should be called in FastAPI lifespan or app startup hook.

    Returns:
        True if model loaded successfully, False otherwise.
    """
    global _hf_init_done, _hf_init_failed

    if _hf_init_done:
        return True

    settings = get_settings()
    # Determine which HF model to pre-load
    embedding_provider = getattr(settings, "embedding_provider", "").strip()
    embedding_model = getattr(settings, "embedding_model", "").strip()

    # If provider is explicitly huggingface, use configured model
    # Otherwise, always pre-load the fallback model
    if embedding_provider == "huggingface":
        model_name = embedding_model or FALLBACK_HF_MODEL
    else:
        model_name = FALLBACK_HF_MODEL  # Always load fallback for resilience

    try:
        loop = asyncio.get_event_loop()
        success = await loop.run_in_executor(None, _load_hf_model_sync, model_name)
        _hf_init_done = success
        _hf_init_failed = not success
        if success:
            logger.info("embedding_model_initialized", model=model_name, provider="huggingface")
        else:
            logger.warning("embedding_model_init_failed", model=model_name)
        return success
    except Exception as e:
        _hf_init_failed = True
        logger.warning("embedding_model_init_error", error=str(e), model=model_name)
        return False


async def generate_embedding(
    text: str,
    max_length: int = MAX_TEXT_LENGTH,
) -> Optional[list[float]]:
    """
    Generate embedding vector for text.

    Strategy:
    1. Try primary provider (configured via EMBEDDING_PROVIDER or core_llm_provider)
    2. If primary fails → fallback to HuggingFace local model (guaranteed offline)
    3. Returns None only if BOTH fail (extremely unlikely after init)

    Args:
        text: Text to embed. Truncated to max_length chars.
        max_length: Maximum text length before truncation.

    Returns:
        Embedding vector (list of floats) or None if all providers fail.
    """
    if not text or not text.strip():
        return None

    # Truncate text
    truncated = text[:max_length].strip()

    settings = get_settings()
    provider, model, api_key, base_url = _resolve_embedding_config(settings)

    # --- Primary provider attempt ---
    result = await _try_primary_provider(truncated, provider, model, api_key, base_url)
    if result is not None:
        return result

    # --- Fallback to HuggingFace local (always available after init) ---
    if provider != "huggingface":
        logger.debug("embedding_fallback_to_huggingface", primary_provider=provider)
        result = await _embed_via_huggingface(truncated, FALLBACK_HF_MODEL)
        if result is not None:
            return result

    return None


def get_embedding_dimensions() -> int:
    """
    Get the configured embedding vector dimensions.

    Resolution order:
    1. EMBEDDING_DIMENSIONS env var (explicit override, non-zero)
    2. DEFAULT_DIMENSIONS lookup by resolved model name
    3. DEFAULT_DIMENSION_FALLBACK (1024)

    This value is used by:
    - SQLAlchemy models (Vector(N) column definition)
    - Alembic migrations (ALTER COLUMN)
    - Vector search queries

    Returns:
        Integer dimension count for embedding vectors.
    """
    settings = get_settings()

    # 1. Explicit config override
    explicit_dims = getattr(settings, "embedding_dimensions", 0)
    if explicit_dims and explicit_dims > 0:
        return explicit_dims

    # 2. Resolve model name, then lookup default dims
    _, model, _, _ = _resolve_embedding_config(settings)
    dims = DEFAULT_DIMENSIONS.get(model, 0)
    if dims > 0:
        return dims

    # 3. Fallback
    return DEFAULT_DIMENSION_FALLBACK


# ============================================
# Internal: Provider routing
# ============================================


async def _try_primary_provider(
    text: str, provider: str, model: str, api_key: str, base_url: str
) -> Optional[list[float]]:
    """Try the primary configured provider."""
    try:
        if provider == "bedrock":
            return await _embed_via_litellm(text, model)
        elif provider == "huggingface":
            return await _embed_via_huggingface(text, model)
        else:
            return await _embed_via_openai_compat(text, provider, model, api_key, base_url)
    except Exception as e:
        logger.debug(
            "embedding_primary_failed",
            error=str(e),
            provider=provider,
            text_preview=text[:50],
        )
        return None


# ============================================
# Internal: Config resolution
# ============================================


def _resolve_embedding_config(
    settings: object,
) -> tuple[str, str, str, str]:
    """
    Resolve embedding provider/model/key/url from config.

    Priority:
    1. Explicit embedding_* settings (from EMBEDDING_* env vars)
    2. Fall back to core LLM provider settings
    """
    # Read from settings (pydantic-settings loads EMBEDDING_* env vars)
    embedding_provider = getattr(settings, "embedding_provider", "").strip()
    embedding_model = getattr(settings, "embedding_model", "").strip()
    embedding_api_key = getattr(settings, "embedding_api_key", "").strip()
    embedding_base_url = getattr(settings, "embedding_base_url", "").strip()

    # Resolve provider (fallback to huggingface — core_llm_provider is for chat LLM,
    # not embedding; e.g. bedrock Claude ≠ bedrock Titan Embed)
    provider = embedding_provider or "huggingface"

    # Resolve model (fallback to provider-specific default)
    model = embedding_model or DEFAULT_MODELS.get(provider, DEFAULT_EMBEDDING_MODEL)

    # Resolve API key
    api_key = embedding_api_key
    if not api_key:
        if provider == "openai":
            api_key = getattr(settings, "openai_api_key", "") or getattr(
                settings, "core_llm_api_key", ""
            )
        elif provider == "openrouter":
            api_key = getattr(settings, "openrouter_api_key", "") or getattr(
                settings, "core_llm_api_key", ""
            )
        elif provider == "gemini":
            api_key = getattr(settings, "core_llm_api_key", "")
        else:
            api_key = getattr(settings, "core_llm_api_key", "")

    # Resolve base URL
    base_url = embedding_base_url or getattr(settings, "core_llm_base_url", "")

    return provider, model, api_key, base_url


# ============================================
# Internal: Litellm (Bedrock) embeddings
# ============================================


async def _embed_via_litellm(text: str, model: str) -> Optional[list[float]]:
    """
    Generate embedding via litellm SDK (direct Bedrock call).

    Uses litellm.aembedding() — handles AWS credentials internally.
    No HTTP proxy needed.
    """
    try:
        import litellm

        litellm.set_verbose = False

        # Ensure bedrock/ prefix for litellm routing
        litellm_model = model
        if not litellm_model.startswith("bedrock/"):
            litellm_model = f"bedrock/{litellm_model}"

        response = await asyncio.wait_for(
            litellm.aembedding(model=litellm_model, input=[text]),
            timeout=EMBEDDING_TIMEOUT,
        )

        if response.data and len(response.data) > 0:
            embedding = response.data[0]["embedding"]
            logger.debug(
                "embedding_generated",
                model=litellm_model,
                text_length=len(text),
                dims=len(embedding),
            )
            return embedding

        return None

    except ImportError:
        logger.debug("litellm_not_installed")
        return None
    except asyncio.TimeoutError:
        logger.debug("embedding_timeout", model=model, timeout=EMBEDDING_TIMEOUT)
        return None
    except Exception as e:
        logger.debug("embedding_generation_failed", error=str(e), model=model)
        return None


# ============================================
# Internal: HuggingFace (sentence-transformers)
# ============================================


def _load_hf_model_sync(model_name: str) -> bool:
    """
    Synchronously load a HuggingFace model into cache.

    Thread-safe: uses lock to prevent multiple concurrent loads
    when multiple async tasks call run_in_executor simultaneously.
    """
    global _hf_model_cache

    # Fast path: already loaded (no lock needed)
    if model_name in _hf_model_cache:
        return True

    # Slow path: acquire lock and load (only one thread loads)
    with _hf_load_lock:
        # Double-check after acquiring lock
        if model_name in _hf_model_cache:
            return True

        try:
            from sentence_transformers import SentenceTransformer

            logger.info("huggingface_model_loading", model=model_name)
            _hf_model_cache[model_name] = SentenceTransformer(
                model_name, trust_remote_code=True
            )
            logger.info("huggingface_model_loaded", model=model_name)
            return True
        except ImportError:
            logger.warning(
                "sentence_transformers_not_installed",
                message="pip install sentence-transformers",
            )
            return False
        except Exception as e:
            logger.warning("huggingface_model_load_failed", error=str(e), model=model_name)
            return False


async def _embed_via_huggingface(text: str, model: str) -> Optional[list[float]]:
    """
    Generate embedding via HuggingFace sentence-transformers (local, offline).

    Uses Qwen3-Embedding by default:
    - Multilingual (supports Vietnamese, Chinese, English, etc.)
    - 1024 dimensions
    - ~600MB model size
    - Runs on CPU, no GPU needed

    Model is loaded eagerly at startup (init_embedding_model).
    If not yet loaded, loads on first call (cold start ~10-20s).
    """
    global _hf_model_cache

    try:
        from sentence_transformers import SentenceTransformer
    except ImportError:
        logger.debug(
            "huggingface_not_installed",
            message="Install: pip install sentence-transformers",
        )
        return None

    try:
        # Load model if not cached (lazy fallback if init wasn't called)
        if model not in _hf_model_cache:
            loop = asyncio.get_event_loop()
            success = await loop.run_in_executor(None, _load_hf_model_sync, model)
            if not success:
                return None

        st_model = _hf_model_cache[model]

        # sentence-transformers encode is sync — run in thread pool
        loop = asyncio.get_event_loop()
        embedding = await asyncio.wait_for(
            loop.run_in_executor(None, lambda: st_model.encode(text).tolist()),
            timeout=EMBEDDING_TIMEOUT,
        )

        logger.debug(
            "embedding_generated",
            provider="huggingface",
            model=model,
            text_length=len(text),
            dims=len(embedding),
        )
        return embedding

    except asyncio.TimeoutError:
        logger.debug("embedding_timeout", provider="huggingface", model=model)
        return None
    except Exception as e:
        logger.debug(
            "embedding_generation_failed",
            error=str(e),
            provider="huggingface",
            model=model,
            text_preview=text[:50],
        )
        return None


# ============================================
# Internal: OpenAI-compatible API
# ============================================


async def _embed_via_openai_compat(
    text: str,
    provider: str,
    model: str,
    api_key: str,
    base_url: str,
) -> Optional[list[float]]:
    """Generate embedding via OpenAI-compatible API (openai, openrouter, gemini, local)."""
    client = _build_openai_client(provider, api_key, base_url)
    if client is None:
        return None

    try:
        response = await asyncio.wait_for(
            client.embeddings.create(model=model, input=text),
            timeout=EMBEDDING_TIMEOUT,
        )

        if response.data and len(response.data) > 0:
            embedding = response.data[0].embedding
            logger.debug(
                "embedding_generated",
                provider=provider,
                model=model,
                text_length=len(text),
                dims=len(embedding),
            )
            return embedding

        return None

    except asyncio.TimeoutError:
        logger.debug("embedding_timeout", provider=provider, model=model)
        return None
    except Exception as e:
        logger.debug(
            "embedding_generation_failed",
            error=str(e),
            provider=provider,
            model=model,
        )
        return None


def _build_openai_client(
    provider: str, api_key: str, base_url: str
) -> Optional[AsyncOpenAI]:
    """Build AsyncOpenAI client. Returns None if no valid API key."""
    if provider == "openai":
        if not api_key:
            logger.debug("embedding_no_api_key", provider=provider)
            return None
        return AsyncOpenAI(
            api_key=api_key,
            base_url=base_url or "https://api.openai.com/v1",
        )

    elif provider == "openrouter":
        if not api_key:
            logger.debug("embedding_no_api_key", provider=provider)
            return None
        return AsyncOpenAI(
            api_key=api_key,
            base_url="https://openrouter.ai/api/v1",
        )

    elif provider == "gemini":
        if not api_key:
            logger.debug("embedding_no_api_key", provider=provider)
            return None
        return AsyncOpenAI(
            api_key=api_key,
            base_url=base_url or "https://generativelanguage.googleapis.com/v1beta/openai/",
        )

    elif provider == "local":
        local_url = base_url or "http://localhost:11434/v1"
        return AsyncOpenAI(
            api_key="local",
            base_url=local_url,
        )

    logger.debug("embedding_provider_unsupported", provider=provider)
    return None
