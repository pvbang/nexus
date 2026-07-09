"""TokenJournal — a single sink that aggregates LLM token usage.

Token usage in mekongai is returned per-call in ``result["usage"]`` but was not
collected anywhere, so per-run breakdowns by model/caller did not exist. This
journal is the one place usage is recorded, with:

- breakdown by model and by caller (role),
- dedup by ``call_id`` (a retried / double-fired streaming call must count once),
- a running grand total.

Mirrors deer-flow's ``runtime/journal.py`` token-aggregation idea, adapted to
mekongai's plain-dict LLM result shape (no LangChain callbacks).
"""

from __future__ import annotations

from typing import Any, Optional

_KEYS = ("prompt_tokens", "completion_tokens", "total_tokens")


def _empty() -> dict[str, int]:
    return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}


class TokenJournal:
    """Accumulates token usage by model and caller, deduping by call_id."""

    def __init__(self) -> None:
        self._total = _empty()
        self._by_model: dict[str, dict[str, int]] = {}
        self._by_caller: dict[str, dict[str, int]] = {}
        self._seen_call_ids: set[str] = set()
        self.call_count = 0

    def record(
        self,
        usage: Optional[dict[str, Any]],
        *,
        model: str = "unknown",
        caller: str = "unknown",
        call_id: Optional[str] = None,
    ) -> None:
        """Record one call's usage. No-op for empty usage or a duplicate call_id."""
        if not usage:
            return
        if call_id is not None:
            if call_id in self._seen_call_ids:
                return
            self._seen_call_ids.add(call_id)

        normalized = {k: int(usage.get(k, 0) or 0) for k in _KEYS}
        if not any(normalized.values()):
            return

        self.call_count += 1
        for k in _KEYS:
            self._total[k] += normalized[k]
        self._add(self._by_model, model or "unknown", normalized)
        self._add(self._by_caller, caller or "unknown", normalized)

    @staticmethod
    def _add(bucket: dict[str, dict[str, int]], key: str, normalized: dict[str, int]) -> None:
        slot = bucket.setdefault(key, _empty())
        for k in _KEYS:
            slot[k] += normalized[k]

    def total(self) -> dict[str, int]:
        return dict(self._total)

    def by_model(self) -> dict[str, dict[str, int]]:
        return {m: dict(v) for m, v in self._by_model.items()}

    def by_caller(self) -> dict[str, dict[str, int]]:
        return {c: dict(v) for c, v in self._by_caller.items()}

    def snapshot(self) -> dict[str, Any]:
        """Full report for logging / persistence."""
        return {
            "total": self.total(),
            "by_model": self.by_model(),
            "by_caller": self.by_caller(),
            "call_count": self.call_count,
        }
