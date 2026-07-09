"""
MekongAI Nexus - Multi-step Workflow Execution Tools.

Provides tools for defining and executing automated multi-step workflows
with dependency graph resolution, parallel execution, and variable substitution.

Self-registers with the tool registry at import time.
"""

from __future__ import annotations

import asyncio
import re
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import structlog

from app.mcp.tool_registry import registry, tool_result, tool_error

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

_VAR_PATTERN = re.compile(r"\{\{(\w+(?:\.\w+)*)\}\}")


class StepStatus(str, Enum):
    """Status of a workflow step."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class WorkflowStatus(str, Enum):
    """Status of a workflow execution."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class StepDef:
    """Definition of a single workflow step."""

    name: str
    tool: str
    params: dict[str, Any] = field(default_factory=dict)
    depends_on: list[str] = field(default_factory=list)
    condition: str = ""
    on_failure: str = "stop"  # stop | continue | retry


@dataclass
class WorkflowDef:
    """Stored workflow definition."""

    id: str
    name: str
    description: str
    steps: list[StepDef]
    created_at: float = field(default_factory=time.time)


@dataclass
class StepResult:
    """Result of executing a single step."""

    name: str
    status: StepStatus
    output: Any = None
    error: str = ""
    duration_ms: float = 0.0


@dataclass
class WorkflowExecution:
    """Tracks a workflow execution."""

    id: str
    workflow_id: str
    status: WorkflowStatus = WorkflowStatus.PENDING
    variables: dict[str, Any] = field(default_factory=dict)
    step_results: list[StepResult] = field(default_factory=list)
    started_at: float = 0.0
    finished_at: float = 0.0


# ---------------------------------------------------------------------------
# Persistent storage (file-backed + in-memory cache)
# ---------------------------------------------------------------------------

import json as _json
from pathlib import Path as _Path

_WORKFLOW_STORE_PATH = _Path(
    __import__("os").environ.get("WORKSPACE_DIR", "workspace_data")
) / ".workflows.json"

_workflows: dict[str, WorkflowDef] = {}
_executions: dict[str, WorkflowExecution] = {}


def _persist_workflows() -> None:
    """Save workflow definitions to disk for survival across restarts."""
    try:
        data = {}
        for wf_id, wf in _workflows.items():
            data[wf_id] = {
                "id": wf.id,
                "name": wf.name,
                "description": wf.description,
                "steps": [
                    {"name": s.name, "tool": s.tool, "params": s.params,
                     "depends_on": s.depends_on, "condition": s.condition,
                     "on_failure": s.on_failure}
                    for s in wf.steps
                ],
            }
        _WORKFLOW_STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _WORKFLOW_STORE_PATH.write_text(_json.dumps(data, indent=2), encoding="utf-8")
    except Exception as e:
        logger.warning("workflow_persist_failed", error=str(e)[:200])


def _load_workflows() -> None:
    """Load persisted workflow definitions on module init."""
    global _workflows
    if not _WORKFLOW_STORE_PATH.exists():
        return
    try:
        data = _json.loads(_WORKFLOW_STORE_PATH.read_text(encoding="utf-8"))
        for wf_id, wf_data in data.items():
            steps = [
                StepDef(
                    name=s["name"], tool=s["tool"],
                    params=s.get("params", {}),
                    depends_on=s.get("depends_on", []),
                    condition=s.get("condition", ""),
                    on_failure=s.get("on_failure", "stop"),
                )
                for s in wf_data.get("steps", [])
            ]
            _workflows[wf_id] = WorkflowDef(
                id=wf_data["id"],
                name=wf_data["name"],
                description=wf_data.get("description", ""),
                steps=steps,
            )
        logger.info("workflows_loaded", count=len(_workflows))
    except Exception as e:
        logger.warning("workflow_load_failed", error=str(e)[:200])


_load_workflows()


# ---------------------------------------------------------------------------
# Engine helpers
# ---------------------------------------------------------------------------


def _substitute_vars(value: Any, variables: dict[str, Any]) -> Any:
    """Replace {{var_name}} and {{step.output}} placeholders in values."""
    if isinstance(value, str):
        def _replacer(m: re.Match) -> str:
            key = m.group(1)
            parts = key.split(".")
            obj: Any = variables
            for part in parts:
                if isinstance(obj, dict):
                    obj = obj.get(part, m.group(0))
                else:
                    return m.group(0)
            return str(obj) if not isinstance(obj, str) else obj

        return _VAR_PATTERN.sub(_replacer, value)
    if isinstance(value, dict):
        return {k: _substitute_vars(v, variables) for k, v in value.items()}
    if isinstance(value, list):
        return [_substitute_vars(item, variables) for item in value]
    return value


def _parse_steps(raw_steps: list[dict[str, Any]]) -> list[StepDef]:
    """Parse raw step dicts into StepDef objects."""
    result: list[StepDef] = []
    for s in raw_steps:
        result.append(StepDef(
            name=s.get("name", f"step_{len(result)}"),
            tool=s.get("tool", ""),
            params=s.get("params", {}),
            depends_on=s.get("depends_on", []),
            condition=s.get("condition", ""),
            on_failure=s.get("on_failure", "stop"),
        ))
    return result


def _build_execution_order(steps: list[StepDef]) -> list[list[StepDef]]:
    """Topological sort steps into parallel layers (Kahn's algorithm)."""
    name_map = {s.name: s for s in steps}
    in_degree: dict[str, int] = {s.name: 0 for s in steps}
    for s in steps:
        for dep in s.depends_on:
            if dep in name_map:
                in_degree[s.name] += 1

    layers: list[list[StepDef]] = []
    remaining = set(in_degree.keys())

    while remaining:
        ready = [n for n in remaining if in_degree[n] == 0]
        if not ready:
            break  # Cycle detected — run remaining sequentially
        layers.append([name_map[n] for n in ready])
        for n in ready:
            remaining.discard(n)
            for s in steps:
                if n in s.depends_on and s.name in remaining:
                    in_degree[s.name] -= 1

    # Add any cycle-stuck steps as final layer
    if remaining:
        layers.append([name_map[n] for n in remaining])

    return layers


async def _execute_step(
    step: StepDef,
    variables: dict[str, Any],
) -> StepResult:
    """Execute a single workflow step by calling its registered tool."""
    start = time.time()
    resolved_params = _substitute_vars(step.params, variables)

    entry = registry.get(step.tool)
    if not entry:
        return StepResult(
            name=step.name,
            status=StepStatus.FAILED,
            error=f"Tool '{step.tool}' not found in registry",
            duration_ms=(time.time() - start) * 1000,
        )

    try:
        if entry.is_async:
            raw = await entry.handler(resolved_params)
        else:
            raw = entry.handler(resolved_params)
        return StepResult(
            name=step.name,
            status=StepStatus.COMPLETED,
            output=raw,
            duration_ms=(time.time() - start) * 1000,
        )
    except Exception as e:
        return StepResult(
            name=step.name,
            status=StepStatus.FAILED,
            error=str(e),
            duration_ms=(time.time() - start) * 1000,
        )


async def _run_workflow(
    steps: list[StepDef],
    variables: dict[str, Any],
    max_parallel: int,
    timeout: int,
    execution: WorkflowExecution,
) -> None:
    """Run the workflow engine: resolve DAG, execute layers, handle failures."""
    execution.status = WorkflowStatus.RUNNING
    execution.started_at = time.time()
    layers = _build_execution_order(steps)
    step_outputs: dict[str, Any] = {}
    failed = False

    for layer in layers:
        if failed:
            for step in layer:
                execution.step_results.append(StepResult(
                    name=step.name, status=StepStatus.SKIPPED,
                ))
            continue

        # Evaluate conditions and filter runnable steps
        runnable: list[StepDef] = []
        for step in layer:
            if step.condition:
                resolved_cond = _substitute_vars(step.condition, variables)
                if resolved_cond.lower() in ("false", "0", "no", ""):
                    execution.step_results.append(StepResult(
                        name=step.name, status=StepStatus.SKIPPED,
                    ))
                    continue
            runnable.append(step)

        # Execute in batches of max_parallel
        for i in range(0, len(runnable), max_parallel):
            batch = runnable[i:i + max_parallel]
            semaphore = asyncio.Semaphore(max_parallel)

            async def _run_with_sem(s: StepDef) -> StepResult:
                async with semaphore:
                    return await _execute_step(s, variables)

            results = await asyncio.gather(
                *[_run_with_sem(s) for s in batch],
                return_exceptions=True,
            )

            for idx, res in enumerate(results):
                step = batch[idx]
                if isinstance(res, Exception):
                    sr = StepResult(
                        name=step.name,
                        status=StepStatus.FAILED,
                        error=str(res),
                    )
                else:
                    sr = res
                execution.step_results.append(sr)

                # Store output for downstream variable substitution
                step_outputs[step.name] = {"output": sr.output, "error": sr.error}
                variables[step.name] = step_outputs[step.name]

                # Handle failure policy
                if sr.status == StepStatus.FAILED:
                    if step.on_failure == "stop":
                        failed = True
                    elif step.on_failure == "retry":
                        retry_res = await _execute_step(step, variables)
                        execution.step_results[-1] = retry_res
                        variables[step.name] = {
                            "output": retry_res.output,
                            "error": retry_res.error,
                        }
                        if retry_res.status == StepStatus.FAILED:
                            failed = True

    execution.finished_at = time.time()
    execution.status = WorkflowStatus.FAILED if failed else WorkflowStatus.COMPLETED


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


async def _handle_create_workflow(args: dict[str, Any]) -> str:
    """Create a reusable workflow definition."""
    name = args.get("name", "").strip()
    description = args.get("description", "")
    raw_steps = args.get("steps", [])

    if not name:
        return tool_error("Workflow name is required")
    if not raw_steps:
        return tool_error("At least one step is required")

    steps = _parse_steps(raw_steps)

    # Validate tool names exist
    for step in steps:
        if not registry.get(step.tool):
            return tool_error(f"Step '{step.name}' references unknown tool '{step.tool}'")

    wf_id = str(uuid.uuid4())[:8]
    _workflows[wf_id] = WorkflowDef(
        id=wf_id, name=name, description=description, steps=steps,
    )
    _persist_workflows()

    logger.info("workflow_created", workflow_id=wf_id, name=name, step_count=len(steps))
    return tool_result(
        success=True,
        output=f"🔄 Workflow '{name}' created with {len(steps)} steps (id: {wf_id})",
        data={"workflow_id": wf_id, "name": name, "step_count": len(steps)},
    )


async def _handle_execute_workflow(args: dict[str, Any]) -> str:
    """Execute a workflow by ID or inline steps."""
    workflow_id = args.get("workflow_id", "")
    inline_steps = args.get("steps", [])
    variables = args.get("variables", {})
    max_parallel = min(args.get("max_parallel", 3), 10)
    timeout = min(args.get("timeout", 600), 3600)

    if workflow_id:
        wf = _workflows.get(workflow_id)
        if not wf:
            return tool_error(f"Workflow '{workflow_id}' not found")
        steps = wf.steps
    elif inline_steps:
        steps = _parse_steps(inline_steps)
        # Validate inline tools
        for step in steps:
            if not registry.get(step.tool):
                return tool_error(f"Step '{step.name}' references unknown tool '{step.tool}'")
        workflow_id = "inline"
    else:
        return tool_error("Provide workflow_id or inline steps")

    exec_id = str(uuid.uuid4())[:8]
    execution = WorkflowExecution(
        id=exec_id, workflow_id=workflow_id, variables=dict(variables),
    )
    _executions[exec_id] = execution

    try:
        await asyncio.wait_for(
            _run_workflow(steps, dict(variables), max_parallel, timeout, execution),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        execution.status = WorkflowStatus.FAILED
        execution.finished_at = time.time()

    # Build summary
    completed = sum(1 for r in execution.step_results if r.status == StepStatus.COMPLETED)
    failed = sum(1 for r in execution.step_results if r.status == StepStatus.FAILED)
    skipped = sum(1 for r in execution.step_results if r.status == StepStatus.SKIPPED)
    duration_s = round(execution.finished_at - execution.started_at, 2)

    summary = (
        f"🔄 Workflow execution {execution.status.value} "
        f"({completed} completed, {failed} failed, {skipped} skipped) "
        f"in {duration_s}s"
    )

    step_data = [
        {"name": r.name, "status": r.status.value, "duration_ms": round(r.duration_ms, 1)}
        for r in execution.step_results
    ]

    logger.info("workflow_executed", exec_id=exec_id, status=execution.status.value)
    return tool_result(
        success=execution.status == WorkflowStatus.COMPLETED,
        output=summary,
        data={"execution_id": exec_id, "status": execution.status.value, "steps": step_data},
    )


async def _handle_list_workflows(args: dict[str, Any]) -> str:
    """List all saved workflow definitions."""
    if not _workflows:
        return tool_result(success=True, output="🔄 No workflows defined.", data=[])

    items = [
        {"id": wf.id, "name": wf.name, "steps": len(wf.steps), "description": wf.description}
        for wf in _workflows.values()
    ]
    lines = [f"🔄 **{len(items)} workflows:**\n"]
    for item in items:
        lines.append(f"- **{item['name']}** (id: {item['id']}, {item['steps']} steps)")

    return tool_result(success=True, output="\n".join(lines), data=items)


async def _handle_get_workflow_status(args: dict[str, Any]) -> str:
    """Get status of a workflow execution."""
    exec_id = args.get("execution_id", "").strip()
    if not exec_id:
        return tool_error("execution_id is required")

    execution = _executions.get(exec_id)
    if not execution:
        return tool_error(f"Execution '{exec_id}' not found")

    step_data = [
        {
            "name": r.name,
            "status": r.status.value,
            "duration_ms": round(r.duration_ms, 1),
            "error": r.error or None,
        }
        for r in execution.step_results
    ]

    duration_s = round(
        (execution.finished_at or time.time()) - execution.started_at, 2
    ) if execution.started_at else 0.0

    return tool_result(
        success=True,
        output=f"🔄 Execution {exec_id}: {execution.status.value} ({duration_s}s)",
        data={
            "execution_id": exec_id,
            "workflow_id": execution.workflow_id,
            "status": execution.status.value,
            "duration_s": duration_s,
            "steps": step_data,
        },
    )


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

registry.register(
    name="create_workflow",
    toolset="workflow",
    schema={
        "description": (
            "Create a reusable multi-step workflow definition (DAG of tool calls). "
            "Steps can depend on each other for ordered execution."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Workflow name"},
                "description": {"type": "string", "description": "What this workflow does"},
                "steps": {
                    "type": "array",
                    "description": "Ordered steps with tool, params, dependencies",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "tool": {"type": "string", "description": "Registered tool name"},
                            "params": {"type": "object", "description": "Parameters for the tool"},
                            "depends_on": {
                                "type": "array", "items": {"type": "string"},
                                "description": "Step names that must complete first",
                            },
                            "condition": {"type": "string", "description": "Expression to evaluate"},
                            "on_failure": {
                                "type": "string", "enum": ["stop", "continue", "retry"],
                                "description": "Failure handling policy",
                            },
                        },
                        "required": ["name", "tool"],
                    },
                },
            },
            "required": ["name", "steps"],
        },
    },
    handler=_handle_create_workflow,
    is_async=True,
    emoji="🔄",
    category="automation",
)

registry.register(
    name="execute_workflow",
    toolset="workflow",
    schema={
        "description": (
            "Execute a defined workflow or inline steps. Runs steps in dependency "
            "order with parallel execution for independent steps."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "workflow_id": {"type": "string", "description": "ID of saved workflow to execute"},
                "steps": {
                    "type": "array",
                    "description": "Inline steps for one-shot execution (alternative to workflow_id)",
                    "items": {"type": "object"},
                },
                "variables": {
                    "type": "object",
                    "description": "Runtime variables accessible via {{var_name}}",
                },
                "max_parallel": {
                    "type": "integer", "description": "Max concurrent steps (default: 3)",
                },
                "timeout": {
                    "type": "integer", "description": "Total timeout in seconds (default: 600)",
                },
            },
        },
    },
    handler=_handle_execute_workflow,
    is_async=True,
    emoji="▶️",
    category="automation",
)

registry.register(
    name="list_workflows",
    toolset="workflow",
    schema={
        "description": "List all saved workflow definitions.",
        "parameters": {"type": "object", "properties": {}},
    },
    handler=_handle_list_workflows,
    is_async=True,
    emoji="📋",
    category="automation",
)

registry.register(
    name="get_workflow_status",
    toolset="workflow",
    schema={
        "description": "Get the status and step results of a workflow execution.",
        "parameters": {
            "type": "object",
            "properties": {
                "execution_id": {"type": "string", "description": "Execution ID to query"},
            },
            "required": ["execution_id"],
        },
    },
    handler=_handle_get_workflow_status,
    is_async=True,
    emoji="📊",
    category="automation",
)
