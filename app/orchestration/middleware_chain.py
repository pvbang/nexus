"""Composable middleware chain with declarative positioning.

deer-flow assembles an agent from feature-gated middleware whose order is
declared with ``@Next`` / ``@Prev`` anchors rather than a hard-coded list. This
module ports that idea as a small, dependency-free chain builder:

- ``add(item, *, enabled=, position=)`` registers a step. ``enabled=False``
  drops it (a feature flag turned off). ``position`` places it relative to a
  named anchor instead of always appending.
- ``resolved()`` returns the final ordered list of enabled step names/objects.

This is the seam for incrementally decomposing the orchestrator god-object:
behaviours move out one at a time into named, independently-toggleable steps
without rewriting the whole pipeline at once.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional


@dataclass(frozen=True)
class After:
    """Position a step immediately after the named anchor."""
    anchor: str


@dataclass(frozen=True)
class Before:
    """Position a step immediately before the named anchor."""
    anchor: str


class MiddlewareChain:
    """Ordered, name-keyed collection of middleware steps with anchored inserts."""

    def __init__(self) -> None:
        self._names: list[str] = []
        self._items: dict[str, Any] = {}
        self._disabled: set[str] = set()

    def add(
        self,
        name: str,
        item: Any = None,
        *,
        enabled: bool = True,
        position: Optional[Any] = None,
    ) -> "MiddlewareChain":
        """Register a step under ``name``. ``item`` defaults to ``name`` itself.

        ``position`` is ``After(anchor)`` / ``Before(anchor)`` or None (append).
        Disabled steps are kept registered but excluded from ``resolved()``.
        """
        if name in self._items:
            raise ValueError(f"Duplicate middleware name: {name!r}")
        self._items[name] = name if item is None else item
        if not enabled:
            self._disabled.add(name)

        if position is None:
            self._names.append(name)
        elif isinstance(position, After):
            self._names.insert(self._anchor_index(position.anchor) + 1, name)
        elif isinstance(position, Before):
            self._names.insert(self._anchor_index(position.anchor), name)
        else:
            raise ValueError(f"Unknown position spec: {position!r}")
        return self

    def _anchor_index(self, anchor: str) -> int:
        if anchor not in self._names:
            raise ValueError(f"Unknown anchor: {anchor!r}")
        return self._names.index(anchor)

    def resolved(self) -> list[Any]:
        """Return enabled items in their resolved order."""
        return [self._items[n] for n in self._names if n not in self._disabled]
