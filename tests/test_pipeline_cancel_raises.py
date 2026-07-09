"""A cooperative cancel must RAISE CancelledError, not fall through to report-gen.

Root cause (found in logic review): when the cancel token/DB-flag tripped, the
pipeline `break`-ed out of the turn loop but then ran FinalReportGenerator +
_summarize_for_user (2 LLM calls) and returned a normal dict, which the
orchestrator hard-codes to status "completed" → task_runner emits task_completed
instead of task_cancelled. Raising CancelledError routes into task_runner's
existing `except asyncio.CancelledError` handler, which finalises task_cancelled
fast. CancelledError is BaseException, so the pipeline's `except Exception` does
not swallow it.

We test the decision helper and the BaseException property the fix relies on.
"""

from __future__ import annotations

import asyncio

import pytest

from app.orchestration.pipeline import NexusExecutionPipeline


def _make_pipeline(cancel_flag: bool):
    # cancel_check is a plain sync callable per the orchestrator wiring.
    return NexusExecutionPipeline(
        llm=None,
        tool_executor=None,
        tool_schemas=[],
        event_callback=None,
        cancel_check=lambda: cancel_flag,
        task_id="00000000-0000-0000-0000-000000000000",
        db=None,
    )


async def test_should_cancel_true_when_token_set():
    p = _make_pipeline(True)
    assert await p._should_cancel() is True


async def test_should_cancel_false_when_not_cancelled():
    p = _make_pipeline(False)
    # _is_cancelled_db returns False on any error / no session factory in tests.
    assert await p._should_cancel() is False


def test_cancellederror_is_baseexception_not_exception():
    # The fix relies on CancelledError NOT being caught by `except Exception`.
    assert issubclass(asyncio.CancelledError, BaseException)
    assert not issubclass(asyncio.CancelledError, Exception)
