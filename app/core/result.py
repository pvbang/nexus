"""
MekongAI Nexus - Result Type for Explicit Error Handling.

Inspired by Shannon's Result<T, E> pattern (Rust-style discriminated union).
Makes error handling explicit without exception spaghetti.

Usage:
    from app.core.result import Result, Ok, Err, ok, err, is_ok, is_err

    async def fetch_user(user_id: str) -> Result[User, AppError]:
        user = await db.get(user_id)
        if not user:
            return err(AppError(code=ErrorCode.NOT_FOUND, message="User not found"))
        return ok(user)

    result = await fetch_user("123")
    if is_ok(result):
        user = result.value
    else:
        handle_error(result.error)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Generic, TypeVar, Union, overload

T = TypeVar("T")
E = TypeVar("E")
U = TypeVar("U")


@dataclass(frozen=True, slots=True)
class Ok(Generic[T]):
    """Success variant of Result."""

    value: T

    @property
    def ok(self) -> bool:
        return True

    def unwrap(self) -> T:
        """Extract value. Always safe on Ok."""
        return self.value

    def unwrap_or(self, default: T) -> T:  # type: ignore[override]
        """Return value (ignores default)."""
        return self.value

    def map(self, fn: Callable[[T], U]) -> Result[U, Any]:
        """Transform the success value."""
        return Ok(value=fn(self.value))

    def map_err(self, fn: Callable[[Any], Any]) -> Result[T, Any]:
        """No-op on Ok (error transform)."""
        return self  # type: ignore[return-value]


@dataclass(frozen=True, slots=True)
class Err(Generic[E]):
    """Error variant of Result."""

    error: E

    @property
    def ok(self) -> bool:
        return False

    def unwrap(self) -> Any:
        """Raises RuntimeError — use is_ok() check first."""
        raise RuntimeError(f"Called unwrap() on Err: {self.error}")

    def unwrap_or(self, default: Any) -> Any:
        """Return default value since this is an error."""
        return default

    def map(self, fn: Callable[[Any], Any]) -> Result[Any, E]:
        """No-op on Err (value transform)."""
        return self  # type: ignore[return-value]

    def map_err(self, fn: Callable[[E], U]) -> Result[Any, U]:
        """Transform the error."""
        return Err(error=fn(self.error))


# Union type — the core Result discriminated union
Result = Union[Ok[T], Err[E]]


def ok(value: T) -> Ok[T]:
    """Create a success Result."""
    return Ok(value=value)


def err(error: E) -> Err[E]:
    """Create an error Result."""
    return Err(error=error)


def is_ok(result: Result[T, E]) -> bool:
    """Type guard for Ok variant."""
    return isinstance(result, Ok)


def is_err(result: Result[T, E]) -> bool:
    """Type guard for Err variant."""
    return isinstance(result, Err)


def result_from_exception(fn: Callable[[], T]) -> Result[T, Exception]:
    """
    Convert a function that may raise into a Result.

    Usage:
        result = result_from_exception(lambda: json.loads(raw))
        if is_err(result):
            log.error("Parse failed", error=result.error)
    """
    try:
        return ok(fn())
    except Exception as e:
        return err(e)


async def async_result_from_exception(fn: Callable[[], Any]) -> Result[Any, Exception]:
    """
    Convert an async function that may raise into a Result.

    Usage:
        result = await async_result_from_exception(lambda: client.chat(messages))
    """
    try:
        value = await fn()
        return ok(value)
    except Exception as e:
        return err(e)
