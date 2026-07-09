"""
MekongAI Nexus - Orchestrator Read-Only Tools.

Gives NexusOrchestrator (and EvolutionAgent) the ability to autonomously
explore the workspace using a tool loop — just like worker agents, but
restricted to READ-ONLY operations.

The orchestrator is the project's brain. It MUST have full visibility
into what exists, what was decided, and what the current state is.
Without tools, it's blind — making decisions based on stale summaries.

Design:
- Tools are READ-ONLY: list_tree, read_file, search_content
- Tool loop runs BEFORE the main evaluation/planning LLM call
- Max rounds capped (prevents runaway loops)
- Results become part of the evaluation context
- LLM decides when it has enough info (or hits max rounds)
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

_WORKSPACE_BASE = Path(os.environ.get("WORKSPACE_DIR", "workspace_data"))

# Orchestrator tool loop limits
_MAX_TOOL_ROUNDS = 10  # GAP-7 FIX: Raised from 5 to 10 for complex workspaces
_MAX_READ_SIZE = 30000  # Max chars returned per file read
_MAX_TREE_DEPTH = 4  # Max directory depth for tree listing

# Skip patterns for tree listing
_SKIP_DIRS = {"__pycache__", ".git", "node_modules", ".venv", "venv"}
_SKIP_EXTENSIONS = {".pyc", ".bin", ".exe", ".dll", ".so", ".png", ".jpg", ".gif"}


def _read_dedup_key(tool_name: str, tool_args: dict) -> str:
    """Stable key identifying a read so repeats can be detected.

    The orchestrator's exploration loop sometimes re-reads the same file or
    re-runs the same search across rounds — pure wasted LLM round-trips that make
    evaluation feel "rề rà". Keying on (tool, primary-arg) lets the loop skip the
    redundant execution and nudge the LLM toward finishing.
    """
    arg = ""
    if tool_name == "read_workspace_file":
        arg = str(tool_args.get("path", ""))
    elif tool_name == "search_workspace":
        arg = str(tool_args.get("query", ""))
    elif tool_name == "workspace_tree":
        arg = "*"  # tree takes no meaningful arg — one read is always enough
    return f"{tool_name}:{arg.strip().lower()}"


# ─── Tool Definitions (schemas for LLM) ─────────────────────────────────

ORCHESTRATOR_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "workspace_tree",
            "description": (
                "List the FULL directory tree of the task workspace (both _context/ and _work/). "
                "Shows all files with sizes. Use this FIRST to understand what exists. "
                "Always call this at the start to get a complete picture."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_workspace_file",
            "description": (
                "Read the content of a specific file in the workspace. "
                "Use after workspace_tree to read files you need more detail on. "
                "Supports both _context/ and _work/ files. "
                "For large files, returns first 30KB with a truncation notice."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": (
                            "Relative path within the task workspace. "
                            "Examples: '_context/TASK_BRIEF.md', '_work/ARCHITECTURE.md', "
                            "'_work/schema.prisma', '_context/TODO.md'"
                        ),
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_workspace",
            "description": (
                "Search for a keyword/pattern across all text files in the workspace. "
                "Returns matching lines with file paths. Use to find specific decisions, "
                "tech choices, or patterns across multiple files without reading each one."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Text to search for (case-insensitive substring match)",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Maximum number of matching lines to return (default: 20)",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "done_reading",
            "description": (
                "Signal that you have gathered enough information to make your evaluation/plan. "
                "Call this when you're confident you understand the workspace state. "
                "After calling this, proceed with your evaluation."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "summary": {
                        "type": "string",
                        "description": "Brief summary of what you learned (1-3 sentences)",
                    },
                },
                "required": ["summary"],
            },
        },
    },
]


# ─── Tool Executors ──────────────────────────────────────────────────────

def _execute_workspace_tree(task_id: str, args: dict) -> str:
    """List full directory tree of task workspace."""
    from app.core import task_paths
    base = task_paths.find_task_dir(task_id) or task_paths.task_dir(task_id)

    if not base.exists():
        return "(workspace directory does not exist yet)"

    lines: list[str] = []
    total_size = 0

    def _walk(directory: Path, prefix: str = "", depth: int = 0):
        nonlocal total_size
        if depth > _MAX_TREE_DEPTH:
            lines.append(f"{prefix}... (max depth reached)")
            return

        try:
            entries = sorted(directory.iterdir(), key=lambda p: (not p.is_dir(), p.name))
        except PermissionError:
            return

        for i, entry in enumerate(entries):
            is_last = i == len(entries) - 1
            connector = "└── " if is_last else "├── "
            child_prefix = prefix + ("    " if is_last else "│   ")

            if entry.is_dir():
                if entry.name in _SKIP_DIRS:
                    continue
                lines.append(f"{prefix}{connector}{entry.name}/")
                _walk(entry, child_prefix, depth + 1)
            else:
                if entry.suffix.lower() in _SKIP_EXTENSIONS:
                    continue
                size = entry.stat().st_size
                total_size += size
                size_str = f"{size / 1024:.1f}KB" if size >= 1024 else f"{size}B"
                lines.append(f"{prefix}{connector}{entry.name} ({size_str})")

    lines.append(f"workspace_data/tasks/{task_paths.dir_name(task_id)}/")
    _walk(base)
    lines.append(f"\nTotal: {len(lines) - 1} items, {total_size / 1024:.1f} KB")

    return "\n".join(lines)


def _execute_read_file(task_id: str, args: dict) -> str:
    """Read a specific workspace file."""
    rel_path = args.get("path", "").strip()
    if not rel_path:
        return "(error: 'path' parameter required)"

    # Security: prevent path traversal
    if ".." in rel_path or rel_path.startswith("/"):
        return "(error: path traversal not allowed)"

    from app.core import task_paths
    base = task_paths.find_task_dir(task_id) or task_paths.task_dir(task_id)
    full_path = base / rel_path

    if not full_path.exists():
        return f"(file not found: {rel_path})"

    if not full_path.is_file():
        return f"(not a file: {rel_path})"

    # Check if binary
    try:
        with open(full_path, "rb") as f:
            chunk = f.read(512)
            if b"\x00" in chunk:
                return f"(binary file, {full_path.stat().st_size} bytes — cannot display)"
    except Exception as e:
        return f"(read error: {e})"

    try:
        content = full_path.read_text(encoding="utf-8", errors="replace")
        if len(content) > _MAX_READ_SIZE:
            return content[:_MAX_READ_SIZE] + f"\n\n[TRUNCATED — file is {len(content)} chars total]"
        return content
    except Exception as e:
        return f"(read error: {e})"


def _execute_search(task_id: str, args: dict) -> str:
    """Search workspace files for a query string."""
    query = args.get("query", "").strip().lower()
    if not query:
        return "(error: 'query' parameter required)"

    max_results = min(args.get("max_results", 20), 50)
    from app.core import task_paths
    base = task_paths.find_task_dir(task_id) or task_paths.task_dir(task_id)

    if not base.exists():
        return "(workspace does not exist)"

    matches: list[str] = []

    for file_path in base.rglob("*"):
        if not file_path.is_file():
            continue
        if file_path.suffix.lower() in _SKIP_EXTENSIONS:
            continue
        if any(skip in str(file_path) for skip in _SKIP_DIRS):
            continue

        try:
            content = file_path.read_text(encoding="utf-8", errors="replace")
            rel = str(file_path.relative_to(base))

            for i, line in enumerate(content.splitlines(), 1):
                if query in line.lower():
                    matches.append(f"{rel}:{i}: {line.strip()[:150]}")
                    if len(matches) >= max_results:
                        break

            if len(matches) >= max_results:
                break
        except Exception:
            continue

    if not matches:
        return f"(no matches for '{query}')"

    result = f"Found {len(matches)} matches for '{query}':\n"
    result += "\n".join(matches)
    return result


# ─── Tool Dispatcher ─────────────────────────────────────────────────────

_TOOL_HANDLERS = {
    "workspace_tree": _execute_workspace_tree,
    "read_workspace_file": _execute_read_file,
    "search_workspace": _execute_search,
}


def execute_orchestrator_tool(task_id: str, tool_name: str, args: dict) -> str:
    """Execute a single orchestrator tool call. Returns result string."""
    handler = _TOOL_HANDLERS.get(tool_name)
    if not handler:
        if tool_name == "done_reading":
            return args.get("summary", "")
        return f"(unknown tool: {tool_name})"
    return handler(task_id, args)


# ─── Tool Loop Runner ────────────────────────────────────────────────────

async def run_orchestrator_tool_loop(
    llm: Any,
    task_id: str,
    system_prompt: str,
    initial_context: str,
    max_rounds: int = _MAX_TOOL_ROUNDS,
) -> str:
    """Run orchestrator's read-only tool loop to gather workspace context.

    The LLM can call workspace_tree, read_workspace_file, search_workspace
    until it calls done_reading or hits max_rounds.

    Returns: Accumulated context string (tool results + LLM's summary).
    """
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": initial_context},
    ]

    gathered_context: list[str] = []
    seen_reads: set[str] = set()  # dedup keys of reads already served this loop

    for round_num in range(max_rounds):
        try:
            response = await llm.chat(
                messages=messages,
                tools=ORCHESTRATOR_TOOLS,
                temperature=0.1,
                max_tokens=2048,
            )
        except Exception as e:
            logger.warning("orchestrator_tool_loop_llm_error", round=round_num, error=str(e)[:200])
            break

        # Extract tool calls from response
        tool_calls = _extract_tool_calls(response)
        content = response.get("content", "") if isinstance(response, dict) else ""

        if not tool_calls:
            # LLM responded without tools — it's done gathering
            if content:
                gathered_context.append(content)
            break

        # Process each tool call
        tool_results: list[dict[str, str]] = []
        done = False

        for tc in tool_calls:
            tool_name = tc.get("name", "")
            tool_args = tc.get("arguments", {})
            tool_call_id = tc.get("id", f"call_{round_num}_{tool_name}")

            if tool_name == "done_reading":
                summary = tool_args.get("summary", "")
                if summary:
                    gathered_context.append(f"[Orchestrator insight: {summary}]")
                done = True
                tool_results.append({
                    "tool_call_id": tool_call_id,
                    "output": "OK — proceeding with evaluation.",
                })
                break

            # Skip re-reads: if this exact read was already served, don't spend a
            # tool execution on it — return a short reminder so the LLM moves on
            # instead of looping over the same file/search (audit #4: "rề rà").
            _dedup_key = _read_dedup_key(tool_name, tool_args)
            if _dedup_key in seen_reads:
                tool_results.append({
                    "tool_call_id": tool_call_id,
                    "output": (
                        "[Already read this in an earlier round — see prior result. "
                        "Call done_reading(summary) if you have enough context.]"
                    ),
                })
                continue
            seen_reads.add(_dedup_key)

            # Execute read-only tool
            result = execute_orchestrator_tool(task_id, tool_name, tool_args)
            gathered_context.append(f"[{tool_name}({json.dumps(tool_args, ensure_ascii=False)[:100]})]\n{result}")

            tool_results.append({
                "tool_call_id": tool_call_id,
                "output": result[:_MAX_READ_SIZE],
            })

        # Build assistant message in OpenAI format
        # tool_calls must be in API format for conversation history
        api_tool_calls = []
        for tc in tool_calls:
            api_tool_calls.append({
                "id": tc.get("id", f"call_{round_num}"),
                "type": "function",
                "function": {
                    "name": tc.get("name", ""),
                    "arguments": json.dumps(tc.get("arguments", {}), ensure_ascii=False),
                },
            })

        assistant_msg: dict[str, Any] = {"role": "assistant", "content": content or None}
        if api_tool_calls:
            assistant_msg["tool_calls"] = api_tool_calls
        messages.append(assistant_msg)

        # Append tool results as separate messages (OpenAI format)
        for i, tr in enumerate(tool_results):
            messages.append({
                "role": "tool",
                "tool_call_id": tr["tool_call_id"],
                "content": tr["output"],
            })

        if done:
            break

    return "\n\n".join(gathered_context)


def _extract_tool_calls(response: Any) -> list[dict[str, Any]]:
    """Extract tool calls from LLM response into normalized format.

    Handles:
    - OpenAI: response["tool_calls"] = [{"id", "type", "function": {"name", "arguments"}}]
    - Anthropic: response["content"] = [{"type": "tool_use", "id", "name", "input"}]
    - Direct: response["tool_calls"] = [{"id", "name", "arguments"}]

    Returns normalized: [{"id": str, "name": str, "arguments": dict}]
    """
    if not isinstance(response, dict):
        return []

    # ── OpenAI-style: response["tool_calls"] ──
    if "tool_calls" in response:
        raw_calls = response["tool_calls"]
        if not isinstance(raw_calls, list):
            return []

        normalized = []
        for tc in raw_calls:
            if not isinstance(tc, dict):
                continue

            # OpenAI format: {"id", "type": "function", "function": {"name", "arguments"}}
            if "function" in tc:
                func = tc["function"]
                args = func.get("arguments", "{}")
                # arguments is JSON string in OpenAI format — parse it
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except (json.JSONDecodeError, TypeError):
                        args = {}
                normalized.append({
                    "id": tc.get("id", ""),
                    "name": func.get("name", ""),
                    "arguments": args if isinstance(args, dict) else {},
                })
            # Direct format: {"id", "name", "arguments"}
            elif "name" in tc:
                args = tc.get("arguments", {})
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except (json.JSONDecodeError, TypeError):
                        args = {}
                normalized.append({
                    "id": tc.get("id", ""),
                    "name": tc["name"],
                    "arguments": args if isinstance(args, dict) else {},
                })

        return normalized

    # ── Anthropic-style: response["content"] contains tool_use blocks ──
    content = response.get("content", "")
    if isinstance(content, list):
        tool_calls = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                tool_calls.append({
                    "id": block.get("id", ""),
                    "name": block.get("name", ""),
                    "arguments": block.get("input", {}),
                })
        if tool_calls:
            return tool_calls

    return []
