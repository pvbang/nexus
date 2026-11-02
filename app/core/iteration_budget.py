"""
Nexus - Per-Session Iteration Budget.

Controls how many iterations (tool calls, LLM rounds) a session can consume.
Prevents runaway agents from burning infinite resources.

Hermes-agent inspired: each session gets an independent budget.
Sub-agents get a lower independent budget from the parent.

Thread-safe with threading.Lock (works across async contexts).
"""

import threading
from dataclasses import dataclass, field
from typing import Optional

import structlog

logger = structlog.get_logger(__name__)


class BudgetExhaustedError(Exception):
    """Raised when an iteration budget is fully consumed."""

    def __init__(self, session_key: str, max_iterations: int) -> None:
        self.session_key = session_key
        self.max_iterations = max_iterations
        super().__init__(
            f"Iteration budget exhausted for session '{session_key}': "
            f"max {max_iterations} iterations consumed."
        )


@dataclass
class BudgetSnapshot:
    """Immutable snapshot of budget state for reporting."""

    max_iterations: int
    consumed: int
    refunded: int
    remaining: int
    is_exhausted: bool
    session_key: str


class IterationBudget:
    """
    Per-session iteration budget with consume/refund semantics.

    Usage:
        budget = IterationBudget(session_key="user:conv", max_iterations=500)

        # In react loop:
        if not budget.can_consume():
            break  # Budget exhausted
        budget.consume()  # Decrements remaining

        # On tool failure (retry allowed):
        budget.refund()  # Adds back 1 iteration

        # Check status:
        print(budget.remaining)  # e.g., 498
        print(budget.is_exhausted)  # False

    Thread Safety:
        All mutations are protected by threading.Lock, safe for concurrent
        access from multiple async tasks (asyncio tasks share thread).
    """

    def __init__(
        self,
        session_key: str,
        max_iterations: int = 0,
        *,
        parent_budget: Optional["IterationBudget"] = None,
    ) -> None:
        """
        Initialize iteration budget.

        Args:
            session_key: Identifier for this budget (usually user_id:conversation_id).
            max_iterations: Maximum iterations allowed. 0 = use default from settings.
            parent_budget: If this is a sub-agent budget, reference to parent.
        """
        if max_iterations <= 0:
            max_iterations = _get_default_max_iterations()

        self._session_key = session_key
        self._max_iterations = max_iterations
        self._consumed = 0
        self._refunded = 0
        self._lock = threading.Lock()
        self._parent_budget = parent_budget

    @property
    def session_key(self) -> str:
        """Session key this budget belongs to."""
        return self._session_key

    @property
    def max_iterations(self) -> int:
        """Maximum allowed iterations."""
        return self._max_iterations

    @property
    def consumed(self) -> int:
        """Total iterations consumed so far."""
        with self._lock:
            return self._consumed

    @property
    def refunded(self) -> int:
        """Total iterations refunded."""
        with self._lock:
            return self._refunded

    @property
    def remaining(self) -> int:
        """Remaining iterations available."""
        with self._lock:
            return self._max_iterations - self._consumed + self._refunded

    @property
    def is_exhausted(self) -> bool:
        """Whether the budget is fully consumed."""
        return self.remaining <= 0

    def can_consume(self, count: int = 1) -> bool:
        """Check if N iterations can be consumed without actually consuming."""
        return self.remaining >= count

    def consume(self, count: int = 1) -> int:
        """
        Consume N iterations from the budget.

        Args:
            count: Number of iterations to consume (default 1).

        Returns:
            Remaining iterations after consumption.

        Raises:
            BudgetExhaustedError: If budget is exhausted.
        """
        with self._lock:
            remaining = self._max_iterations - self._consumed + self._refunded
            if remaining < count:
                raise BudgetExhaustedError(self._session_key, self._max_iterations)
            self._consumed += count
            new_remaining = self._max_iterations - self._consumed + self._refunded

        # Also consume from parent if this is a sub-agent budget
        if self._parent_budget is not None:
            try:
                self._parent_budget.consume(count)
            except BudgetExhaustedError:
                # Parent exhausted — refund the child consumption
                with self._lock:
                    self._consumed -= count
                raise

        if new_remaining <= 10 and new_remaining % 5 == 0:
            logger.warning(
                "iteration_budget_low",
                session_key=self._session_key,
                remaining=new_remaining,
                consumed=self._consumed,
            )

        return new_remaining

    def refund(self, count: int = 1) -> int:
        """
        Refund N iterations back to the budget.

        Used when a tool call fails and is retried (the failed attempt
        shouldn't count against budget).

        Args:
            count: Number of iterations to refund (default 1).

        Returns:
            Remaining iterations after refund.
        """
        with self._lock:
            self._refunded += count
            remaining = self._max_iterations - self._consumed + self._refunded

        # Refund parent too
        if self._parent_budget is not None:
            self._parent_budget.refund(count)

        logger.debug(
            "iteration_budget_refund",
            session_key=self._session_key,
            refunded=count,
            remaining=remaining,
        )
        return remaining

    def snapshot(self) -> BudgetSnapshot:
        """Get an immutable snapshot of current budget state."""
        with self._lock:
            remaining = self._max_iterations - self._consumed + self._refunded
            return BudgetSnapshot(
                max_iterations=self._max_iterations,
                consumed=self._consumed,
                refunded=self._refunded,
                remaining=remaining,
                is_exhausted=remaining <= 0,
                session_key=self._session_key,
            )

    def create_sub_budget(
        self,
        sub_key: str,
        max_iterations: int = 0,
    ) -> "IterationBudget":
        """
        Create a sub-agent budget derived from this budget.

        Sub-agent budget has:
        - Independent (lower) iteration limit
        - Parent reference: consuming sub-budget also consumes parent
        - Default sub-budget = 25% of parent's remaining

        Args:
            sub_key: Identifier for sub-agent (e.g., "user:conv:sub_researcher").
            max_iterations: Explicit limit for sub-agent. 0 = auto (25% of parent remaining).

        Returns:
            New IterationBudget for the sub-agent.
        """
        if max_iterations <= 0:
            # Default: sub-agent gets 25% of parent's remaining, min 20
            max_iterations = max(20, self.remaining // 4)

        sub_budget = IterationBudget(
            session_key=sub_key,
            max_iterations=max_iterations,
            parent_budget=self,
        )

        logger.info(
            "sub_budget_created",
            parent_key=self._session_key,
            sub_key=sub_key,
            sub_max=max_iterations,
            parent_remaining=self.remaining,
        )
        return sub_budget

    def __repr__(self) -> str:
        return (
            f"IterationBudget(session='{self._session_key}', "
            f"remaining={self.remaining}/{self._max_iterations})"
        )


def _get_default_max_iterations() -> int:
    """Get default max iterations from app settings."""
    try:
        from app.config import get_settings
        settings = get_settings()
        # Use effective_agent_max_rounds as the budget ceiling
        return settings.effective_agent_max_rounds
    except Exception:
        return 500  # Safety fallback


# ── Module-level registry of active budgets ──

_active_budgets: dict[str, IterationBudget] = {}
_budgets_lock = threading.Lock()


def get_or_create_budget(
    session_key: str,
    max_iterations: int = 0,
) -> IterationBudget:
    """
    Get existing budget for session or create a new one.

    Thread-safe. If budget already exists, returns it (preserving state).
    """
    with _budgets_lock:
        if session_key in _active_budgets:
            return _active_budgets[session_key]
        budget = IterationBudget(session_key=session_key, max_iterations=max_iterations)
        _active_budgets[session_key] = budget
        return budget


def remove_budget(session_key: str) -> Optional[IterationBudget]:
    """Remove a budget from the registry. Returns removed budget or None."""
    with _budgets_lock:
        return _active_budgets.pop(session_key, None)


def get_budget(session_key: str) -> Optional[IterationBudget]:
    """Get an existing budget without creating. Returns None if not found."""
    with _budgets_lock:
        return _active_budgets.get(session_key)
