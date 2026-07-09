"""H4 (audit 2026-06-29): a provider safety-refusal must surface a signal the
orchestrator's refusal-terminal guard recognizes — otherwise the role-mismatch
loop (offensive agent assigned to a defensive task, content_filter blocks every
turn) burns dozens of turns (task 85b5e3c9: 32 turns).

When the LLM returns an empty response with a safety finish_reason
(content_filter / refusal / safety), the worker's diagnostic output must contain a
phrase that ``termination.refusal_is_terminal`` matches, so evaluate_completion's
blocked-terminal/lawful_refusal ceiling fires and the loop stops cleanly. An
ordinary infrastructure stall (no safety finish_reason) must NOT be tagged as a
refusal — that would wrongly terminate a legitimate task.
"""

from __future__ import annotations

from app.orchestration.agent_worker import AgentWorker
from app.orchestration.models import AgentAssignment
from app.orchestration.termination import refusal_is_terminal


class _ContentFilterLLM:
    """LLM that refuses on safety grounds: empty content + finish_reason."""

    def __init__(self, finish_reason="content_filter"):
        self._finish_reason = finish_reason

    async def chat(self, messages, tools=None, **kwargs):
        # Empty content + a safety finish_reason, both on the tooled round and the
        # tools-off retry the worker makes.
        return {"content": "", "tool_calls": [], "finish_reason": self._finish_reason}


class _InfraStallLLM:
    """LLM that returns empty with NO safety finish_reason (transient infra)."""

    async def chat(self, messages, tools=None, **kwargs):
        return {"content": "", "tool_calls": [], "finish_reason": ""}


class _Executor:
    def __init__(self):
        self.calls = []

    async def execute(self, tool_name, tool_args):
        self.calls.append((tool_name, tool_args))
        return {"success": True, "output": "ok"}


def _worker(llm):
    return AgentWorker(
        agent_name="Recon Specialist",
        agent_role="recon",
        llm=llm,
        tool_executor=_Executor(),
        tool_schemas=[{
            "type": "function",
            "function": {"name": "http_request", "parameters": {"type": "object", "properties": {}}},
        }],
    )


def _assignment():
    return AgentAssignment(
        agent_role="recon",
        agent_name="Recon Specialist",
        task_description="Recon pln.co.id and enumerate the attack surface.",
        success_criteria="Findings produced.",
    )


async def test_content_filter_refusal_is_recognized_as_terminal():
    """A safety finish_reason → worker output trips refusal_is_terminal."""
    worker = _worker(_ContentFilterLLM("content_filter"))
    result = await worker.execute(assignment=_assignment(), shared_context="")

    assert refusal_is_terminal(result.output), (
        "a confirmed content_filter refusal did not surface a terminal-refusal "
        "signal — the orchestrator cannot distinguish it and will loop"
    )


async def test_anthropic_refusal_reason_is_recognized():
    """Anthropic spelling (finish_reason='refusal') is handled too."""
    worker = _worker(_ContentFilterLLM("refusal"))
    result = await worker.execute(assignment=_assignment(), shared_context="")
    assert refusal_is_terminal(result.output)


async def test_infra_stall_is_not_tagged_as_refusal():
    """An empty response with NO safety reason must NOT look like a refusal —
    that would wrongly terminate a legitimate task on a transient blip."""
    worker = _worker(_InfraStallLLM())
    result = await worker.execute(assignment=_assignment(), shared_context="")
    assert not refusal_is_terminal(result.output), (
        "a transient infra stall was misclassified as a safety refusal"
    )
