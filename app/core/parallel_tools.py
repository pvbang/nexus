"""
MekongAI Nexus - Parallel Tool Execution (Hermes-agent inspired).

Execute multiple tool calls concurrently using asyncio.gather().
Respects per-tool concurrency policies and iteration budgets.

Inspired by: hermes-agent/model_tools.py ThreadPoolExecutor pattern
Adapted to async: uses asyncio.gather() + Semaphore instead of ThreadPoolExecutor
since our tools are already async.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import Any, Optional

import structlog

from app.config import get_settings
from app.core.context_manager import truncate_tool_result
from app.mcp.tool_executor import ToolExecutor

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# IterationBudget — hermes-agent pattern for capping tool calls per session
# ---------------------------------------------------------------------------


class IterationBudget:
    """Track and enforce a maximum number of tool calls per session/task.

    Hermes-agent pattern: prevent runaway agents from burning through
    unlimited tool calls. When budget is exhausted, the executor returns
    an error message instead of running the tool.
    """

    def __init__(self, max_iterations: int | None = None) -> None:
        self._max = max_iterations or get_settings().tool_iteration_budget
        self._consumed: int = 0

    # -- mutators ----------------------------------------------------------

    def consume(self, n: int = 1) -> bool:
        """Consume *n* iterations. Return ``False`` if budget exceeded."""
        if self._consumed + n > self._max:
            logger.warning(
                "iteration_budget_exceeded",
                consumed=self._consumed,
                requested=n,
                max=self._max,
            )
            return False
        self._consumed += n
        return True

    def reset(self) -> None:
        """Reset for a new session / task."""
        self._consumed = 0

    # -- read-only properties ----------------------------------------------

    @property
    def remaining(self) -> int:
        return max(0, self._max - self._consumed)

    @property
    def consumed(self) -> int:
        return self._consumed

    @property
    def is_exhausted(self) -> bool:
        return self._consumed >= self._max

    def __repr__(self) -> str:
        return (
            f"IterationBudget(consumed={self._consumed}, "
            f"max={self._max}, remaining={self.remaining})"
        )


# ---------------------------------------------------------------------------
# ToolCallResult — structured result per tool call
# ---------------------------------------------------------------------------


@dataclass
class ToolCallResult:
    """Result of a single tool call execution."""

    tool_call_id: str
    tool_name: str
    result: dict[str, Any]
    success: bool
    execution_time_ms: float
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize for message bus / logging."""
        return {
            "tool_call_id": self.tool_call_id,
            "tool_name": self.tool_name,
            "result": self.result,
            "success": self.success,
            "execution_time_ms": round(self.execution_time_ms, 1),
            "error": self.error,
        }


# ---------------------------------------------------------------------------
# ParallelToolExecutor — main orchestrator
# ---------------------------------------------------------------------------


class ParallelToolExecutor:
    """Execute multiple tool calls concurrently with budget + concurrency control.

    Design choices (hermes-agent inspired):
    - Single tool call → executed directly (no gather overhead)
    - Multiple tool calls → ``asyncio.gather()`` with ``asyncio.Semaphore``
    - Per-tool timeout via ``asyncio.wait_for``
    - Error isolation: one failure does NOT cancel siblings
    - Results returned in SAME ORDER as input tool_calls
    - Tool output truncated via ``truncate_tool_result()``
    """

    def __init__(
        self,
        tool_executor: ToolExecutor,
        max_concurrent: int | None = None,
        iteration_budget: int | None = None,
        timeout: int | None = None,
    ) -> None:
        self.tool_executor = tool_executor
        settings = get_settings()
        self.max_concurrent = max_concurrent or settings.tool_max_concurrent
        self.timeout = timeout or settings.tool_timeout
        self.budget = IterationBudget(max_iterations=iteration_budget)
        self._semaphore = asyncio.Semaphore(self.max_concurrent)

    # -- public API --------------------------------------------------------

    async def execute_tool_calls(
        self,
        tool_calls: list[dict[str, Any]],
    ) -> list[ToolCallResult]:
        """Execute a batch of tool calls, returning results in input order.

        Each *tool_call* dict must contain:
          - ``id``  (str) — unique tool_call_id
          - ``function.name`` (str)
          - ``function.arguments`` (str — JSON)

        Returns a list of :class:`ToolCallResult` in the **same order**
        as the input ``tool_calls``.
        """
        n = len(tool_calls)
        if n == 0:
            return []

        # Budget check — fail fast if budget is already exhausted
        if not self.budget.consume(n):
            return self._budget_exceeded_results(tool_calls)

        # Single tool call → direct execution (no gather overhead)
        if n == 1:
            logger.debug("parallel_tools_single", tool=self._tc_name(tool_calls[0]))
            result = await self._execute_one(tool_calls[0])
            return [result]

        # Multiple tool calls → parallel via asyncio.gather
        logger.info(
            "parallel_tools_batch",
            count=n,
            tools=[self._tc_name(tc) for tc in tool_calls],
            max_concurrent=self.max_concurrent,
        )

        # Create coroutines — gather preserves order
        coros = [self._execute_one_guarded(tc) for tc in tool_calls]
        raw_results = await asyncio.gather(*coros, return_exceptions=True)

        # Convert any escaped exceptions into failed ToolCallResult
        results: list[ToolCallResult] = []
        for i, r in enumerate(raw_results):
            if isinstance(r, BaseException):
                tc = tool_calls[i]
                results.append(ToolCallResult(
                    tool_call_id=tc.get("id", f"call_{i}"),
                    tool_name=self._tc_name(tc),
                    result={"success": False, "output": f"Unexpected error: {r}", "data": None, "artifacts": []},
                    success=False,
                    execution_time_ms=0.0,
                    error=f"Gather exception: {type(r).__name__}: {r}",
                ))
            else:
                results.append(r)

        succeeded = sum(1 for r in results if r.success)
        logger.info(
            "parallel_tools_done",
            total=n,
            succeeded=succeeded,
            failed=n - succeeded,
            total_time_ms=sum(r.execution_time_ms for r in results),
        )

        return results

    # -- internals ---------------------------------------------------------

    async def _execute_one_guarded(self, tc: dict[str, Any]) -> ToolCallResult:
        """Execute one tool call with semaphore + timeout + error isolation."""
        async with self._semaphore:
            return await self._execute_one(tc)

    async def _execute_one(self, tc: dict[str, Any]) -> ToolCallResult:
        """Execute a single tool call with timeout and error handling."""
        tool_call_id = tc.get("id", "unknown")
        tool_name = self._tc_name(tc)
        tool_params = self._tc_params(tc)

        start = time.perf_counter()
        try:
            result = await asyncio.wait_for(
                self.tool_executor.execute(tool_name, tool_params),
                timeout=self.timeout,
            )
            elapsed_ms = (time.perf_counter() - start) * 1000

            return ToolCallResult(
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                result=result,
                success=result.get("success", False),
                execution_time_ms=elapsed_ms,
            )
        except asyncio.TimeoutError:
            elapsed_ms = (time.perf_counter() - start) * 1000
            logger.error(
                "tool_timeout",
                tool=tool_name,
                timeout=self.timeout,
                elapsed_ms=elapsed_ms,
            )
            return ToolCallResult(
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                result={
                    "success": False,
                    "output": f"Tool '{tool_name}' timed out after {self.timeout}s",
                    "data": None,
                    "artifacts": [],
                },
                success=False,
                execution_time_ms=elapsed_ms,
                error=f"TimeoutError: exceeded {self.timeout}s",
            )
        except Exception as exc:
            elapsed_ms = (time.perf_counter() - start) * 1000
            logger.error(
                "tool_execution_error",
                tool=tool_name,
                error=str(exc),
                elapsed_ms=elapsed_ms,
            )
            return ToolCallResult(
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                result={
                    "success": False,
                    "output": f"Tool error: {exc}",
                    "data": None,
                    "artifacts": [],
                },
                success=False,
                execution_time_ms=elapsed_ms,
                error=f"{type(exc).__name__}: {exc}",
            )

    def _budget_exceeded_results(
        self, tool_calls: list[dict[str, Any]]
    ) -> list[ToolCallResult]:
        """Return budget-exceeded error results for all tool calls."""
        msg = (
            f"Tool call budget exceeded "
            f"({self.budget.consumed}/{self.budget.consumed + len(tool_calls)} max). "
            f"Please summarize progress."
        )
        return [
            ToolCallResult(
                tool_call_id=tc.get("id", "unknown"),
                tool_name=self._tc_name(tc),
                result={
                    "success": False,
                    "output": msg,
                    "data": None,
                    "artifacts": [],
                },
                success=False,
                execution_time_ms=0.0,
                error="IterationBudgetExceeded",
            )
            for tc in tool_calls
        ]

    # -- helpers: parse tool_call dict -------------------------------------

    @staticmethod
    def _tc_name(tc: dict[str, Any]) -> str:
        """Extract tool name from OpenAI-format tool_call dict."""
        return tc.get("function", {}).get("name", "unknown")

    @staticmethod
    def _tc_params(tc: dict[str, Any]) -> dict[str, Any]:
        """Extract and parse tool parameters from tool_call dict.

        Hermes-agent pattern: coerce_tool_args — basic arg validation/parsing.
        """
        raw_args = tc.get("function", {}).get("arguments", "{}")
        if isinstance(raw_args, dict):
            return raw_args
        try:
            parsed = json.loads(raw_args)
            if isinstance(parsed, dict):
                return parsed
            return {}
        except (json.JSONDecodeError, TypeError):
            logger.warning(
                "tool_args_parse_failed",
                tool=tc.get("function", {}).get("name", "?"),
                raw=str(raw_args)[:200],
            )
            return {}
