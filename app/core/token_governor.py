"""Token Governor — pure measurement primitives for long-running tasks.

Three independent, fail-open components (NO side effects — they only measure
and return a verdict; the caller decides the action):

  - TokenEstimator (B): estimate tokens BEFORE an LLM call. tiktoken baseline
    (optional import) + per-model calibration learned from real usage.
  - BudgetTracker  (A): cumulative-EFFORT ceiling (monotonic total tokens spent),
    distinct from context compression (a point-in-time window check).
  - LoopMonitor    (C): detect a single agent repeating the same tool call
    inside its react-loop.

Inspired by deer-flow's TokenBudgetMiddleware + LoopDetectionMiddleware, adapted
to mekongai's plain-dict LLM result shape.
"""

from __future__ import annotations

import hashlib
import threading
from typing import Any, NamedTuple, Optional

import structlog

logger = structlog.get_logger(__name__)

# ── B: token estimation ──────────────────────────────────────────────────────

_CHARS_PER_TOKEN_LATIN = 4.0
_CHARS_PER_TOKEN_CJK = 2.0
_CORRECTION_MIN = 0.5
_CORRECTION_MAX = 2.0
_CORRECTION_EMA_ALPHA = 0.2  # weight of each new observation


def _content_text(msg: dict) -> str:
    """Flatten a message's content to plain text (handles str or block list)."""
    content = msg.get("content")
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        text = " ".join(
            b.get("text", "") for b in content if isinstance(b, dict)
        )
    else:
        text = ""
    # tool_calls arguments also cost tokens
    for tc in msg.get("tool_calls") or []:
        if isinstance(tc, dict):
            text += " " + str(tc.get("function", {}).get("arguments", ""))
    return text


def _char_estimate(messages: list[dict]) -> int:
    """CJK-aware char-based token estimate (fallback when tiktoken unavailable).

    Latin text ~4 chars/token; CJK/non-ASCII ~2 chars/token. We split each
    message's chars into ascii vs non-ascii and divide by the matching ratio.
    """
    total = 0.0
    for msg in messages:
        text = _content_text(msg)
        if not text:
            continue
        non_ascii = sum(1 for c in text if ord(c) > 127)
        ascii_n = len(text) - non_ascii
        total += ascii_n / _CHARS_PER_TOKEN_LATIN + non_ascii / _CHARS_PER_TOKEN_CJK
        total += 4  # per-message role/metadata overhead
    return int(total)


class TokenEstimator:
    """Estimate prompt tokens before a call; self-calibrate from real usage."""

    def __init__(self, use_tiktoken: bool = True) -> None:
        self._use_tiktoken = use_tiktoken
        self._correction: dict[str, float] = {}
        self._encoding = None
        self._tiktoken_ok = False
        if use_tiktoken:
            self._try_init_tiktoken()

    def _try_init_tiktoken(self) -> None:
        try:
            import tiktoken

            # cl100k_base is a reasonable cross-model baseline; per-model drift
            # is corrected by observe(). Warmup is cheap here (no network).
            self._encoding = tiktoken.get_encoding("cl100k_base")
            self._tiktoken_ok = True
        except Exception as e:  # missing lib, transient download failure, etc.
            logger.debug("tiktoken_unavailable", error=str(e))
            self._tiktoken_ok = False

    def _raw_estimate(self, messages: list[dict], model: str = "") -> int:
        """Uncalibrated baseline estimate (tiktoken if available, else char)."""
        if self._tiktoken_ok and self._encoding is not None:
            try:
                total = 0
                for msg in messages:
                    text = _content_text(msg)
                    if text:
                        total += len(self._encoding.encode(text)) + 4
                return total
            except Exception:
                pass  # fall through to char estimate
        return _char_estimate(messages)

    def estimate(self, messages: list[dict], model: str = "") -> int:
        """Calibrated token estimate for a message list."""
        if not messages:
            return 0
        raw = self._raw_estimate(messages, model)
        factor = self._correction.get(model, 1.0) if model else 1.0
        return int(raw * factor)

    def observe(self, model: str, est_tokens: int, real_prompt_tokens: int) -> None:
        """Update per-model correction from a real prompt_tokens observation."""
        if not model or est_tokens <= 0 or real_prompt_tokens <= 0:
            return
        ratio = real_prompt_tokens / est_tokens
        ratio = max(_CORRECTION_MIN, min(_CORRECTION_MAX, ratio))
        prev = self._correction.get(model, 1.0)
        # No lock: by design fail-open. A lost EMA update under concurrent
        # observe() only slows convergence — each write is an independent float,
        # never a corrupting partial state.
        self._correction[model] = (
            (1 - _CORRECTION_EMA_ALPHA) * prev + _CORRECTION_EMA_ALPHA * ratio
        )


# ── A: cumulative-effort budget ──────────────────────────────────────────────


class BudgetStatus(NamedTuple):
    level: str   # "ok" | "warn" | "hard_stop"
    used: int
    limit: int
    pct: float


class BudgetTracker:
    """Monotonic cumulative token ceiling (effort/cost), fed by REAL usage.

    Distinct from compression: this never decreases. warn → ask the caller to
    wrap up; hard_stop → caller forces a conclusion.
    """

    def __init__(
        self,
        limit: int,
        warn_ratio: float = 0.80,
        hard_stop_ratio: float = 0.95,
    ) -> None:
        self._limit = max(1, int(limit))
        self._warn = warn_ratio
        self._hard = hard_stop_ratio
        self._used = 0

    def add(self, usage: Optional[dict]) -> None:
        if not usage:
            return
        try:
            tokens = int(usage.get("total_tokens", 0) or 0)
        except Exception:
            return
        if tokens > 0:
            self._used += tokens

    @property
    def used(self) -> int:
        return self._used

    def reset(self) -> None:
        """Clear cumulative usage back to zero, re-scoping the ceiling.

        Use this to bound the budget to a fresh accounting window. A per-TURN
        tracker (config.agent_token_budget) calls this at the start of each
        execute() so a reused worker judges each turn on its own effort; a
        per-TASK tracker (config.task_token_budget, held in the registry) does
        NOT — its window is the whole task lifetime. Do not reset a registry
        (per-task) tracker, or the whole-task ceiling is lost.
        """
        self._used = 0

    def status(self) -> BudgetStatus:
        pct = self._used / self._limit
        if pct >= self._hard:
            level = "hard_stop"
        elif pct >= self._warn:
            level = "warn"
        else:
            level = "ok"
        return BudgetStatus(level=level, used=self._used, limit=self._limit, pct=pct)


# Per-task registry — mirrors compression_chain.get_or_create_chain so workers
# can feed the task-level budget via current_task_id without parameter threading.
_task_budgets: dict[str, BudgetTracker] = {}
_task_budgets_lock = threading.Lock()


def get_task_budget(task_id: str, limit: int) -> BudgetTracker:
    with _task_budgets_lock:
        bt = _task_budgets.get(task_id)
        if bt is None:
            bt = BudgetTracker(limit=limit)
            _task_budgets[task_id] = bt
        return bt


def clear_task_budget(task_id: str) -> None:
    with _task_budgets_lock:
        _task_budgets.pop(task_id, None)


# ── C: in-agent loop detection ────────────────────────────────────────────────

# Identifying arg fields, in priority order. First present field defines the
# call's identity; volatile fields (timeout, max_tokens, line ranges) ignored.
_IDENTITY_FIELDS = ("url", "target", "path", "file_path", "query", "key", "command")
_KEY_VALUE_MAX = 200


def _stable_tool_key(tool_name: str, args: Any) -> str:
    """Normalized identity for a tool call (deer-flow _stable_tool_key idea).

    Buckets by the first identifying field so repeats of the same logical action
    collide even when volatile params differ. Falls back to a hash of the whole
    arg blob when no identifying field is present.
    """
    if isinstance(args, dict):
        for field in _IDENTITY_FIELDS:
            val = args.get(field)
            if val:
                return f"{tool_name}:{field}={str(val)[:_KEY_VALUE_MAX]}"
        blob = repr(sorted((k, str(v)[:_KEY_VALUE_MAX]) for k, v in args.items()))
    else:
        blob = str(args)[:_KEY_VALUE_MAX]
    digest = hashlib.sha1(blob.encode("utf-8", "ignore")).hexdigest()[:12]
    return f"{tool_name}:{digest}"


class LoopStatus(NamedTuple):
    level: str   # "ok" | "warn" | "hard_stop"
    key: str
    count: int


class LoopMonitor:
    """Counts repeated identical tool calls within ONE agent react-loop."""

    def __init__(self, warn_count: int = 3, hard_stop_count: int = 5) -> None:
        self._warn = warn_count
        self._hard = hard_stop_count
        self._counts: dict[str, int] = {}

    def record(self, tool_name: str, args: Any) -> LoopStatus:
        key = _stable_tool_key(tool_name, args)
        count = self._counts.get(key, 0) + 1
        self._counts[key] = count
        if count >= self._hard:
            level = "hard_stop"
        elif count == self._warn:
            level = "warn"     # warn exactly once, at the warn threshold
        else:
            level = "ok"
        return LoopStatus(level=level, key=key, count=count)

    def reset(self) -> None:
        self._counts.clear()
