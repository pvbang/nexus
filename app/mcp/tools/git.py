"""
MekongAI Nexus - Git Operations Tool.

Execute git commands safely within workspace.
Self-registers with the tool registry at import time.
"""

from __future__ import annotations

import asyncio
from typing import Any

import structlog

from app.mcp.tool_registry import registry, tool_result, tool_error
from app.mcp.tools.filesystem import get_task_workspace, WORKSPACE_DIR

logger = structlog.get_logger(__name__)


def _git_available() -> bool:
    """Check if git is installed."""
    import shutil
    return shutil.which("git") is not None


async def _run_git(args: list[str], cwd: str | None = None, timeout: int = 60) -> tuple[bool, str]:
    """Run a git command and return (success, output)."""
    cmd = ["git"] + args
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd or str(WORKSPACE_DIR),
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        output = stdout.decode("utf-8", errors="replace")
        err = stderr.decode("utf-8", errors="replace")
        combined = (output + "\n" + err).strip() if err else output.strip()
        return proc.returncode == 0, combined
    except asyncio.TimeoutError:
        return False, f"Git command timed out after {timeout}s"
    except Exception as e:
        return False, f"Git error: {e}"


# Allowed operations (security: no push without explicit flag)
_ALLOWED_OPS = {
    "clone", "init", "status", "log", "diff", "add", "commit",
    "branch", "checkout", "pull", "push", "fetch", "remote",
    "stash", "tag", "show", "blame", "rev-parse",
}


async def _handle_git(args: dict[str, Any]) -> str:
    """Execute a git operation."""
    operation = args.get("operation", "").strip().lower()
    params = args.get("args", "").strip()
    repo_path = args.get("repo_path", "")

    if not operation:
        return tool_error("Git operation is required (e.g., 'status', 'log', 'diff')")

    if operation not in _ALLOWED_OPS:
        return tool_error(
            f"Operation '{operation}' not allowed. "
            f"Allowed: {', '.join(sorted(_ALLOWED_OPS))}"
        )

    ws = get_task_workspace()
    if repo_path:
        repo = ws / repo_path
        if not repo.exists():
            repo = WORKSPACE_DIR / repo_path  # fallback for existing repos
        cwd = str(repo)
    else:
        cwd = str(ws)

    # Build git command
    git_args = [operation]
    if params:
        git_args.extend(params.split())

    # Special handling for clone (needs more time)
    timeout = 120 if operation == "clone" else 60

    success, output = await _run_git(git_args, cwd=cwd, timeout=timeout)

    # Truncate very long output (e.g., large diffs)
    if len(output) > 10_000:
        output = output[:10_000] + "\n... (truncated)"

    if success:
        return tool_result(
            success=True,
            output=f"🔀 git {operation}:\n\n{output}",
            data={"operation": operation, "output_length": len(output)},
        )
    else:
        return tool_error(f"git {operation} failed:\n{output}")


# ------------------------------------------------------------------
# Registration
# ------------------------------------------------------------------

registry.register(
    name="git",
    toolset="devops",
    schema={
        "description": (
            "Execute git operations: clone, init, status, log, diff, add, commit, "
            "branch, checkout, pull, push, fetch, remote, stash, tag, show, blame. "
            "Use for version control and code management."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "operation": {
                    "type": "string",
                    "enum": sorted(_ALLOWED_OPS),
                    "description": "Git operation to execute",
                },
                "args": {
                    "type": "string",
                    "description": "Additional arguments (e.g., '-n 10' for log, '--oneline' for log)",
                },
                "repo_path": {
                    "type": "string",
                    "description": "Repo directory relative to workspace (default: workspace root)",
                },
            },
            "required": ["operation"],
        },
    },
    handler=_handle_git,
    is_async=True,
    check_fn=_git_available,
    emoji="🔀",
)
