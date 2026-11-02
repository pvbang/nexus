"""
Nexus - Environment Details Builder.

Builds workspace context information to inject into each LLM call.
Informs the agent about current workspace state, available files,
stale warnings, and file editing rules.

Inspired by Roo-Code's environment_details injection per turn.
"""

import asyncio
import socket
from typing import Any, Optional
from pathlib import Path

import structlog

from app.core.file_context_tracker import FileContextTracker
from app.core.workspace_manager import WorkspaceManager

logger = structlog.get_logger(__name__)


def build_environment_details(
    tracker: Optional[FileContextTracker] = None,
    workspace: Optional[WorkspaceManager] = None,
    workspace_inventory: Optional[str] = None,
) -> str:
    """
    Build environment details text to append to user messages or system prompt.

    Args:
        tracker: FileContextTracker for the current task (if available)
        workspace: WorkspaceManager for the current task (if available)
        workspace_inventory: Pre-built inventory text (if workspace already scanned)

    Returns:
        Formatted environment details string
    """
    sections = []

    # Section 1: Workspace files
    sections.append("# Workspace Files")
    if workspace_inventory:
        sections.append(workspace_inventory)
    elif tracker and tracker.active_count > 0:
        sections.append(tracker.get_workspace_summary())
    else:
        sections.append("(No files in workspace yet. Use write_file to create new files.)")

    # Section 2: Recently edited files (if any)
    if tracker:
        edited_text = tracker.get_recently_edited_text()
        if edited_text != "(None)":
            sections.append("\n# Recently Edited")
            sections.append(edited_text)

    # Section 3: Stale file warnings (if any)
    if tracker:
        stale_files = tracker.get_stale_files()
        if stale_files:
            sections.append("\n# ⚠️ Stale Files (MUST re-read before editing)")
            sections.append(tracker.get_stale_files_text())

    return "\n".join(sections)


# Pre-built file editing rules for system prompt injection
FILE_EDITING_RULES = """
## FILE EDITING RULES (MANDATORY)

You have access to file tools for managing workspace content. Follow these rules strictly:

### Reading Files
- Use `read_file` to view file content WITH line numbers
- Output format: "  1 | first line\\n  2 | second line..."
- Use `offset` and `limit` params to paginate large files
- ALWAYS check total_lines in response metadata

### Editing Files — `edit_file` is the ONE tool for all edits
`edit_file` auto-detects the edit mode from which parameters you send. Pick ONE
mode per call:

- **String** (default — use for most edits): `old_string` + `new_string`
  - `old_string` must match the file exactly (whitespace/indentation included)
  - If exact match fails, a whitespace-tolerant line match is tried automatically
  - Must be UNIQUE; add surrounding context, or set `occurrence` (0=all, N=Nth)
  - `new_string: ""` deletes the matched text
- **Batch**: `edits` — array of {old_string, new_string} applied atomically
  (all succeed or none — transactional, never leaves a half-edited file)
- **Regex**: `pattern` + `replacement` (use `\1`, `\2` for capture groups;
  `occurrence` 0=all, N=Nth)
- **Line span**: `start_line` + `end_line` + `expected_text` (+ `new_string`)
  - `expected_text` MUST equal the current content of those lines — a safety
    guard against stale line numbers. `new_string: ""` deletes the span.
- **Anchor insert**: `after` OR `before` + `new_string`
  - Inserts new_string next to a marker WITHOUT removing it (ideal for adding
    imports, methods, config keys)

Shared options: `occurrence`, `create_if_missing` (create file if absent),
`dry_run` (preview without writing).

- Use `write_file` ONLY for:
  - Creating brand NEW files
  - Complete file rewrites (rare — prefer edit_file)

### Critical Rules
1. ALWAYS `read_file` before editing — never assume file content from memory
2. NEVER use write_file to modify existing files — use edit_file instead
3. Prefer paths RELATIVE to your workspace (e.g. `report.md`, `recon/notes.md`).
   The tools also accept a full `workspace_data/tasks/<id>/_work/...` or
   `tasks/<id>/_work/...` prefix — all of these resolve to the same file in
   your task workspace. A plain relative path is simplest and recommended.
4. Use `list_files` to check what files exist before creating duplicates
5. When an edit fails with "no match", the error quotes the closest region —
   use it to correct old_string, or re-read the file
6. Include 3-5 lines of context in old_string to ensure uniqueness
7. After editing, the tool returns confirmation — no need to re-read to verify

### File Naming
- Reports/Analysis: `{topic}_{date}.md`
- Code: `meaningful_name.{ext}`
- Data output: `data/{name}.{ext}`
- Never create files with generic names like "output.txt" or "result.md"
"""


# ================================================================
# Runtime Detection
# ================================================================

_RUNTIME_CHECK_TIMEOUT = 5  # seconds per check


async def _check_command_version(cmd: str, *args: str) -> Optional[str]:
    """Run a command and return its output (version string), or None on failure."""
    try:
        proc = await asyncio.create_subprocess_exec(
            cmd, *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(
            proc.communicate(), timeout=_RUNTIME_CHECK_TIMEOUT,
        )
        if proc.returncode == 0 and stdout:
            return stdout.decode().strip().split("\n")[0]
        return None
    except (FileNotFoundError, asyncio.TimeoutError, OSError):
        return None


async def _check_port_available(port: int) -> bool:
    """Check if a TCP port is available for binding."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(1)
        result = sock.connect_ex(("127.0.0.1", port))
        sock.close()
        # connect_ex returns 0 if port is IN USE (something listening)
        # We want to know if port is available (nothing listening)
        return result != 0
    except OSError:
        return False


async def _check_dns_resolve() -> bool:
    """Test public network access via DNS resolution."""
    try:
        loop = asyncio.get_event_loop()
        await asyncio.wait_for(
            loop.run_in_executor(None, socket.getaddrinfo, "dns.google", 443),
            timeout=_RUNTIME_CHECK_TIMEOUT,
        )
        return True
    except (socket.gaierror, asyncio.TimeoutError, OSError):
        return False


async def detect_available_runtimes() -> dict[str, Any]:
    """Detect available runtimes and tools in the current environment.

    Runs checks in parallel with timeout per check. Used to inform
    task planning about what technologies are actually available.

    Returns:
        Dict with keys:
            python: version string or None
            node: version string or None
            npm: version string or None
            docker: bool
            git: bool
            public_network: bool
            available_ports: list[int] (from [3000, 5000, 8000, 8080])
            tunnel_tools: list[str] (e.g. ["ngrok", "cloudflared"])
    """
    # Run all checks concurrently
    python_ver, node_ver, npm_ver, docker_ver, git_ver, network = (
        await asyncio.gather(
            _check_command_version("python", "--version"),
            _check_command_version("node", "--version"),
            _check_command_version("npm", "--version"),
            _check_command_version("docker", "--version"),
            _check_command_version("git", "--version"),
            _check_dns_resolve(),
            return_exceptions=True,
        )
    )

    # Safely extract results (gather with return_exceptions=True)
    def _safe_str(val: Any) -> Optional[str]:
        if isinstance(val, BaseException):
            return None
        return val

    def _safe_bool(val: Any) -> bool:
        if isinstance(val, BaseException):
            return False
        return bool(val)

    # Check ports
    ports_to_check = [3000, 5000, 8000, 8080]
    port_results = await asyncio.gather(
        *[_check_port_available(p) for p in ports_to_check],
        return_exceptions=True,
    )
    available_ports = [
        port for port, available in zip(ports_to_check, port_results)
        if not isinstance(available, BaseException) and available
    ]

    # Check tunnel tools
    tunnel_checks = await asyncio.gather(
        _check_command_version("ngrok", "version"),
        _check_command_version("cloudflared", "--version"),
        return_exceptions=True,
    )
    tunnel_tools: list[str] = []
    if not isinstance(tunnel_checks[0], BaseException) and tunnel_checks[0]:
        tunnel_tools.append("ngrok")
    if not isinstance(tunnel_checks[1], BaseException) and tunnel_checks[1]:
        tunnel_tools.append("cloudflared")

    result = {
        "python": _safe_str(python_ver),
        "node": _safe_str(node_ver),
        "npm": _safe_str(npm_ver),
        "docker": _safe_bool(docker_ver),
        "git": _safe_bool(git_ver),
        "public_network": _safe_bool(network),
        "available_ports": available_ports,
        "tunnel_tools": tunnel_tools,
    }

    logger.info("environment_runtimes_detected", **result)
    return result


def format_runtime_constraints(runtimes: dict[str, Any]) -> str:
    """Format detected runtimes into an informational text for LLM prompts.

    Args:
        runtimes: Output from detect_available_runtimes()

    Returns:
        Human-readable environment status string to inject into planning prompts.
    """
    lines: list[str] = ["## Environment Status (Informational — NOT a hard constraint)"]
    lines.append(
        "The following shows what is pre-installed. Agents CAN and SHOULD install "
        "missing tools as needed (npm, node, docker, etc.) if the task requires them. "
        "Network access means anything installable is available."
    )

    # Python
    if runtimes.get("python"):
        lines.append(f"- Python: pre-installed ({runtimes['python']})")
    else:
        lines.append("- Python: not detected on PATH (can be installed)")

    # Node.js
    if runtimes.get("node"):
        lines.append(f"- Node.js: pre-installed ({runtimes['node']})")
        if runtimes.get("npm"):
            lines.append(f"- npm: pre-installed ({runtimes['npm']})")
    else:
        lines.append("- Node.js/npm: not detected on PATH (install with: apt-get install nodejs npm, or nvm, or download)")

    # Docker / Git
    lines.append(f"- Docker: {'pre-installed' if runtimes.get('docker') else 'not detected on PATH (install if needed)'}")
    lines.append(f"- Git: {'pre-installed' if runtimes.get('git') else 'not detected on PATH (install if needed)'}")

    # Network
    if runtimes.get("public_network"):
        lines.append("- Public network: available (can install ANY package/tool on demand)")
    else:
        lines.append("- Public network: not detected (try alternative network methods or work offline)")

    # Ports
    if runtimes.get("available_ports"):
        ports_str = ", ".join(str(p) for p in runtimes["available_ports"])
        lines.append(f"- Available ports: {ports_str}")

    # Tunnel tools
    if runtimes.get("tunnel_tools"):
        lines.append(f"- Tunnel tools: {', '.join(runtimes['tunnel_tools'])}")

    lines.append("")
    lines.append(
        "REMEMBER: 'Not detected' ≠ 'impossible'. If the task needs Node.js, Docker, "
        "or any tool — INSTALL IT. Use package managers, download binaries, or "
        "bootstrap the capability. Never abandon the optimal approach just because "
        "a tool isn't pre-installed."
    )

    return "\n".join(lines)
