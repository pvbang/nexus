"""
MekongAI Nexus - LLM Client Service (Multi-Provider) with Retry & Failover.

Hỗ trợ providers:
- bedrock:  AWS Bedrock (Claude, Llama, Titan)
- openai:   OpenAI API (GPT-4o, GPT-4o-mini)
- gemini:   Google Gemini (gemini-2.0-flash, gemini-pro)
- openrouter: OpenRouter (100+ models)
- deepseek: DeepSeek API (deepseek-chat, deepseek-reasoner)
- local:    Ollama/vLLM (Qwen, Llama, Mistral)
- kilo:     Kilo Gateway (free models, anonymous access via IP rate limiting)

Features:
- Retry loop with exponential backoff (hermes-agent pattern)
- Error classifier integration for smart recovery
- Anthropic prompt cache injection
- Rate limit tracking from response headers
"""

import asyncio
import json
import random
import os
import threading
from typing import Any, AsyncGenerator, Optional

import structlog
from openai import AsyncOpenAI

from app.config import get_settings
from app.core.circuit_breaker import CircuitOpenError, get_circuit_breaker
from app.core.error_classifier import classify_api_error, FailoverReason
from app.core.exceptions import LLMError
from app.core.prompt_cache import (
    apply_anthropic_cache_control,
    is_anthropic_model,
    PromptCacheOptimizer,
)
from app.core.rate_limit_tracker import RateLimitTracker
from app.services.llm_providers import get_provider_spec
from app.services.prompted_tools import (
    build_tool_instructions,
    flatten_tool_messages,
    parse_tool_calls_from_text,
    supports_native_tools,
)
from app.services.toolcall_recovery import recover_tool_calls
from app.services.safety_finish_reason import SAFETY_FINISH_REASONS

logger = structlog.get_logger(__name__)


def _settings():
    """Lazy settings accessor — avoids caching at import time."""
    return get_settings()


_api_key_cache: dict[str, tuple[str, float]] = {}
_API_KEY_CACHE_TTL = 60.0  # seconds


def _resolve_api_key(provider: str, explicit_api_key: str = "") -> str:
    """Resolve provider API key from settings/env (best-effort, cached 60s).

    Many deployments set provider-native variables like OPENAI_API_KEY or
    OPENROUTER_API_KEY (as in our .env.example) instead of CORE_LLM_API_KEY.
    """
    if explicit_api_key:
        return explicit_api_key

    import time as _time
    p = (provider or "").lower().strip()
    now = _time.monotonic()

    cached = _api_key_cache.get(p)
    if cached and (now - cached[1]) < _API_KEY_CACHE_TTL:
        return cached[0]

    s = _settings()

    if p == "openai":
        result = (s.openai_api_key or os.environ.get("OPENAI_API_KEY", "")).strip()
    elif p == "openrouter":
        result = (s.openrouter_api_key or os.environ.get("OPENROUTER_API_KEY", "")).strip()
    elif p == "gemini":
        result = (
            s.core_llm_api_key
            or os.environ.get("GOOGLE_API_KEY", "")
            or os.environ.get("GEMINI_API_KEY", "")
        ).strip()
    elif p == "kilo":
        result = ""
    elif (spec := get_provider_spec(p)) is not None:
        # OpenAI-compatible providers (deepseek, groq, xai, mistral, ...):
        # key comes from the provider-native env var(s) declared in the registry.
        result = ""
        for env_key in spec.env_keys:
            result = os.environ.get(env_key, "").strip()
            if result:
                break
    else:
        result = (s.core_llm_api_key or "").strip()

    _api_key_cache[p] = (result, now)
    return result

# Retry configuration
MAX_RETRIES = 3
BASE_DELAY = 1.0
MAX_DELAY = 30.0
JITTER_FACTOR = 0.25

# Length-cutoff retry (audit 3c44a93f bug #6b): when chat() gets
# finish_reason='length' with no surviving tool-call, double the token budget and
# re-ask so a clipped (unclosed) tool-call block can finish. Mirrors chat_json.
_MAX_LENGTH_RETRIES = 2
_MAX_LENGTH_RETRY_TOKENS = 32768

# Per-call timeout: prevents individual LLM calls from hanging forever.
# Per-model timeout map: models known to be fast get shorter timeouts to fail-fast.
# Unlisted models use the default (900s) which accommodates deep-thinking models.
_MODEL_TIMEOUTS: dict[str, int] = {
    "gpt-4o-mini": 60,
    "gpt-4o": 120,
    "gpt-4.1-mini": 60,
    "gpt-4.1": 120,
    "gemini-2.0-flash": 90,
    "gemini-2.5-flash": 90,
    "claude-haiku": 60,
    "claude-sonnet": 180,
}
LLM_CALL_TIMEOUT = 900  # Default for deep-thinking models (Opus, o1, complex tool calls)


def _get_timeout_for_model(model: str) -> int:
    """Return timeout seconds based on model capability tier."""
    model_lower = model.lower()
    for pattern, timeout in _MODEL_TIMEOUTS.items():
        if pattern in model_lower:
            return timeout
    return LLM_CALL_TIMEOUT

# Global singletons
_rate_limit_tracker: Optional[RateLimitTracker] = None
_prompt_cache_optimizer: Optional[PromptCacheOptimizer] = None
_core_llm_singleton: Optional["LLMClient"] = None
_agent_llm_singleton: Optional["LLMClient"] = None
_singleton_lock = threading.RLock()


def get_rate_limit_tracker() -> RateLimitTracker:
    global _rate_limit_tracker
    if _rate_limit_tracker is None:
        with _singleton_lock:
            if _rate_limit_tracker is None:
                _rate_limit_tracker = RateLimitTracker()
    return _rate_limit_tracker


def get_prompt_cache_optimizer() -> PromptCacheOptimizer:
    global _prompt_cache_optimizer
    if _prompt_cache_optimizer is None:
        with _singleton_lock:
            if _prompt_cache_optimizer is None:
                _prompt_cache_optimizer = PromptCacheOptimizer()
    return _prompt_cache_optimizer


def _create_client_for_provider(
    provider: str,
    api_key: str = "",
    base_url: str = "",
    model: str = "",
) -> tuple[AsyncOpenAI, str]:
    """
    Tạo AsyncOpenAI client phù hợp với provider.

    Returns: (client, resolved_model)
    """
    provider = (provider or "").lower().strip()
    resolved_key = _resolve_api_key(provider, api_key)

    if provider == "bedrock":
        try:
            import litellm
            litellm.set_verbose = False
            litellm.modify_params = True  # Add dummy tool for Bedrock when tools not specified
            # Drop params a given model rejects (litellm is the per-model authority).
            # e.g. Claude Opus 4.x / reasoning models only accept temperature=1 — sending
            # 0.7/0.3 raises UnsupportedParamsError. Letting litellm drop the unsupported
            # value falls back to the model's default (=1) instead of failing the call.
            litellm.drop_params = True
            client = AsyncOpenAI(
                api_key="bedrock",
                base_url="http://localhost:4000/v1" if not base_url else base_url,
            )
            # Auto-add cross-region inference prefix for newer Claude models
            # Required: us.anthropic.claude-opus-4-6-v1 (not anthropic.claude-opus-4-6-v1)
            bedrock_model = model
            if bedrock_model.startswith("anthropic.claude-") and not bedrock_model.startswith("us."):
                bedrock_model = f"us.{bedrock_model}"
                logger.info("bedrock_cross_region", original=model, resolved=bedrock_model)
            resolved_model = f"bedrock/{bedrock_model}" if not bedrock_model.startswith("bedrock/") else bedrock_model
            return client, resolved_model
        except ImportError:
            logger.warning("litellm_not_installed",
                           message="Install litellm for Bedrock support: pip install litellm")
            client = AsyncOpenAI(
                api_key=resolved_key or "bedrock",
                base_url=base_url or "https://bedrock-runtime.us-east-1.amazonaws.com",
            )
            return client, model

    elif provider == "openai":
        client = AsyncOpenAI(
            api_key=resolved_key,
            base_url=base_url or "https://api.openai.com/v1",
        )
        return client, model

    elif provider == "gemini":
        client = AsyncOpenAI(
            api_key=resolved_key,
            base_url=base_url or "https://generativelanguage.googleapis.com/v1beta/openai",
        )
        return client, model

    elif provider == "openrouter":
        client = AsyncOpenAI(
            api_key=resolved_key,
            base_url=base_url or "https://openrouter.ai/api/v1",
            default_headers={
                "HTTP-Referer": "https://mekongai-nexus.local",
                "X-Title": "MekongAI Nexus",
            },
        )
        return client, model

    elif provider == "local":
        client = AsyncOpenAI(
            api_key="not-needed",
            base_url=base_url or "http://localhost:11434/v1",
        )
        return client, model

    elif provider == "kilo":
        client = AsyncOpenAI(
            api_key="anonymous",
            base_url=base_url or "https://api.kilo.ai/api/gateway/v1",
        )
        return client, model

    elif provider == "pollinations":
        client = AsyncOpenAI(
            api_key="not-needed",
            base_url=base_url or "https://text.pollinations.ai/openai",
        )
        return client, model

    elif provider == "g4f":
        # Import and return G4FClient for g4f provider
        from app.services.g4f_client import G4FClient
        return G4FClient(model=model), model

    elif (spec := get_provider_spec(provider)) is not None:
        # OpenAI-compatible providers from the registry (deepseek, groq, xai,
        # mistral, together, fireworks, cerebras, moonshot, deepinfra, dashscope).
        # All speak the OpenAI protocol — only the base_url differs.
        client = AsyncOpenAI(
            api_key=resolved_key,
            base_url=base_url or spec.base_url,
        )
        return client, model

    else:
        client = AsyncOpenAI(
            api_key=resolved_key,
            base_url=base_url or "https://api.openai.com/v1",
        )
        return client, model


def _compute_backoff_delay(attempt: int) -> float:
    """Exponential backoff with jitter."""
    delay = min(BASE_DELAY * (2 ** attempt), MAX_DELAY)
    jitter = delay * JITTER_FACTOR * random.random()
    return delay + jitter


class LLMClient:
    """Async LLM client with retry, failover, and prompt cache support."""

    # Global concurrency limiter for all LLM API calls system-wide.
    # Lazily initialized to avoid event loop issues (must be created within a running loop).
    _global_semaphore: asyncio.Semaphore | None = None
    _semaphore_limit: int = 5

    @classmethod
    def _get_semaphore(cls) -> asyncio.Semaphore:
        """Get or create the global LLM concurrency semaphore (lazy init)."""
        if cls._global_semaphore is None:
            limit = getattr(_settings(), 'max_concurrent_llm_calls', 5)
            cls._global_semaphore = asyncio.Semaphore(limit)
            cls._semaphore_limit = limit
        return cls._global_semaphore

    def __init__(
        self,
        provider: str = "bedrock",
        api_key: str = "",
        base_url: str = "",
        model: str = "",
        name: str = "default",
    ):
        self.provider = provider
        self.name = name
        self.api_key = api_key
        self.base_url = base_url
        self.client, self.model = _create_client_for_provider(
            provider=provider, api_key=api_key, base_url=base_url, model=model,
        )
        self._tracker = get_rate_limit_tracker()
        self._cache_optimizer = get_prompt_cache_optimizer()
        self._timeout = _get_timeout_for_model(self.model)

    async def chat(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int = 4096,
        tools: Optional[list[dict[str, Any]]] = None,
        tool_choice: Optional[str] = None,
        response_format: Optional[dict] = None,
        max_retries: int = MAX_RETRIES,
    ) -> dict[str, Any]:
        """Send a chat completion request with retry and error recovery.

        FIX-3: When error_classifier returns should_compress=True (context overflow),
        automatically trim messages before retrying instead of retrying with same
        oversized context (which would always fail again).
        """
        # Circuit breaker check: reject immediately if this (provider, model) is tripped
        breaker = get_circuit_breaker()
        if not breaker.can_call(self.provider, model=self.model):
            raise CircuitOpenError(self.provider, model=self.model)

        # Reset per-call state
        self._tool_flatten_attempted = False

        # Strip internal metadata fields (e.g. _protected) that LLM APIs reject
        api_messages = [
            {k: v for k, v in m.items() if not k.startswith("_")}
            for m in messages
        ]
        # Sanitize tool_use/tool_result pairing (prevents Bedrock 400 errors)
        api_messages = self._sanitize_tool_messages(api_messages)
        # Apply Anthropic cache control if applicable
        if is_anthropic_model(self.model, self.provider):
            api_messages = apply_anthropic_cache_control(api_messages)

        # Sort tools deterministically for cache stability
        api_tools = tools
        if tools:
            api_tools = self._cache_optimizer.sort_tool_definitions(tools)

        # Prompted (text-based) tool-calling for providers without native
        # function-calling (g4f, Pollinations, ...). We describe the tools in
        # the prompt and parse the model's text response back into tool_calls,
        # so weak/free providers can still act instead of only "thinking".
        prompted_tool_names: set[str] = set()
        if tools and not supports_native_tools(self.provider, self.model):
            # Render any accumulated tool_call/tool-result history as text the
            # prompted provider can read, then describe the tools in-prompt.
            api_messages = flatten_tool_messages(api_messages)
            api_messages = self._inject_tool_instructions(api_messages, tools)
            prompted_tool_names = {
                (t.get("function", t) or {}).get("name")
                for t in tools
                if (t.get("function", t) or {}).get("name")
            }
            api_tools = None  # provider can't consume native tool schemas

        last_error: Optional[Exception] = None
        # Length-cutoff retry budget (audit 3c44a93f bug #6b). Separate from the
        # infra-error retries: a truncated response is a SUCCESSFUL call whose
        # output was clipped at max_tokens — doubling the budget and re-asking
        # lets the model finish an otherwise-dropped tool-call block. Bounded so a
        # persistently-verbose turn can't loop forever.
        effective_max_tokens = max_tokens
        _length_retries_left = _MAX_LENGTH_RETRIES

        for attempt in range(max_retries + 1):
            try:
                # Acquire global concurrency semaphore to prevent overwhelming LLM providers
                sem = self._get_semaphore()
                if sem.locked():
                    logger.warning(
                        "llm_semaphore_full",
                        limit=self._semaphore_limit,
                        client=self.name,
                        provider=self.provider,
                    )
                async with sem:
                    result = await self._do_chat_call(
                        api_messages, temperature, effective_max_tokens, api_tools, tool_choice, response_format,
                    )

                # Prompted tool-calling: extract tool calls the model emitted
                # as text (only when this provider was driven via prompted
                # tools and didn't already return native tool_calls).
                if prompted_tool_names and not result.get("tool_calls"):
                    parsed = parse_tool_calls_from_text(
                        result.get("content", ""), prompted_tool_names
                    )
                    if parsed:
                        result["tool_calls"] = parsed

                # Length-cutoff retry: the response was clipped at max_tokens
                # (finish_reason='length') and no tool-call survived. Retrying with
                # a bigger budget lets the model finish — without this, an unclosed
                # <invoke> block is silently dropped (Turn-4 narration failure).
                # If a tool-call DID survive, downstream can act, so don't retry.
                if (
                    result.get("finish_reason") == "length"
                    and not result.get("tool_calls")
                    and _length_retries_left > 0
                ):
                    _length_retries_left -= 1
                    _new_max = min(effective_max_tokens * 2, _MAX_LENGTH_RETRY_TOKENS)
                    logger.warning(
                        "llm_length_cutoff_retry",
                        client=self.name,
                        old_max_tokens=effective_max_tokens,
                        new_max_tokens=_new_max,
                        content_length=len(result.get("content", "")),
                    )
                    if _new_max > effective_max_tokens:
                        effective_max_tokens = _new_max
                        if result.get("usage"):
                            self._cache_optimizer.observe_cache_usage(result["usage"])
                        breaker.record_success(self.provider, model=self.model)
                        await asyncio.sleep(0.3)
                        continue

                # Observe prompt cache usage
                if result.get("usage"):
                    self._cache_optimizer.observe_cache_usage(result["usage"])

                # Circuit breaker: record success
                breaker.record_success(self.provider, model=self.model)
                return result

            except Exception as e:
                last_error = e
                classified = classify_api_error(
                    e,
                    provider=self.provider,
                    model=self.model,
                    num_messages=len(messages),
                )

                # Circuit breaker: record failure (only trips for infra errors)
                breaker.record_failure(self.provider, e, classified.reason, model=self.model)

                logger.warning(
                    "llm_call_error",
                    client=self.name,
                    attempt=attempt + 1,
                    max_retries=max_retries,
                    reason=classified.reason.value,
                    status_code=classified.status_code,
                    retryable=classified.retryable,
                    message=classified.message[:200],
                )

                # ════════════════════════════════════════════════════════════
                # This makes retries actually useful instead of repeating the same
                # oversized request that will always fail.
                # ════════════════════════════════════════════════════════════
                if getattr(classified, 'should_compress', False):
                    logger.info(
                        "llm_context_overflow_compress_retry",
                        client=self.name,
                        attempt=attempt + 1,
                        original_messages=len(api_messages),
                    )
                    api_messages = self._emergency_context_trim(api_messages)
                    # Also update messages reference for subsequent retries
                    messages = api_messages
                    logger.info(
                        "llm_context_trimmed",
                        remaining_messages=len(api_messages),
                    )
                    # Short delay then retry with trimmed context
                    await asyncio.sleep(1.0)
                    continue

                # Tool format conversion error: flatten tool messages and retry.
                # This happens when messages contain tool_call_ids from a different
                # provider family (e.g. Kilo's "chatcmpl-tool-xxx" in Bedrock context).
                _is_tool_format_error = (
                    "tool call" in str(e).lower()
                    or "_convert_to_bedrock_tool" in str(e)
                    or "tool_call_invoke" in str(e).lower()
                )
                if _is_tool_format_error and not getattr(self, "_tool_flatten_attempted", False):
                    self._tool_flatten_attempted = True
                    from app.core.provider_fallback import FallbackLLMClient
                    api_messages = FallbackLLMClient._flatten_tool_messages(api_messages)
                    messages = api_messages
                    # Strip tools — flattened context can't use structured tools
                    api_tools = None
                    tool_choice = None
                    logger.warning(
                        "llm_tool_format_error_flattened",
                        client=self.name,
                        provider=self.provider,
                        error=str(e)[:200],
                    )
                    await asyncio.sleep(1.0)
                    continue

                # Non-retryable: bail immediately
                if not classified.retryable:
                    break

                # Last attempt: bail
                if attempt >= max_retries:
                    break

                # Backoff and retry
                delay = _compute_backoff_delay(attempt)

                # Rate limit: longer delay + capture Retry-After for smart wait
                if classified.reason == FailoverReason.rate_limit:
                    delay = max(delay, 5.0)
                    retry_after = self._extract_retry_after(e)
                    self._tracker.record_rate_limit_hit(self.provider, retry_after)

                await asyncio.sleep(delay)

        # Classify the last error to set appropriate ErrorCode
        from app.core.error_codes import ErrorCode as EC, classify_error
        error_code = EC.LLM_TIMEOUT  # default
        is_retryable = False
        if last_error:
            classification = classify_error(last_error)
            is_retryable = classification.retryable
            if classification.category.value == "llm":
                msg_lower = str(last_error).lower()
                if "rate" in msg_lower:
                    error_code = EC.LLM_RATE_LIMITED
                elif "overload" in msg_lower:
                    error_code = EC.LLM_OVERLOADED
                elif "context" in msg_lower:
                    error_code = EC.LLM_CONTEXT_OVERFLOW

        raise LLMError(
            f"LLM call failed after {max_retries + 1} attempts ({self.name}/{self.provider}): {last_error}",
            code=error_code,
            retryable=is_retryable,
        ) from last_error

    @staticmethod
    def _inject_tool_instructions(
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Append prompted tool-calling instructions to the system message.

        Used for providers without native function-calling: the tools and the
        exact call format are described in-prompt so the model knows how to
        act. Falls back to prepending a system message if none exists.
        """
        instructions = build_tool_instructions(tools)
        if not instructions:
            return messages

        result = list(messages)
        for i, msg in enumerate(result):
            if msg.get("role") == "system":
                content = msg.get("content", "") or ""
                result[i] = {**msg, "content": f"{content}\n\n{instructions}"}
                return result

        # No system message — prepend one.
        return [{"role": "system", "content": instructions}, *result]

    @staticmethod
    def _extract_retry_after(error: Exception) -> Optional[float]:
        """Extract Retry-After seconds from error response headers or body."""
        # Try response headers
        response = getattr(error, "response", None)
        if response is not None:
            headers = getattr(response, "headers", {})
            if headers:
                ra = headers.get("retry-after") or headers.get("Retry-After")
                if ra:
                    try:
                        return float(ra)
                    except (ValueError, TypeError):
                        pass
        # Try extracting from error message ("try again in X seconds")
        import re
        msg = str(error).lower()
        match = re.search(r"try again in (\d+(?:\.\d+)?)\s*(?:s|sec|second)", msg)
        if match:
            return float(match.group(1))
        match = re.search(r"retry after (\d+(?:\.\d+)?)\s*(?:s|sec|second)?", msg)
        if match:
            return float(match.group(1))
        return None

    @staticmethod
    def _emergency_context_trim(messages: list[dict]) -> list[dict]:
        """Emergency context trim for overflow recovery.

        Strategy: Keep system prompt + first user message (task objective) +
        last 40% of remaining messages. Preserves the original goal context
        that agents need for coherence while trimming the middle history.

        This is a fast, non-LLM-powered trim for use inside the retry loop.
        """
        if not messages or len(messages) <= 4:
            return messages

        # Always keep system prompt
        anchored = [messages[0]]

        # Keep first user message (contains the original task/objective)
        remaining = messages[1:]
        first_user_idx = next(
            (i for i, m in enumerate(remaining) if m.get("role") == "user"),
            None,
        )
        if first_user_idx is not None:
            anchored.append(remaining[first_user_idx])
            remaining = remaining[:first_user_idx] + remaining[first_user_idx + 1:]

        # Keep last 40% of the rest (minimum 3)
        keep_count = max(3, len(remaining) * 40 // 100)
        # Adjust split point: never start at a "tool" message (orphan result)
        split_idx = len(remaining) - keep_count
        while split_idx < len(remaining) and remaining[split_idx].get("role") == "tool":
            split_idx -= 1
        if split_idx < 0:
            split_idx = 0
        trimmed = remaining[split_idx:]

        # Truncate extremely long content in kept messages
        result = list(anchored)
        for msg in trimmed:
            content = msg.get("content", "") or ""
            if len(content) > 15000:
                truncated = content[:8000] + "\n...[trimmed for context limit]...\n" + content[-4000:]
                result.append({**msg, "content": truncated})
            else:
                result.append(msg)

        logger.info(
            "emergency_trim_applied",
            original=len(messages),
            kept=len(result),
        )
        return LLMClient._sanitize_tool_messages(result)

    @staticmethod
    def _sanitize_tool_messages(messages: list[dict]) -> list[dict]:
        """Ensure tool_use/tool_result pairing is consistent after trimming.

        Bedrock requires: every tool_result must reference a tool_use from
        the preceding assistant turn, and vice versa. After context trimming,
        orphans can appear in either direction. This removes orphan tool_results
        and adds dummy results for orphan tool_uses.
        """
        tool_use_ids: set[str] = set()
        for msg in messages:
            if msg.get("role") == "assistant":
                for tc in msg.get("tool_calls", []):
                    tc_id = tc.get("id", "")
                    if tc_id:
                        tool_use_ids.add(tc_id)

        tool_result_ids: set[str] = set()
        for msg in messages:
            if msg.get("role") == "tool":
                tc_id = msg.get("tool_call_id", "")
                if tc_id:
                    tool_result_ids.add(tc_id)

        orphan_use_ids = tool_use_ids - tool_result_ids
        orphan_result_ids = tool_result_ids - tool_use_ids

        if not orphan_use_ids and not orphan_result_ids:
            return messages

        result: list[dict] = []
        for msg in messages:
            if msg.get("role") == "tool":
                tc_id = msg.get("tool_call_id", "")
                if tc_id in orphan_result_ids:
                    continue
            result.append(msg)
            if msg.get("role") == "assistant" and orphan_use_ids:
                for tc in msg.get("tool_calls", []):
                    tc_id = tc.get("id", "")
                    if tc_id in orphan_use_ids:
                        result.append({
                            "role": "tool",
                            "tool_call_id": tc_id,
                            "content": "[trimmed]",
                        })
                        orphan_use_ids.discard(tc_id)

        return result

    @staticmethod
    def _recover_leaked_tool_calls(
        result: dict[str, Any],
        content: Optional[str],
        tools: Optional[list[dict[str, Any]]],
    ) -> None:
        """Recover a tool call the model leaked into prose `content` (phantom-write).

        A provider sometimes emits a tool call as XML ``<invoke>`` / a ``<tool_call>``
        JSON blob in `content` while leaving the native tool_calls slot empty. This
        recovers it in place so every downstream consumer sees a uniform shape.
        Shared by both the non-streaming and streaming paths so they can't drift
        (E9). No-op when: tool_calls already present, no content, no tools offered,
        or the turn was content-filtered (never execute filtered prose).
        """
        if result.get("tool_calls") or not content or not tools:
            return
        _fr = str(result.get("finish_reason") or "").lower()
        if _fr in SAFETY_FINISH_REASONS:
            return
        _valid = {
            name
            for t in tools
            if (name := (t.get("function", t) or {}).get("name"))
        }
        if not _valid:
            return
        _recovered = recover_tool_calls(content, _valid)
        if _recovered:
            result["tool_calls"] = _recovered
            logger.warning(
                "toolcall_recovered_at_boundary",
                count=len(_recovered),
                tools=[c["function"]["name"] for c in _recovered],
            )

    async def _do_chat_call(
        self,
        messages: list[dict],
        temperature: float,
        max_tokens: int,
        tools: Optional[list[dict]],
        tool_choice: Optional[str],
        response_format: Optional[dict],
    ) -> dict[str, Any]:
        """Execute a single chat completion call."""
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if tools:
            kwargs["tools"] = tools
        if tool_choice:
            kwargs["tool_choice"] = tool_choice
        if response_format:
            kwargs["response_format"] = response_format

        # g4f provider uses its own client interface (not OpenAI-compatible)
        if self.provider == "g4f":
            from app.services.g4f_client import G4FClient
            if isinstance(self.client, G4FClient):
                return await self.client.chat(
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    tools=tools,
                )

        # Bedrock via litellm — with per-call timeout to prevent infinite hang
        timeout = self._timeout
        if self.provider == "bedrock":
            try:
                import litellm
                logger.debug(
                    "llm_call_start",
                    provider=self.provider,
                    model=self.model,
                    timeout=timeout,
                )
                response = await asyncio.wait_for(
                    litellm.acompletion(**kwargs),
                    timeout=timeout,
                )
            except ImportError:
                response = await asyncio.wait_for(
                    self.client.chat.completions.create(**kwargs),
                    timeout=timeout,
                )
            except asyncio.TimeoutError:
                raise LLMError(
                    f"LLM call timed out after {timeout}s "
                    f"(provider={self.provider}, model={self.model})"
                )
        else:
            try:
                response = await asyncio.wait_for(
                    self.client.chat.completions.create(**kwargs),
                    timeout=timeout,
                )
            except asyncio.TimeoutError:
                raise LLMError(
                    f"LLM call timed out after {timeout}s "
                    f"(provider={self.provider}, model={self.model})"
                )

        # Update rate limit tracker from response headers
        raw_response = getattr(response, "_response", None) or getattr(response, "response", None)
        if raw_response and hasattr(raw_response, "headers"):
            self._tracker.update(self.provider, raw_response.headers)

        choice = response.choices[0]
        content = choice.message.content
        # Some free providers (Kilo/Poolside) return content=null when all tokens
        # are consumed by reasoning. Fall back to reasoning text if available.
        if content is None:
            reasoning = getattr(choice.message, "reasoning", None)
            if reasoning and isinstance(reasoning, str):
                content = reasoning
            else:
                content = ""
        result: dict[str, Any] = {
            "content": content,
            "role": choice.message.role,
            "finish_reason": choice.finish_reason,
            "usage": {
                "prompt_tokens": response.usage.prompt_tokens if response.usage else 0,
                "completion_tokens": response.usage.completion_tokens if response.usage else 0,
                "total_tokens": response.usage.total_tokens if response.usage else 0,
            },
        }

        # Handle tool calls
        if hasattr(choice.message, "tool_calls") and choice.message.tool_calls:
            result["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": tc.type,
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
                for tc in choice.message.tool_calls
            ]

        # Boundary recovery (audit 2026-06-29): a native provider (bedrock/claude)
        # sometimes leaks a tool call into `content` as XML <invoke> or a JSON blob
        # while leaving the native tool_calls slot empty — the phantom-write mode.
        # Recover it here, once, so every downstream consumer sees a uniform shape.
        self._recover_leaked_tool_calls(result, content, tools)

        # Drop partial tool_calls if the provider stopped for safety reasons.
        from app.services.safety_finish_reason import sanitize_safety_finish

        result = sanitize_safety_finish(result)

        return result

    async def chat_streaming_with_tools(
        self,
        messages: list[dict[str, Any]],
        tools: Optional[list[dict[str, Any]]] = None,
        temperature: float = 0.7,
        max_tokens: int = 16384,
        on_token: Optional[Any] = None,  # async callable(str) -> None
    ) -> dict[str, Any]:
        """Stream chat completion WITH tool call support, emitting content tokens real-time.

        Returns the same dict format as chat() — but calls `on_token(chunk)` for
        each content delta as it arrives. This lets the frontend show LLM reasoning
        in real-time even during tool-calling rounds.

        If LLM returns tool_calls, they are accumulated from streaming deltas and
        returned in result["tool_calls"] exactly like non-streaming chat().
        """
        # Strip internal metadata fields before sending to LLM API
        clean_messages = [
            {k: v for k, v in m.items() if not k.startswith("_")}
            for m in messages
        ]

        # Apply Anthropic cache control (same as non-streaming path)
        if is_anthropic_model(self.model, self.provider):
            clean_messages = apply_anthropic_cache_control(clean_messages)

        # Sort tools deterministically for cache stability
        api_tools = tools
        if tools:
            api_tools = self._cache_optimizer.sort_tool_definitions(tools)

        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": clean_messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
        }
        if api_tools:
            kwargs["tools"] = api_tools

        # Accumulators
        full_content = ""
        tool_calls_acc: dict[int, dict[str, Any]] = {}  # index → {id, function: {name, arguments}}
        finish_reason = None

        # Per-model timeout (fast models fail-fast) — mirrors non-streaming chat() path.
        call_timeout = self._timeout

        try:
            # g4f doesn't support streaming with tools — fallback to non-streaming
            if self.provider == "g4f":
                from app.services.g4f_client import G4FClient
                if isinstance(self.client, G4FClient):
                    result = await self.client.chat(
                        messages=clean_messages,
                        temperature=temperature,
                        max_tokens=max_tokens,
                        tools=api_tools,
                    )
                    full_content = result.get("content", "")
                    if on_token and full_content:
                        await on_token(full_content)
                    return result

            if self.provider == "bedrock":
                try:
                    import litellm
                    stream = await asyncio.wait_for(
                        litellm.acompletion(**kwargs),
                        timeout=call_timeout,
                    )
                except ImportError:
                    stream = await asyncio.wait_for(
                        self.client.chat.completions.create(**kwargs),
                        timeout=call_timeout,
                    )
                except asyncio.TimeoutError:
                    raise LLMError(
                        f"LLM streaming call timed out after {call_timeout}s "
                        f"(provider={self.provider}, model={self.model})"
                    )
            else:
                try:
                    stream = await asyncio.wait_for(
                        self.client.chat.completions.create(**kwargs),
                        timeout=call_timeout,
                    )
                except asyncio.TimeoutError:
                    raise LLMError(
                        f"LLM streaming call timed out after {call_timeout}s "
                        f"(provider={self.provider}, model={self.model})"
                    )

            import time as _time
            _stream_start = _time.monotonic()
            _STREAM_ITER_TIMEOUT = call_timeout

            _reasoning_acc = ""  # Accumulate reasoning for Kilo/Poolside fallback

            async for chunk in stream:
                if _time.monotonic() - _stream_start > _STREAM_ITER_TIMEOUT:
                    raise LLMError(
                        f"LLM stream iteration timed out after {_STREAM_ITER_TIMEOUT}s "
                        f"(provider={self.provider}, model={self.model})"
                    )
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                finish_reason = chunk.choices[0].finish_reason or finish_reason

                # Content tokens → emit real-time
                if delta.content:
                    full_content += delta.content
                    if on_token:
                        try:
                            await on_token(delta.content)
                        except Exception:
                            pass

                # Capture reasoning tokens (Kilo/Poolside models)
                reasoning = getattr(delta, "reasoning", None)
                if reasoning and isinstance(reasoning, str):
                    _reasoning_acc += reasoning

                # Tool call deltas → accumulate
                if hasattr(delta, "tool_calls") and delta.tool_calls:
                    for tc_delta in delta.tool_calls:
                        idx = tc_delta.index
                        if idx not in tool_calls_acc:
                            tool_calls_acc[idx] = {
                                "id": tc_delta.id or f"call_{idx}",
                                "type": "function",
                                "function": {"name": "", "arguments": ""},
                            }
                        if tc_delta.id:
                            tool_calls_acc[idx]["id"] = tc_delta.id
                        if tc_delta.function:
                            if tc_delta.function.name:
                                tool_calls_acc[idx]["function"]["name"] += tc_delta.function.name
                            if tc_delta.function.arguments:
                                tool_calls_acc[idx]["function"]["arguments"] += tc_delta.function.arguments

        except Exception as e:
            logger.error("chat_streaming_with_tools_failed", error=str(e))
            raise LLMError(f"Streaming chat failed ({self.name}): {e}")

        # If no content was emitted but reasoning was captured, use reasoning as content
        if not full_content and _reasoning_acc:
            full_content = _reasoning_acc
            if on_token:
                try:
                    await on_token(full_content)
                except Exception:
                    pass

        # Build result in same format as chat()
        result: dict[str, Any] = {
            "content": full_content,
            "role": "assistant",
            "finish_reason": finish_reason or "stop",
            "usage": {},  # Usage not reliably available in streaming
        }

        if tool_calls_acc:
            validated_tool_calls: list[dict[str, Any]] = []
            for idx in sorted(tool_calls_acc.keys()):
                tc = tool_calls_acc[idx]
                fn_name = tc.get("function", {}).get("name", "").strip()
                fn_args = tc.get("function", {}).get("arguments", "")

                # Skip tool calls with empty function name
                if not fn_name:
                    logger.warning(
                        "streaming_tool_call_empty_name",
                        index=idx,
                        client=self.name,
                    )
                    continue

                # Validate that arguments are parseable JSON (or empty object)
                args_str = fn_args.strip() if fn_args else "{}"
                if not args_str:
                    args_str = "{}"
                try:
                    json.loads(args_str)
                except (json.JSONDecodeError, TypeError):
                    logger.warning(
                        "streaming_tool_call_invalid_args",
                        index=idx,
                        tool_name=fn_name,
                        args_preview=args_str[:100],
                        client=self.name,
                    )
                    # Use empty args instead of dropping — lets executor return proper error
                    tc["function"]["arguments"] = "{}"

                validated_tool_calls.append(tc)

            if validated_tool_calls:
                result["tool_calls"] = validated_tool_calls
            elif full_content:
                # All tool_calls were invalid but we have content — return as text response
                logger.warning(
                    "streaming_all_tool_calls_invalid",
                    original_count=len(tool_calls_acc),
                    client=self.name,
                )
            else:
                # No valid tool_calls and no content — likely a broken stream
                logger.error(
                    "streaming_broken_no_valid_output",
                    tool_calls_attempted=len(tool_calls_acc),
                    client=self.name,
                )

        # Boundary recovery (E9): the streaming path leaks tool calls into prose
        # `content` too — recover with the SAME helper as _do_chat_call so a
        # `<tool_call>` a model streams into content is executed, not phantom-written.
        self._recover_leaked_tool_calls(result, full_content, tools)

        # Drop partial tool_calls if the provider stopped for safety reasons.
        from app.services.safety_finish_reason import sanitize_safety_finish

        result = sanitize_safety_finish(result)

        return result

    async def chat_stream(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> AsyncGenerator[str, None]:
        """Stream a chat completion response, yielding content delta chunks."""
        import time as _time
        prompt_tokens = 0
        completion_tokens = 0

        # Per-model timeout (fast models fail-fast) — mirrors non-streaming chat() path.
        call_timeout = self._timeout

        try:
            _stream_start = _time.monotonic()

            # g4f provider uses its own streaming interface
            if self.provider == "g4f":
                from app.services.g4f_client import G4FClient
                if isinstance(self.client, G4FClient):
                    async for chunk in self.client.chat_stream(
                        messages=messages, temperature=temperature, max_tokens=max_tokens,
                    ):
                        yield chunk
                    return

            if self.provider == "bedrock":
                try:
                    import litellm
                    response = await litellm.acompletion(
                        model=self.model, messages=messages,
                        temperature=temperature, max_tokens=max_tokens,
                        stream=True, stream_options={"include_usage": True},
                    )
                    async for chunk in response:
                        if _time.monotonic() - _stream_start > call_timeout:
                            raise LLMError(f"LLM stream timed out after {call_timeout}s")
                        if chunk.choices and chunk.choices[0].delta.content:
                            yield chunk.choices[0].delta.content
                        if hasattr(chunk, "usage") and chunk.usage:
                            prompt_tokens = getattr(chunk.usage, "prompt_tokens", 0)
                            completion_tokens = getattr(chunk.usage, "completion_tokens", 0)
                    return
                except ImportError:
                    pass

            # Kilo/Pollinations don't support stream_options
            stream_kwargs = {
                "model": self.model, "messages": messages,
                "temperature": temperature, "max_tokens": max_tokens,
                "stream": True,
            }
            if self.provider not in ("kilo", "pollinations"):
                stream_kwargs["stream_options"] = {"include_usage": True}

            stream = await self.client.chat.completions.create(**stream_kwargs)
            _has_content = False
            _reasoning_acc = ""
            async for chunk in stream:
                if _time.monotonic() - _stream_start > call_timeout:
                    raise LLMError(f"LLM stream timed out after {call_timeout}s")
                if chunk.choices and chunk.choices[0].delta.content:
                    _has_content = True
                    yield chunk.choices[0].delta.content
                # Capture reasoning tokens (Kilo/Poolside models)
                if chunk.choices:
                    reasoning = getattr(chunk.choices[0].delta, "reasoning", None)
                    if reasoning and isinstance(reasoning, str):
                        _reasoning_acc += reasoning
                if hasattr(chunk, "usage") and chunk.usage:
                    prompt_tokens = getattr(chunk.usage, "prompt_tokens", 0)
                    completion_tokens = getattr(chunk.usage, "completion_tokens", 0)

            # If no content was streamed but reasoning was captured, yield reasoning
            if not _has_content and _reasoning_acc:
                yield _reasoning_acc

        except Exception as e:
            logger.error("llm_stream_failed", client=self.name, error=str(e))
            raise LLMError(f"LLM stream failed ({self.name}): {e}")

    async def chat_json(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.3,
        max_tokens: int = 4096,
        max_json_retries: int = 2,
    ) -> dict[str, Any]:
        """Chat và parse response thành JSON.

        Robust strategy:
        1. Request JSON via response_format if provider supports it
        2. If finish_reason="length" (truncated) → retry with 2x max_tokens
        3. On parse failure → try JSON repair (close unclosed brackets/strings)
        4. If still fails → retry (LLM output is non-deterministic)

        Args:
            messages: Chat messages.
            temperature: LLM temperature.
            max_tokens: Max tokens for LLM response.
            max_json_retries: Number of retries on JSONDecodeError.
        """
        json_messages = list(messages)
        if json_messages and json_messages[0]["role"] == "system":
            json_messages[0]["content"] += "\n\nIMPORTANT: Return ONLY valid JSON."

        last_error: Optional[Exception] = None
        current_max_tokens = max_tokens

        for attempt in range(max_json_retries + 1):
            result = await self.chat(
                messages=json_messages,
                temperature=temperature,
                max_tokens=current_max_tokens,
                response_format={"type": "json_object"},
            )
            content = result.get("content", "")
            finish_reason = result.get("finish_reason", "stop")

            # Detect truncation: finish_reason="length" means max_tokens hit
            if finish_reason == "length" and attempt < max_json_retries:
                current_max_tokens = min(current_max_tokens * 2, 32768)
                logger.warning(
                    "chat_json_truncated_retry",
                    client=self.name,
                    attempt=attempt + 1,
                    old_max_tokens=current_max_tokens // 2,
                    new_max_tokens=current_max_tokens,
                    content_length=len(content),
                )
                await asyncio.sleep(0.5)
                continue

            try:
                return self._parse_json_content(content)
            except (json.JSONDecodeError, TypeError) as e:
                # Try JSON repair before giving up
                repaired = self._repair_truncated_json(content)
                if repaired is not None:
                    logger.info(
                        "chat_json_repaired",
                        client=self.name,
                        attempt=attempt + 1,
                        content_length=len(content),
                    )
                    return repaired

                last_error = e
                logger.warning(
                    "chat_json_parse_failed",
                    client=self.name,
                    attempt=attempt + 1,
                    max_retries=max_json_retries,
                    finish_reason=finish_reason,
                    content_length=len(content),
                    error=str(e)[:120],
                )
                if attempt < max_json_retries:
                    await asyncio.sleep(1.0)
                    continue

        raise last_error  # type: ignore[misc]

    @staticmethod
    def _fix_literal_newlines_in_json(content: str) -> Optional[str]:
        """Replace literal newlines inside JSON string values with \\n.

        Handles LLM outputs that contain unescaped newlines within strings.
        """
        try:
            result = []
            in_str = False
            escape_next = False
            for ch in content:
                if escape_next:
                    result.append(ch)
                    escape_next = False
                    continue
                if ch == '\\':
                    escape_next = True
                    result.append(ch)
                    continue
                if ch == '"':
                    in_str = not in_str
                    result.append(ch)
                    continue
                if in_str and ch == '\n':
                    result.append('\\n')
                    continue
                if in_str and ch == '\r':
                    result.append('\\r')
                    continue
                if in_str and ch == '\t':
                    result.append('\\t')
                    continue
                result.append(ch)
            return ''.join(result)
        except Exception:
            return None

    @staticmethod
    def _parse_json_content(content: str) -> dict[str, Any]:
        """Parse LLM response content as JSON, handling common formatting issues."""
        try:
            return json.loads(content)
        except (json.JSONDecodeError, TypeError):
            pass

        # Try fixing literal newlines in string values
        fixed = LLMClient._fix_literal_newlines_in_json(content)
        if fixed:
            try:
                return json.loads(fixed)
            except (json.JSONDecodeError, TypeError):
                pass

        # Extract from markdown code blocks
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0]
        elif "```" in content:
            content = content.split("```")[1].split("```")[0]
        # Try extracting JSON object or array from mixed content
        obj_start = content.find("{")
        arr_start = content.find("[")
        # Pick whichever comes first (object or array)
        if arr_start >= 0 and (obj_start < 0 or arr_start < obj_start):
            end = content.rfind("]") + 1
            if end > arr_start:
                content = content[arr_start:end]
        elif obj_start >= 0:
            end = content.rfind("}") + 1
            if end > obj_start:
                content = content[obj_start:end]
        return json.loads(content)

    @staticmethod
    def _repair_truncated_json(content: str) -> Optional[dict[str, Any]]:
        """Attempt to repair truncated JSON by closing unclosed brackets/strings.

        Handles common truncation patterns:
        - Unterminated strings: add closing quote
        - Unclosed objects/arrays: add closing brackets
        - Trailing commas before close

        Returns parsed dict if repair succeeds, None otherwise.
        """
        if not content or not content.strip():
            return None

        # Extract JSON portion first
        text = content.strip()
        obj_start = text.find("{")
        if obj_start < 0:
            return None
        text = text[obj_start:]

        # Try fixing literal newlines first — may resolve the issue without bracket repair
        fixed = LLMClient._fix_literal_newlines_in_json(text)
        if fixed:
            try:
                return json.loads(fixed)
            except (json.JSONDecodeError, TypeError):
                text = fixed  # Use partially fixed version for further repair

        # Track bracket depth to know what to close
        in_string = False
        escape_next = False
        brackets: list[str] = []

        for ch in text:
            if escape_next:
                escape_next = False
                continue
            if ch == "\\":
                if in_string:
                    escape_next = True
                continue
            if ch == '"' and not escape_next:
                in_string = not in_string
                continue
            if in_string:
                continue
            if ch in "{[":
                brackets.append(ch)
            elif ch == "}":
                if brackets and brackets[-1] == "{":
                    brackets.pop()
            elif ch == "]":
                if brackets and brackets[-1] == "[":
                    brackets.pop()

        if not brackets and not in_string:
            # Already balanced — can't help
            return None

        # Build repair suffix
        repair = ""
        if in_string:
            repair += '"'
        # Remove trailing incomplete key-value (e.g. `"key": "incompl`)
        # Close all open brackets in reverse
        for bracket in reversed(brackets):
            if bracket == "{":
                repair += "}"
            else:
                repair += "]"

        repaired_text = text + repair

        # Clean trailing commas before closing brackets: ,} or ,]
        repaired_text = repaired_text.replace(",}", "}").replace(",]", "]")

        try:
            return json.loads(repaired_text)
        except (json.JSONDecodeError, TypeError):
            return None

def get_core_llm() -> "Any":
    """Get LLM client for Core Agent with automatic free-tier fallback.

    Returns FallbackLLMClient with chain: core_provider → kilo → pollinations → g4f.
    Cached singleton for performance.
    """
    global _core_llm_singleton
    if _core_llm_singleton is None:
        with _singleton_lock:
            if _core_llm_singleton is None:
                _core_llm_singleton = get_core_llm_with_fallback()
    return _core_llm_singleton


def get_agent_llm() -> "Any":
    """Get LLM client for sub-agents with automatic free-tier fallback.

    Returns FallbackLLMClient with chain: agent_provider → kilo → pollinations.
    Cached singleton for performance.
    """
    global _agent_llm_singleton
    if _agent_llm_singleton is None:
        with _singleton_lock:
            if _agent_llm_singleton is None:
                _agent_llm_singleton = _build_agent_llm_with_fallback()
    return _agent_llm_singleton


def _build_agent_llm_with_fallback() -> "Any":
    """Build agent LLM with free-tier fallback chain."""
    from app.core.provider_fallback import (
        FallbackLLMClient,
        ProviderConfig,
        ProviderFallbackChain,
    )

    _s = _settings()
    providers: list[ProviderConfig] = []

    # Primary: agent provider
    providers.append(ProviderConfig(
        provider=_s.agent_llm_provider,
        model=_s.agent_llm_model,
        api_key=_resolve_api_key(_s.agent_llm_provider, _s.agent_llm_api_key) or None,
        base_url=_s.agent_llm_base_url or None,
        priority=0,
        max_failures=5,
        cooldown_seconds=15.0,
    ))

    # Fallback: Kilo Gateway free
    if _s.kilo_gateway_enabled:
        providers.append(ProviderConfig(
            provider="kilo",
            model="kilo-auto/free",
            api_key=None,
            base_url=_s.kilo_gateway_base_url,
            priority=1,
            max_failures=10,
            cooldown_seconds=30.0,
        ))

    # Last resort: Pollinations
    if _s.pollination_enabled:
        providers.append(ProviderConfig(
            provider="pollinations",
            model="openai",
            api_key="not-needed",
            base_url=None,
            priority=2,
            max_failures=10,
            cooldown_seconds=60.0,
        ))

    if len(providers) <= 1:
        return LLMClient(
            provider=_s.agent_llm_provider,
            api_key=_resolve_api_key(_s.agent_llm_provider, _s.agent_llm_api_key),
            base_url=_s.agent_llm_base_url,
            model=_s.agent_llm_model,
            name="agent",
        )

    return FallbackLLMClient(fallback_chain=ProviderFallbackChain(providers=providers))


def get_core_llm_with_fallback() -> "Any":
    """
    Get Core LLM with automatic fallback to Agent LLM.

    If core and agent use different providers, automatically creates a
    FallbackLLMClient chain: core_provider → agent_provider.
    Also parses llm_fallback_providers from settings if configured.

    Falls back gracefully to plain LLMClient if only one provider is available.

    Returns:
        FallbackLLMClient (if fallback available) or LLMClient (if single provider).
        Both have compatible .chat(), .chat_stream(), .chat_json() interfaces.
    """
    from app.core.provider_fallback import (
        FallbackLLMClient,
        ProviderConfig,
        ProviderFallbackChain,
    )

    _s = _settings()
    providers: list[ProviderConfig] = []

    # Primary: core provider
    providers.append(ProviderConfig(
        provider=_s.core_llm_provider,
        model=_s.core_llm_model,
        api_key=_resolve_api_key(_s.core_llm_provider, _s.core_llm_api_key),
        base_url=_s.core_llm_base_url or None,
        priority=0,
        max_failures=5,  # Increased: format_error retries shouldn't disable primary
        cooldown_seconds=15.0,  # Shorter cooldown: primary should recover quickly
    ))

    # Secondary: agent provider (if different from core)
    agent_key = f"{_s.agent_llm_provider}:{_s.agent_llm_model}"
    core_key = f"{_s.core_llm_provider}:{_s.core_llm_model}"

    agent_resolved_key = _resolve_api_key(_s.agent_llm_provider, _s.agent_llm_api_key)

    # NOTE: Agent API key may come from provider-native env vars (OPENAI_API_KEY,
    # OPENROUTER_API_KEY, ...), not necessarily AGENT_LLM_API_KEY.
    if agent_key != core_key and (agent_resolved_key or _s.agent_llm_provider.lower() in ("bedrock", "local", "kilo", "pollinations", "g4f")):
        providers.append(ProviderConfig(
            provider=_s.agent_llm_provider,
            model=_s.agent_llm_model,
            api_key=agent_resolved_key,
            base_url=_s.agent_llm_base_url or None,
            priority=1,
            max_failures=3,
            cooldown_seconds=60.0,
        ))

    # Additional: parse llm_fallback_providers from settings
    # Format: "provider:model:api_key,provider:model:api_key,..."
    if _s.llm_fallback_providers:
        for entry in _s.llm_fallback_providers.split(","):
            parts = entry.strip().split(":")
            if len(parts) >= 2:
                fb_provider = parts[0].strip()
                fb_model = parts[1].strip()
                fb_key = parts[2].strip() if len(parts) > 2 else ""
                fb_full = f"{fb_provider}:{fb_model}"
                # Avoid duplicates
                existing_keys = {f"{p.provider}:{p.model}" for p in providers}
                if fb_full not in existing_keys:
                    providers.append(ProviderConfig(
                        provider=fb_provider,
                        model=fb_model,
                        api_key=fb_key or None,
                        priority=len(providers),
                        max_failures=3,
                        cooldown_seconds=60.0,
                    ))

    # ── LAST RESORT: Kilo Gateway free models (always add, anonymous access) ──
    # These are added automatically as ultimate fallbacks - no API key needed
    if _s.kilo_gateway_enabled:
        kilo_free_models = [
            ("kilo-auto/free", "Auto-routed best free model"),
            ("nvidia/nemotron-3-super-120b-a12b:free", "Nemotron 3 Super 120B"),
            ("poolside/laguna-m.1:free", "Poolside Laguna"),
        ]
        for fb_model, fb_desc in kilo_free_models:
            fb_full = f"kilo:{fb_model}"
            existing_keys = {f"{p.provider}:{p.model}" for p in providers}
            if fb_full not in existing_keys:
                providers.append(ProviderConfig(
                    provider="kilo",
                    model=fb_model,
                    api_key=None,  # Anonymous access - no key required
                    base_url=_s.kilo_gateway_base_url,
                    priority=len(providers),
                    max_failures=10,  # Higher tolerance - rate limit is 200/hr/IP
                    cooldown_seconds=30.0,
                ))
                logger.info("kilo_free_model_added_to_fallback", model=fb_model)

    # ── ABSOLUTE LAST RESORT: Pollinations AI & g4f anonymous fallbacks ──
    if _s.pollination_enabled:
        pollination_models = [
            ("openai", "Pollinations OpenAI"),
            ("mistral", "Pollinations Mistral"),
            ("llama", "Pollinations Llama"),
            ("qwen", "Pollinations Qwen"),
        ]
        for fb_model, _ in pollination_models:
            fb_full = f"pollinations:{fb_model}"
            existing_keys = {f"{p.provider}:{p.model}" for p in providers}
            if fb_full not in existing_keys:
                providers.append(ProviderConfig(
                    provider="pollinations",
                    model=fb_model,
                    api_key="not-needed",
                    base_url=None,
                    priority=len(providers),
                    max_failures=10,
                    cooldown_seconds=60.0,
                ))

    if _s.g4f_enabled:
        g4f_models = [
            ("gpt-4o", "g4f GPT-4o"),
            ("gpt-4", "g4f GPT-4"),
            ("claude", "g4f Claude"),
            ("llama-3", "g4f Llama 3"),
        ]
        for fb_model, _ in g4f_models:
            fb_full = f"g4f:{fb_model}"
            existing_keys = {f"{p.provider}:{p.model}" for p in providers}
            if fb_full not in existing_keys:
                providers.append(ProviderConfig(
                    provider="g4f",
                    model=fb_model,
                    api_key="not-needed",
                    base_url=None,
                    priority=len(providers),
                    max_failures=15,
                    cooldown_seconds=60.0,
                ))

    if len(providers) <= 1:
        logger.info("llm_fallback_single_provider", provider=core_key)
        return LLMClient(
            provider=_s.core_llm_provider,
            api_key=_resolve_api_key(_s.core_llm_provider, _s.core_llm_api_key),
            base_url=_s.core_llm_base_url,
            model=_s.core_llm_model,
            name="core",
        )

    # Build fallback chain
    logger.info(
        "llm_fallback_chain_built",
        providers=[f"{p.provider}:{p.model}" for p in providers],
        count=len(providers),
    )

    # Credential pool (optional)
    from app.core.credential_pool import CredentialPool
    pool: CredentialPool | None = None
    if _s.llm_credential_pool:
        try:
            import json
            pool_config = json.loads(_s.llm_credential_pool)
            if isinstance(pool_config, dict) and pool_config:
                pool = CredentialPool(
                    cooldown_seconds=float(_s.llm_credential_cooldown),
                )
                for _provider, _keys in pool_config.items():
                    if isinstance(_keys, list):
                        _str_keys = [str(k) for k in _keys if k]
                        if _str_keys:
                            pool.add_credentials(_provider, _str_keys)
        except Exception as e:
            logger.warning("credential_pool_parse_failed", error=str(e))

    chain = ProviderFallbackChain(providers=providers)
    return FallbackLLMClient(fallback_chain=chain, credential_pool=pool)


def get_local_llm() -> Optional[LLMClient]:
    """Get local LLM client if enabled."""
    _s = _settings()
    if not _s.local_llm_enabled:
        return None
    return LLMClient(
        provider="local",
        api_key="not-needed",
        base_url=_s.local_llm_base_url,
        model=_s.local_llm_model,
        name="local",
    )


def get_kilo_free_llm(model: str = "kilo-auto/free") -> LLMClient:
    """Get Kilo Gateway free LLM client (anonymous access, IP-based rate limiting).

    Args:
        model: Model ID to use. Defaults to 'kilo-auto/free' (auto-routed best free model).
                Available models: kilo-auto/free, nvidia/nemotron-3-super-120b-a12b:free,
                poolside/laguna-m.1:free, openrouter/free.

    Returns:
        LLMClient configured for Kilo Gateway anonymous access.
    """
    _s = _settings()
    return LLMClient(
        provider="kilo",
        api_key="anonymous",
        base_url=_s.kilo_gateway_base_url,
        model=model,
        name="kilo-free",
    )


def get_core_llm_with_kilo_fallback() -> "Any":
    """
    Get Core LLM with automatic fallback to Kilo Gateway free models.

    Best-effort chain: core_provider → kilo-auto/free → other providers from fallback chain.
    Useful for development/testing without any API keys configured.

    Returns:
        FallbackLLMClient with kilo gateway fallback, or LLMClient if no fallback configured.
    """
    from app.core.provider_fallback import (
        FallbackLLMClient,
        ProviderConfig,
        ProviderFallbackChain,
    )

    _s = _settings()
    providers: list[ProviderConfig] = []

    # Primary: core provider
    core_resolved_key = _resolve_api_key(_s.core_llm_provider, _s.core_llm_api_key)
    providers.append(ProviderConfig(
        provider=_s.core_llm_provider,
        model=_s.core_llm_model,
        api_key=core_resolved_key or None,
        base_url=_s.core_llm_base_url or None,
        priority=0,
        max_failures=5,
        cooldown_seconds=15.0,
    ))

    # Secondary: Kilo Gateway free (if enabled and no core key available)
    if _s.kilo_gateway_enabled:
        providers.append(ProviderConfig(
            provider="kilo",
            model="kilo-auto/free",
            api_key=None,  # Anonymous access
            base_url=_s.kilo_gateway_base_url,
            priority=1,
            max_failures=10,
            cooldown_seconds=30.0,
        ))

    # Additional: other fallback providers
    if _s.llm_fallback_providers:
        for entry in _s.llm_fallback_providers.split(","):
            parts = entry.strip().split(":")
            if len(parts) >= 2:
                fb_provider = parts[0].strip()
                fb_model = parts[1].strip()
                fb_key = parts[2].strip() if len(parts) > 2 else ""
                fb_full = f"{fb_provider}:{fb_model}"
                existing_keys = {f"{p.provider}:{p.model}" for p in providers}
                if fb_full not in existing_keys:
                    providers.append(ProviderConfig(
                        provider=fb_provider,
                        model=fb_model,
                        api_key=fb_key or None,
                        priority=len(providers),
                        max_failures=3,
                        cooldown_seconds=60.0,
                    ))

    # Last resort: Pollinations (anonymous, always available)
    if _s.pollination_enabled:
        providers.append(ProviderConfig(
            provider="pollinations",
            model="openai",
            api_key="not-needed",
            base_url=None,
            priority=len(providers),
            max_failures=10,
            cooldown_seconds=60.0,
        ))

    if len(providers) <= 1:
        return LLMClient(
            provider=_s.core_llm_provider,
            api_key=_resolve_api_key(_s.core_llm_provider, _s.core_llm_api_key),
            base_url=_s.core_llm_base_url,
            model=_s.core_llm_model,
            name="core",
        )

    return FallbackLLMClient(fallback_chain=ProviderFallbackChain(providers=providers))
