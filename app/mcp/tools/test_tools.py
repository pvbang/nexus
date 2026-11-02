"""
Nexus - Build & Test Tools.

Execute build and test commands for projects.
Parse output for errors using ErrorPatternMatcher.

Tools registered:
    - run_build: Run build/compile command (auto-detect or override)
    - run_tests: Run test suite (auto-detect or override)
    - check_project_health: Quick health check for a project
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

import structlog

from app.core.command_guard import check_path_safety
from app.mcp.tool_registry import registry

logger = structlog.get_logger(__name__)

WORKSPACE_DIR = Path(os.environ.get("WORKSPACE_DIR", "workspace_data"))
WORKSPACE_DIR.mkdir(exist_ok=True)

# Default command timeout (seconds)
_DEFAULT_TIMEOUT = 120


# ------------------------------------------------------------------
# Internal helpers
# ------------------------------------------------------------------


def _resolve_project_path(project_path_str: str) -> tuple[Path | None, str]:
    """Resolve and validate project path within workspace.

    Returns (resolved_path, error_message). If error, path is None.
    """
    if not project_path_str:
        return None, "Missing required param: project_path"

    verdict = check_path_safety(project_path_str, str(WORKSPACE_DIR))
    if not verdict.safe:
        return None, f"⚠️ Path blocked: {verdict.reason}"

    resolved = WORKSPACE_DIR / project_path_str
    if not resolved.exists():
        return None, f"Project path not found: {project_path_str}"
    if not resolved.is_dir():
        return None, f"Not a directory: {project_path_str}"

    return resolved, ""


async def _run_command(
    command: str,
    cwd: Path,
    timeout: int,
) -> tuple[int, str, str]:
    """Execute a shell command and capture output.

    Returns (return_code, stdout, stderr).
    """
    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            cwd=str(cwd),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            proc.communicate(),
            timeout=timeout,
        )
        return (
            proc.returncode or 0,
            stdout_bytes.decode("utf-8", errors="replace"),
            stderr_bytes.decode("utf-8", errors="replace"),
        )
    except asyncio.TimeoutError:
        # Kill the process on timeout
        try:
            proc.kill()  # type: ignore[possibly-undefined]
        except (ProcessLookupError, OSError):
            pass
        return -1, "", f"Command timed out after {timeout}s"
    except Exception as exc:
        return -1, "", f"Command execution failed: {exc}"


async def _detect_build_command(project_path: Path) -> str:
    """Auto-detect build command from project configuration files."""
    # Check package.json (Node.js)
    pkg_json = project_path / "package.json"
    if pkg_json.exists():
        try:
            data = json.loads(pkg_json.read_text(encoding="utf-8"))
            scripts = data.get("scripts", {})
            if "build" in scripts:
                return "npm run build"
            # No build script — try type-check if TS project
            if (project_path / "tsconfig.json").exists():
                return "npx tsc --noEmit"
        except (json.JSONDecodeError, OSError):
            pass

    # Check pyproject.toml (Python)
    if (project_path / "pyproject.toml").exists():
        return "python -m build"

    # Check Makefile
    if (project_path / "Makefile").exists():
        return "make build"

    return ""


async def _detect_test_command(project_path: Path) -> str:
    """Auto-detect test command from project configuration files."""
    # Check package.json (Node.js)
    pkg_json = project_path / "package.json"
    if pkg_json.exists():
        try:
            data = json.loads(pkg_json.read_text(encoding="utf-8"))
            scripts = data.get("scripts", {})
            if "test" in scripts:
                return "npm test"
        except (json.JSONDecodeError, OSError):
            pass

    # Python tests (pytest)
    if (project_path / "tests").is_dir() or (project_path / "test").is_dir():
        return "python -m pytest"

    # Makefile test target
    if (project_path / "Makefile").exists():
        return "make test"

    return ""


def _parse_errors(output: str) -> tuple[list[str], list[str]]:
    """Parse command output for errors and warnings using ErrorPatternMatcher.

    Returns (errors, warnings) as lists of formatted strings.
    """
    errors: list[str] = []
    warnings: list[str] = []

    try:
        from app.project_builder.error_patterns import ErrorPatternMatcher

        matcher = ErrorPatternMatcher()

        for line in output.split("\n"):
            stripped = line.strip()
            if not stripped:
                continue

            match_result = matcher.match(stripped)
            if match_result:
                if match_result.category in ("syntax", "import", "type", "build"):
                    errors.append(f"[{match_result.pattern_name}] {stripped}")
                else:
                    warnings.append(f"[{match_result.pattern_name}] {stripped}")
            elif "warning" in stripped.lower() and "error" not in stripped.lower():
                warnings.append(stripped)
    except ImportError:
        # Fallback: simple keyword scanning
        for line in output.split("\n"):
            stripped = line.strip()
            if not stripped:
                continue
            lower = stripped.lower()
            if "error" in lower:
                errors.append(stripped)
            elif "warning" in lower:
                warnings.append(stripped)

    return errors, warnings


# ------------------------------------------------------------------
# Handlers
# ------------------------------------------------------------------


async def handle_run_build(params: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    """Run build/compile command for a project.

    Auto-detects build command from package.json, pyproject.toml,
    or Makefile when no command override is provided.
    """
    project_path_str = params.get("project_path", "")
    command_override = params.get("command", "")
    timeout = min(max(params.get("timeout", _DEFAULT_TIMEOUT), 10), 600)

    project_path, error = _resolve_project_path(project_path_str)
    if project_path is None:
        return {"success": False, "output": error, "data": None, "artifacts": []}

    # Determine build command
    command = command_override or await _detect_build_command(project_path)
    if not command:
        return {
            "success": False,
            "output": (
                f"No build command found for {project_path_str}. "
                "Provide a 'command' param or add a build script to package.json/pyproject.toml/Makefile."
            ),
            "data": None,
            "artifacts": [],
        }

    logger.info("run_build_start", project=project_path_str, command=command)

    return_code, stdout, stderr = await _run_command(command, project_path, timeout)
    combined_output = (stdout + "\n" + stderr).strip()

    # Parse errors from output
    errors, warnings = _parse_errors(combined_output)

    is_success = return_code == 0

    # Build human-readable output
    output_parts: list[str] = [
        f"Build {'✅ PASSED' if is_success else '❌ FAILED'} (exit code: {return_code})",
        f"Command: {command}",
    ]
    if errors:
        output_parts.append(f"\nErrors ({len(errors)}):")
        output_parts.extend(f"  {e}" for e in errors[:20])
        if len(errors) > 20:
            output_parts.append(f"  ... and {len(errors) - 20} more")
    if warnings:
        output_parts.append(f"\nWarnings ({len(warnings)}):")
        output_parts.extend(f"  {w}" for w in warnings[:10])
        if len(warnings) > 10:
            output_parts.append(f"  ... and {len(warnings) - 10} more")

    # Truncate raw output for data payload
    raw_output = combined_output[:5000]
    if len(combined_output) > 5000:
        raw_output += "\n... (truncated)"

    logger.info(
        "run_build_done",
        project=project_path_str,
        success=is_success,
        error_count=len(errors),
        warning_count=len(warnings),
    )

    return {
        "success": is_success,
        "output": "\n".join(output_parts),
        "data": {
            "return_code": return_code,
            "command": command,
            "errors": errors,
            "warnings": warnings,
            "error_count": len(errors),
            "warning_count": len(warnings),
            "raw_output": raw_output,
        },
        "artifacts": [],
    }


async def handle_run_tests(params: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    """Run test suite for a project.

    Auto-detects test framework (jest, vitest, pytest, etc.)
    when no command override is provided.
    """
    project_path_str = params.get("project_path", "")
    command_override = params.get("command", "")
    timeout = min(max(params.get("timeout", _DEFAULT_TIMEOUT), 10), 600)

    project_path, error = _resolve_project_path(project_path_str)
    if project_path is None:
        return {"success": False, "output": error, "data": None, "artifacts": []}

    # Determine test command
    command = command_override or await _detect_test_command(project_path)
    if not command:
        return {
            "success": False,
            "output": (
                f"No test command found for {project_path_str}. "
                "Provide a 'command' param or add a test script to package.json, "
                "or create a tests/ directory for pytest."
            ),
            "data": None,
            "artifacts": [],
        }

    logger.info("run_tests_start", project=project_path_str, command=command)

    return_code, stdout, stderr = await _run_command(command, project_path, timeout)
    combined_output = (stdout + "\n" + stderr).strip()

    # Parse errors from output
    errors, warnings = _parse_errors(combined_output)

    is_success = return_code == 0

    # Build human-readable output
    output_parts: list[str] = [
        f"Tests {'✅ PASSED' if is_success else '❌ FAILED'} (exit code: {return_code})",
        f"Command: {command}",
    ]
    if errors:
        output_parts.append(f"\nErrors ({len(errors)}):")
        output_parts.extend(f"  {e}" for e in errors[:20])
        if len(errors) > 20:
            output_parts.append(f"  ... and {len(errors) - 20} more")
    if warnings:
        output_parts.append(f"\nWarnings ({len(warnings)}):")
        output_parts.extend(f"  {w}" for w in warnings[:10])

    raw_output = combined_output[:5000]
    if len(combined_output) > 5000:
        raw_output += "\n... (truncated)"

    logger.info(
        "run_tests_done",
        project=project_path_str,
        success=is_success,
        error_count=len(errors),
    )

    return {
        "success": is_success,
        "output": "\n".join(output_parts),
        "data": {
            "return_code": return_code,
            "command": command,
            "errors": errors,
            "warnings": warnings,
            "error_count": len(errors),
            "warning_count": len(warnings),
            "raw_output": raw_output,
        },
        "artifacts": [],
    }


async def handle_check_project_health(
    params: dict[str, Any], **kwargs: Any
) -> dict[str, Any]:
    """Quick health check for a project.

    Verifies config files exist, dependencies are installed,
    source files are present, and reports what is missing.
    """
    project_path_str = params.get("project_path", "")

    project_path, error = _resolve_project_path(project_path_str)
    if project_path is None:
        return {"success": False, "output": error, "data": None, "artifacts": []}

    checks: list[dict[str, Any]] = []
    is_healthy = True

    # --- Check 1: Config files ---
    config_files = {
        "package.json": "Node.js project config",
        "pyproject.toml": "Python project config",
        "requirements.txt": "Python dependencies",
        "Makefile": "Build automation",
        "tsconfig.json": "TypeScript config",
        "vite.config.ts": "Vite config",
        "vite.config.js": "Vite config",
        "next.config.js": "Next.js config",
        "next.config.mjs": "Next.js config",
    }
    found_configs: list[str] = []
    for filename, desc in config_files.items():
        if (project_path / filename).exists():
            found_configs.append(filename)

    if found_configs:
        checks.append({
            "name": "config_files",
            "status": "pass",
            "message": f"Found: {', '.join(found_configs)}",
        })
    else:
        is_healthy = False
        checks.append({
            "name": "config_files",
            "status": "fail",
            "message": "No project config files found (package.json, pyproject.toml, etc.)",
        })

    # --- Check 2: Dependencies installed ---
    has_node_modules = (project_path / "node_modules").is_dir()
    has_venv = (
        (project_path / "venv").is_dir()
        or (project_path / ".venv").is_dir()
    )
    has_pkg_json = (project_path / "package.json").exists()
    has_python_config = (
        (project_path / "pyproject.toml").exists()
        or (project_path / "requirements.txt").exists()
    )

    if has_pkg_json and not has_node_modules:
        is_healthy = False
        checks.append({
            "name": "node_dependencies",
            "status": "fail",
            "message": "package.json exists but node_modules/ not found. Run: npm install",
        })
    elif has_pkg_json and has_node_modules:
        checks.append({
            "name": "node_dependencies",
            "status": "pass",
            "message": "node_modules/ present",
        })

    if has_python_config and not has_venv:
        checks.append({
            "name": "python_dependencies",
            "status": "warn",
            "message": "Python config found but no venv/.venv directory. Dependencies may be global.",
        })
    elif has_python_config and has_venv:
        checks.append({
            "name": "python_dependencies",
            "status": "pass",
            "message": "Virtual environment present",
        })

    # --- Check 3: Source files exist ---
    src_dirs = ["src", "app", "lib", "pages", "components"]
    found_src: list[str] = []
    for d in src_dirs:
        if (project_path / d).is_dir():
            found_src.append(d)

    # Also check for root-level source files
    root_source_exts = {".py", ".ts", ".tsx", ".js", ".jsx"}
    root_sources = [
        f.name for f in project_path.iterdir()
        if f.is_file() and f.suffix in root_source_exts
    ]

    if found_src or root_sources:
        detail = []
        if found_src:
            detail.append(f"Dirs: {', '.join(found_src)}")
        if root_sources:
            detail.append(f"Root files: {len(root_sources)}")
        checks.append({
            "name": "source_files",
            "status": "pass",
            "message": "; ".join(detail),
        })
    else:
        is_healthy = False
        checks.append({
            "name": "source_files",
            "status": "fail",
            "message": "No source directories (src/, app/, etc.) or source files found",
        })

    # --- Check 4: Entry point exists ---
    entry_files = [
        "index.html", "index.ts", "index.tsx", "index.js",
        "main.ts", "main.tsx", "main.js", "main.py",
        "app.py", "manage.py", "server.ts", "server.js",
    ]
    found_entries: list[str] = []
    for ef in entry_files:
        # Check root and src/ directory
        if (project_path / ef).exists():
            found_entries.append(ef)
        elif (project_path / "src" / ef).exists():
            found_entries.append(f"src/{ef}")

    if found_entries:
        checks.append({
            "name": "entry_points",
            "status": "pass",
            "message": f"Found: {', '.join(found_entries)}",
        })
    else:
        checks.append({
            "name": "entry_points",
            "status": "warn",
            "message": "No standard entry point files found",
        })

    # Build output
    status_icons = {"pass": "✅", "fail": "❌", "warn": "⚠️"}
    output_lines = [
        f"Project Health: {'✅ Healthy' if is_healthy else '❌ Issues found'}",
        f"Path: {project_path_str}",
        "",
    ]
    for check in checks:
        icon = status_icons.get(check["status"], "❔")
        output_lines.append(f"  {icon} {check['name']}: {check['message']}")

    logger.info(
        "check_project_health_done",
        project=project_path_str,
        healthy=is_healthy,
        check_count=len(checks),
    )

    return {
        "success": True,
        "output": "\n".join(output_lines),
        "data": {
            "healthy": is_healthy,
            "checks": checks,
            "project_path": project_path_str,
        },
        "artifacts": [],
    }


# ------------------------------------------------------------------
# Registration
# ------------------------------------------------------------------

_TOOLSET = "build_test"

registry.register(
    name="run_build",
    toolset=_TOOLSET,
    schema={
        "description": (
            "Run build/compile command for a project. Auto-detects build command "
            "from package.json, pyproject.toml, or Makefile."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "project_path": {
                    "type": "string",
                    "description": "Path to project directory (relative to workspace)",
                },
                "command": {
                    "type": "string",
                    "description": "Build command override (auto-detect if empty)",
                },
                "timeout": {
                    "type": "integer",
                    "description": "Timeout in seconds (default: 120, max: 600)",
                    "default": 120,
                },
            },
            "required": ["project_path"],
        },
    },
    handler=handle_run_build,
    is_async=True,
    emoji="🔨",
)

registry.register(
    name="run_tests",
    toolset=_TOOLSET,
    schema={
        "description": (
            "Run test suite for a project. Auto-detects test framework "
            "(jest, vitest, pytest, etc.)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "project_path": {
                    "type": "string",
                    "description": "Path to project directory (relative to workspace)",
                },
                "command": {
                    "type": "string",
                    "description": "Test command override (auto-detect if empty)",
                },
                "timeout": {
                    "type": "integer",
                    "description": "Timeout in seconds (default: 120, max: 600)",
                    "default": 120,
                },
            },
            "required": ["project_path"],
        },
    },
    handler=handle_run_tests,
    is_async=True,
    emoji="🧪",
)

registry.register(
    name="check_project_health",
    toolset=_TOOLSET,
    schema={
        "description": (
            "Quick health check for a project: verify config files exist, "
            "dependencies installed, source files present."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "project_path": {
                    "type": "string",
                    "description": "Path to project directory (relative to workspace)",
                },
            },
            "required": ["project_path"],
        },
    },
    handler=handle_check_project_health,
    is_async=True,
    emoji="🏥",
)
