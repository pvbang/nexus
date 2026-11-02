"""
Nexus - Terminal Tools.

Register execute_command, execute_code into the global tool registry.
"""

import os
import re
from pathlib import Path
from typing import Any

import structlog

from app.core.command_guard import CommandGuard, get_command_guard
from app.mcp.tool_registry import registry
from app.mcp.tools.filesystem import WORKSPACE_DIR, get_task_workspace
from app.sandbox.manager import SandboxManager

logger = structlog.get_logger(__name__)

# Shared instances (lazy-initialized)
_sandbox: SandboxManager | None = None
_guard: CommandGuard | None = None


def _get_sandbox() -> SandboxManager:
    global _sandbox
    if _sandbox is None:
        _sandbox = SandboxManager()
    return _sandbox


def _get_guard() -> CommandGuard:
    global _guard
    if _guard is None:
        _guard = get_command_guard()
    return _guard


def _validate_cwd_in_workspace(cwd: str, workspace: Path) -> bool:
    """Ensure cwd is within workspace bounds."""
    try:
        resolved = Path(cwd).resolve()
        ws_resolved = workspace.resolve()
        return str(resolved).startswith(str(ws_resolved))
    except (ValueError, OSError):
        return False


def _combine_output(result: Any) -> str:
    """Build the text the agent sees from a sandbox result.

    Audit d6c33621: returning ``result.output or result.error`` DISCARDED the
    traceback whenever a script printed anything to stdout before crashing — the
    non-empty stdout header won and the stderr traceback was lost. The agent then
    debugged blind and mis-diagnosed the crash for 6 turns. On FAILURE we now
    surface BOTH streams (stdout first, then a clearly-marked stderr block) so the
    real error is always visible. On success, stdout is returned unchanged.
    """
    out = (result.output or "").rstrip()
    err = (result.error or "").rstrip()
    if result.success:
        return result.output or result.error
    if out and err:
        return f"{out}\n--- stderr ---\n{err}"
    return out or err


# ------------------------------------------------------------------
# Handlers
# ------------------------------------------------------------------


async def handle_execute_command(params: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    """Chạy shell command trong sandbox với dangerous command detection.

    Supports optional ``cwd`` parameter to set working directory before execution.
    """
    command = params.get("command", "")
    timeout = params.get("timeout", 30)
    cwd = params.get("cwd", "")

    if not command:
        return {"success": False, "output": "No command provided", "data": None, "artifacts": []}

    verdict = _get_guard().check_command(command)
    if not verdict.allowed:
        logger.warning(
            "dangerous_command_blocked",
            command=command[:200],
            risk=verdict.risk.value,
            reason=verdict.reason,
        )
        return {
            "success": False,
            "output": (
                f"⚠️ Command blocked ({verdict.risk.value} risk): {verdict.reason}\n"
                f"Command: {command[:100]}{'...' if len(command) > 100 else ''}"
            ),
            "data": {"blocked": True, "risk": verdict.risk.value, "reason": verdict.reason},
            "artifacts": [],
        }

    if verdict.is_dangerous:
        logger.warning(
            "dangerous_command_allowed",
            command=command[:200],
            risk=verdict.risk.value,
            reason=verdict.reason,
        )

    # Resolve workspace for cwd validation
    task_workspace = get_task_workspace()

    # Validate and apply cwd
    effective_command = command
    if cwd and cwd.strip():
        if not os.path.isdir(cwd):
            return {
                "success": False,
                "output": f"Error: cwd directory does not exist: {cwd}",
                "data": None,
                "artifacts": [],
            }
        if not _validate_cwd_in_workspace(cwd, WORKSPACE_DIR):
            logger.warning(
                "cwd_outside_workspace_rejected",
                cwd=cwd,
                workspace=str(WORKSPACE_DIR.resolve()),
            )
            return {
                "success": False,
                "output": (
                    f"Error: cwd must be within the task workspace. "
                    f"Current workspace: {task_workspace}"
                ),
                "data": None,
                "artifacts": [],
            }
        effective_command = f"cd {cwd!r} && {command}"
    else:
        # Default cwd to task workspace
        effective_command = f"cd {str(task_workspace)!r} && {command}"

    result = await _get_sandbox().execute_code("bash", effective_command, timeout=timeout)

    # RC3 self-heal (audit c527e1c2): a command run with CWD=_work/ may write to a
    # path repeating the workspace prefix → nested duplicate. Merge it back. Gate on
    # task context (final-review): without it, get_task_workspace() can fall back to
    # a NEIGHBOUR task's _work/ — we must not sweep someone else's workspace.
    try:
        from app.core.execution_context import current_task_id as _cti
        if _cti.get(""):
            from app.mcp.tools.filesystem import collapse_nested_workspace
            collapse_nested_workspace(task_workspace, WORKSPACE_DIR.name)
    except Exception:
        pass

    return {
        "success": result.success,
        "output": _combine_output(result),
        "data": {
            "exit_code": result.exit_code,
            "execution_time": result.execution_time,
            "command_risk": verdict.risk.value,
            "cwd": cwd or "",
        },
        "artifacts": [],
    }


async def handle_execute_code(params: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    """Execute code trong sandbox với workspace persistence và package support."""
    language = params.get("language", "python")
    code = params.get("code", "")
    timeout = min(params.get("timeout", 30), 300)  # Extended max 300s
    install_packages = params.get("install_packages", [])
    env_vars = params.get("env_vars", {})

    # Pre-install packages if requested (prepend pip install to code)
    if install_packages and language == "python":
        pkg_list = " ".join(install_packages[:10])  # Max 10 packages
        install_cmd = f"import subprocess; subprocess.check_call(['pip', 'install', '-q', {', '.join(repr(p) for p in install_packages[:10])}])\n"
        code = install_cmd + code

    # Set env vars in code if provided
    if env_vars and language == "python":
        env_setup = "import os\n" + "\n".join(
            f"os.environ[{repr(k)}] = {repr(v)}" for k, v in list(env_vars.items())[:20]
        ) + "\n"
        code = env_setup + code

    # Use task workspace if available.
    # NOTE: Sandbox environment restricts filesystem access. The os.chdir() below
    # sets initial CWD but agent code could call os.chdir() again. Full path
    # restriction is enforced at the sandbox/container level.
    try:
        from app.core.execution_context import current_task_id
        task_id = current_task_id.get("")
        if task_id and language == "python":
            ws = get_task_workspace()
            cwd_setup = f"import os; os.chdir({repr(str(ws))})\n"
            code = cwd_setup + code
    except Exception:
        pass

    result = await _get_sandbox().execute_code(language, code, timeout=timeout)

    # RC3 self-heal (audit c527e1c2): agent code run with CWD=_work/ may write to a
    # path that repeats the workspace prefix, creating a nested _work/workspace_data/
    # tasks/<id>/_work/ duplicate. Merge any such tree back into the real _work/.
    try:
        from app.core.execution_context import current_task_id as _cti
        if _cti.get(""):
            from app.mcp.tools.filesystem import collapse_nested_workspace
            collapse_nested_workspace(get_task_workspace(), WORKSPACE_DIR.name)
    except Exception:
        pass

    artifacts = []
    if result.success and ("open(" in code or "write" in code):
        artifacts.append({"type": "code_output", "language": language})

    # Truncate output to 5000 chars
    raw_output = _combine_output(result)
    if raw_output and len(raw_output) > 5000:
        raw_output = raw_output[:5000] + "\n[truncated]"

    return {
        "success": result.success,
        "output": raw_output,
        "data": {
            "language": language,
            "exit_code": result.exit_code,
            "packages_installed": install_packages if install_packages else [],
        },
        "artifacts": artifacts,
    }


async def handle_install_packages(params: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    """Install Python packages in sandbox for use by execute_code."""
    packages = params.get("packages", [])
    if not packages:
        return {"success": False, "output": "No packages specified", "data": None, "artifacts": []}

    # Safety is enforced by ISOLATION (the sandbox), not by a name allowlist:
    # `execute_code` already runs arbitrary Python, so whitelisting pip names protects
    # nothing real while blocking legitimate work across an open-ended task space.
    # The actual risk here is pip ARGUMENT INJECTION — an entry like "--index-url=http://evil"
    # or "-r /etc/x" is parsed by pip as an OPTION, not a package. So we validate that each
    # entry is a well-formed PEP 508 requirement (name + optional extras + optional version),
    # and reject anything that could smuggle a pip flag or shell content.
    PKG_SPEC = re.compile(
        r"^[A-Za-z0-9][A-Za-z0-9._-]*"          # distribution name
        r"(\[[A-Za-z0-9._,-]+\])?"               # optional extras, e.g. [socks]
        r"\s*((==|>=|<=|~=|!=|>|<)\s*[A-Za-z0-9._*+!]+"  # optional version specifier
        r"(\s*,\s*(==|>=|<=|~=|!=|>|<)\s*[A-Za-z0-9._*+!]+)*)?$"
    )

    safe_packages = []
    blocked = []
    for pkg in packages[:10]:
        candidate = pkg.strip()
        # Leading '-' would be read as a pip flag; PKG_SPEC also forbids it, but reject early.
        if candidate and not candidate.startswith("-") and PKG_SPEC.match(candidate):
            safe_packages.append(candidate)
        else:
            blocked.append(pkg)

    if not safe_packages:
        return {
            "success": False,
            "output": (
                f"No valid package specifiers. Rejected: {blocked}. "
                "Each entry must be a PEP 508 requirement, e.g. 'ezdxf', 'cadquery>=2', "
                "'requests[socks]==2.31.0' — not a pip flag or URL."
            ),
            "data": None,
            "artifacts": [],
        }

    # '--' ends pip option parsing: everything after is treated as a positional package
    # spec, so even a validated name can never be reinterpreted as a flag (defense in depth).
    install_code = f"import subprocess; subprocess.check_call(['pip', 'install', '-q', '--', {', '.join(repr(p) for p in safe_packages)}]); print('Installed: ' + ', '.join({repr(safe_packages)}))"
    result = await _get_sandbox().execute_code("python", install_code, timeout=120)

    return {
        "success": result.success,
        "output": result.output or result.error,
        "data": {"installed": safe_packages, "blocked": blocked},
        "artifacts": [],
    }


# ------------------------------------------------------------------
# Registration
# ------------------------------------------------------------------

_TOOLSET = "terminal"

registry.register(
    name="execute_command",
    toolset=_TOOLSET,
    schema={
        "description": "Execute a shell command (bash). Use for git, npm, pip, system operations.",
        "parameters": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Shell command to execute"},
                "timeout": {"type": "integer", "description": "Timeout in seconds (max 300)", "default": 30},
                "cwd": {"type": "string", "description": "Working directory for command execution"},
            },
            "required": ["command"],
        },
    },
    handler=handle_execute_command,
    is_async=True,
    emoji="💻",
)

registry.register(
    name="execute_code",
    toolset=_TOOLSET,
    schema={
        "description": (
            "Execute code in a sandboxed environment. Supports Python, JavaScript, Bash. "
            "Can install packages, set env vars, and use task workspace for file persistence."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "language": {
                    "type": "string",
                    "enum": ["python", "javascript", "bash"],
                    "description": "Programming language",
                },
                "code": {"type": "string", "description": "Code to execute"},
                "timeout": {
                    "type": "integer",
                    "default": 30,
                    "description": "Timeout in seconds (max 300 for complex tasks)",
                },
                "install_packages": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Python packages to install before execution (e.g., ['pandas', 'numpy'])",
                },
                "env_vars": {
                    "type": "object",
                    "description": "Environment variables to set (e.g., {'API_KEY': 'value'})",
                },
            },
            "required": ["language", "code"],
        },
    },
    handler=handle_execute_code,
    is_async=True,
    emoji="⚙️",
)

registry.register(
    name="install_packages",
    toolset=_TOOLSET,
    schema={
        "description": (
            "Install Python packages in sandbox. Packages persist for subsequent execute_code calls. "
            "Common packages allowed: pandas, numpy, scipy, matplotlib, requests, scikit-learn, etc."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "packages": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of package names to install (max 10)",
                },
            },
            "required": ["packages"],
        },
    },
    handler=handle_install_packages,
    is_async=True,
    emoji="📦",
)
