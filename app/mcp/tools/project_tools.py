"""
MekongAI Nexus - Project Builder Tools.

Tools for scaffolding new projects and managing dependencies.
Used by ProjectBuilderPipeline but also available to agents directly.

Tools registered:
    - project_scaffold: Create a new project from a template
    - install_dependencies: Install project dependencies
    - build_project: Build a complete project from a description (LLM-driven entry point)
"""

from __future__ import annotations

import asyncio
import os
import re
from pathlib import Path
from typing import Any

import structlog

from app.core.command_guard import check_path_safety
from app.mcp.tool_registry import registry
from app.mcp.tools.filesystem import get_task_workspace

logger = structlog.get_logger(__name__)

WORKSPACE_DIR = Path(os.environ.get("WORKSPACE_DIR", "workspace_data"))
WORKSPACE_DIR.mkdir(exist_ok=True)

# Regex for sanitizing project names to filesystem-safe strings
_SAFE_NAME_RE = re.compile(r"[^a-zA-Z0-9_\-]")

# Maximum timeout for dependency installation (seconds)
_INSTALL_TIMEOUT = 3000


def _sanitize_project_name(name: str) -> str:
    """Sanitize project name for filesystem use.

    Converts to lowercase, replaces spaces with hyphens,
    strips unsafe characters, and limits length.
    """
    sanitized = name.strip().lower().replace(" ", "-")
    sanitized = _SAFE_NAME_RE.sub("", sanitized)
    # Ensure non-empty and reasonable length
    sanitized = sanitized[:64] or "untitled-project"
    return sanitized


def _detect_package_manager(project_path: Path) -> str | None:
    """Auto-detect package manager from project manifest files.

    Returns:
        Detected manager name or None if not detected.
    """
    if (project_path / "package-lock.json").exists():
        return "npm"
    if (project_path / "yarn.lock").exists():
        return "yarn"
    if (project_path / "pnpm-lock.yaml").exists():
        return "pnpm"
    if (project_path / "package.json").exists():
        return "npm"  # Default for Node.js projects
    if (project_path / "pyproject.toml").exists():
        return "pip"
    if (project_path / "requirements.txt").exists():
        return "pip"
    if (project_path / "Pipfile").exists():
        return "pip"
    return None


# ------------------------------------------------------------------
# Handlers
# ------------------------------------------------------------------


async def handle_project_scaffold(params: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    """Scaffold a new project from a template.

    Creates directory structure and writes all template files,
    then optionally writes additional custom files.
    """
    project_name = params.get("project_name", "")
    template = params.get("template", "")
    base_path = params.get("base_path", "")
    custom_files = params.get("custom_files", [])

    if not project_name:
        return {"success": False, "output": "Missing required param: project_name", "data": None, "artifacts": []}
    if not template:
        return {"success": False, "output": "Missing required param: template", "data": None, "artifacts": []}

    # Sanitize project name
    safe_name = _sanitize_project_name(project_name)

    # Determine base path (resolve at runtime for task isolation)
    ws = get_task_workspace()
    if base_path:
        if Path(base_path).is_absolute():
            return {"success": False, "output": "⚠️ Absolute paths are not allowed. Use a relative path.", "data": None, "artifacts": []}
        verdict = check_path_safety(base_path, str(WORKSPACE_DIR))
        if not verdict.safe:
            return {"success": False, "output": f"⚠️ Base path blocked: {verdict.reason}", "data": None, "artifacts": []}
        project_base = ws / base_path
    else:
        project_base = ws / "projects"

    project_dir = project_base / safe_name

    # Check if project already exists
    if project_dir.exists():
        return {
            "success": False,
            "output": f"Project directory already exists: {project_dir.relative_to(WORKSPACE_DIR)}",
            "data": None,
            "artifacts": [],
        }

    # Get template files from ProjectTemplateRegistry
    try:
        from app.project_builder.templates import ProjectTemplateRegistry

        template_registry = ProjectTemplateRegistry()
    except ImportError:
        logger.error("project_template_registry_import_failed")
        return {
            "success": False,
            "output": "ProjectTemplateRegistry not available. Ensure app.project_builder.templates is installed.",
            "data": None,
            "artifacts": [],
        }

    available = template_registry.list_templates()
    if template not in available:
        return {
            "success": False,
            "output": f"Unknown template: {template}. Available: {', '.join(available)}",
            "data": None,
            "artifacts": [],
        }

    scaffold_files = template_registry.get_scaffold_files(template, safe_name)
    if not scaffold_files:
        return {
            "success": False,
            "output": f"Template '{template}' returned no scaffold files",
            "data": None,
            "artifacts": [],
        }

    # Create project directory
    project_dir.mkdir(parents=True, exist_ok=True)

    files_created: list[str] = []
    dirs_created: set[str] = set()
    artifacts: list[dict[str, Any]] = []

    # Write scaffold files
    for file_spec in scaffold_files:
        file_path = project_dir / file_spec.path

        # Create parent directories
        if file_path.parent != project_dir:
            file_path.parent.mkdir(parents=True, exist_ok=True)
            rel_dir = str(file_path.parent.relative_to(WORKSPACE_DIR))
            dirs_created.add(rel_dir)

        file_path.write_text(file_spec.content, encoding="utf-8")
        rel_file = str(file_path.relative_to(WORKSPACE_DIR))
        files_created.append(rel_file)
        artifacts.append({"type": "file", "path": rel_file, "size": len(file_spec.content)})

    # Write custom files
    for cf in custom_files or []:
        cf_path_str = cf.get("path", "")
        cf_content = cf.get("content", "")
        if not cf_path_str:
            continue

        cf_path = project_dir / cf_path_str
        # Safety check: must stay within project dir
        try:
            cf_path.resolve().relative_to(project_dir.resolve())
        except ValueError:
            logger.warning("custom_file_outside_project", path=cf_path_str)
            continue

        cf_path.parent.mkdir(parents=True, exist_ok=True)
        cf_path.write_text(cf_content, encoding="utf-8")
        rel_file = str(cf_path.relative_to(WORKSPACE_DIR))
        files_created.append(rel_file)
        artifacts.append({"type": "file", "path": rel_file, "size": len(cf_content)})

    project_rel = str(project_dir.relative_to(WORKSPACE_DIR))

    logger.info(
        "project_scaffolded",
        project_name=safe_name,
        template=template,
        files_created=len(files_created),
        project_path=project_rel,
    )

    return {
        "success": True,
        "output": (
            f"Project '{safe_name}' scaffolded from template '{template}'.\n"
            f"Location: {project_rel}\n"
            f"Files created: {len(files_created)}\n"
            f"Directories created: {len(dirs_created)}"
        ),
        "data": {
            "project_name": safe_name,
            "project_path": project_rel,
            "template": template,
            "files_created": files_created,
            "dirs_created": sorted(dirs_created),
        },
        "artifacts": artifacts,
    }


async def handle_install_dependencies(params: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    """Install dependencies for a project.

    Auto-detects package manager from project files, or uses
    the explicitly specified one. Runs the install command as
    a subprocess with timeout.
    """
    project_path_str = params.get("project_path", "")
    package_manager = params.get("package_manager", "auto")
    packages = params.get("packages", [])
    dev = params.get("dev", False)

    if not project_path_str:
        return {"success": False, "output": "Missing required param: project_path", "data": None, "artifacts": []}

    # Resolve path
    verdict = check_path_safety(project_path_str, str(WORKSPACE_DIR))
    if not verdict.safe:
        return {"success": False, "output": f"⚠️ Path blocked: {verdict.reason}", "data": None, "artifacts": []}

    project_path = WORKSPACE_DIR / project_path_str
    if not project_path.exists():
        return {"success": False, "output": f"Project path not found: {project_path_str}", "data": None, "artifacts": []}
    if not project_path.is_dir():
        return {"success": False, "output": f"Not a directory: {project_path_str}", "data": None, "artifacts": []}

    # Determine package manager
    if package_manager == "auto" or not package_manager:
        detected = _detect_package_manager(project_path)
        if detected is None:
            return {
                "success": False,
                "output": (
                    f"Could not auto-detect package manager for {project_path_str}. "
                    "No package.json, requirements.txt, or pyproject.toml found."
                ),
                "data": None,
                "artifacts": [],
            }
        package_manager = detected

    # Build command
    cmd: list[str] = []
    pkg_list = " ".join(packages) if packages else ""

    if package_manager == "npm":
        if packages:
            cmd = ["npm", "install"] + (["--save-dev"] if dev else []) + list(packages)
        else:
            cmd = ["npm", "install"]

    elif package_manager == "yarn":
        if packages:
            cmd = ["yarn", "add"] + (["--dev"] if dev else []) + list(packages)
        else:
            cmd = ["yarn", "install"]

    elif package_manager == "pnpm":
        if packages:
            cmd = ["pnpm", "add"] + (["-D"] if dev else []) + list(packages)
        else:
            cmd = ["pnpm", "install"]

    elif package_manager == "pip":
        if packages:
            cmd = ["pip", "install"] + list(packages)
        else:
            # Install from requirements.txt or pyproject.toml
            if (project_path / "requirements.txt").exists():
                cmd = ["pip", "install", "-r", "requirements.txt"]
            elif (project_path / "pyproject.toml").exists():
                cmd = ["pip", "install", "-e", "."]
            else:
                return {
                    "success": False,
                    "output": "No requirements.txt or pyproject.toml found for pip install",
                    "data": None,
                    "artifacts": [],
                }
    else:
        return {
            "success": False,
            "output": f"Unsupported package manager: {package_manager}",
            "data": None,
            "artifacts": [],
        }

    logger.info(
        "install_dependencies_start",
        project_path=project_path_str,
        package_manager=package_manager,
        command=" ".join(cmd),
        packages=packages or "all",
    )

    # Execute command
    try:
        process = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(project_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            process.communicate(),
            timeout=_INSTALL_TIMEOUT,
        )
    except asyncio.TimeoutError:
        logger.error("install_dependencies_timeout", project_path=project_path_str, timeout=_INSTALL_TIMEOUT)
        try:
            process.kill()
        except ProcessLookupError:
            pass
        return {
            "success": False,
            "output": f"Installation timed out after {_INSTALL_TIMEOUT}s",
            "data": {"package_manager": package_manager, "timeout": True},
            "artifacts": [],
        }
    except FileNotFoundError:
        return {
            "success": False,
            "output": f"Command not found: {cmd[0]}. Is {package_manager} installed?",
            "data": {"package_manager": package_manager},
            "artifacts": [],
        }
    except OSError as exc:
        return {
            "success": False,
            "output": f"Failed to run install command: {exc}",
            "data": {"package_manager": package_manager},
            "artifacts": [],
        }

    stdout_text = stdout_bytes.decode("utf-8", errors="replace") if stdout_bytes else ""
    stderr_text = stderr_bytes.decode("utf-8", errors="replace") if stderr_bytes else ""
    exit_code = process.returncode or 0
    success = exit_code == 0

    # Parse errors from stderr
    errors: list[str] = []
    if stderr_text:
        for line in stderr_text.splitlines():
            line_lower = line.lower()
            if "error" in line_lower or "err!" in line_lower or "fatal" in line_lower:
                errors.append(line.strip())

    # Truncate output for response
    max_output = 5000
    combined_output = stdout_text[-max_output:] if len(stdout_text) > max_output else stdout_text
    if stderr_text and not success:
        combined_output += "\n--- STDERR ---\n"
        combined_output += stderr_text[-max_output:] if len(stderr_text) > max_output else stderr_text

    logger.info(
        "install_dependencies_done",
        project_path=project_path_str,
        package_manager=package_manager,
        exit_code=exit_code,
        success=success,
        errors_count=len(errors),
    )

    return {
        "success": success,
        "output": combined_output or ("Dependencies installed successfully" if success else "Installation failed"),
        "data": {
            "package_manager": package_manager,
            "exit_code": exit_code,
            "installed_packages": packages or [],
            "errors": errors[:10],
        },
        "artifacts": [],
    }


# ------------------------------------------------------------------
# Registration
# ------------------------------------------------------------------

_TOOLSET = "project_builder"

registry.register(
    name="project_scaffold",
    toolset=_TOOLSET,
    schema={
        "description": (
            "Create a new project from a template. Available templates: "
            "react-vite-tailwind, next-app, fastapi, fullstack-react-fastapi"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "project_name": {"type": "string", "description": "Project name (will be sanitized for filesystem)"},
                "template": {
                    "type": "string",
                    "description": "Template name",
                    "enum": ["react-vite-tailwind", "next-app", "fastapi", "fullstack-react-fastapi"],
                },
                "base_path": {
                    "type": "string",
                    "description": "Base directory relative to workspace (default: projects/)",
                },
                "custom_files": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string", "description": "File path relative to project root"},
                            "content": {"type": "string", "description": "File content"},
                        },
                        "required": ["path", "content"],
                    },
                    "description": "Additional custom files to create after scaffolding",
                },
            },
            "required": ["project_name", "template"],
        },
    },
    handler=handle_project_scaffold,
    is_async=True,
    emoji="🏗️",
)

registry.register(
    name="install_dependencies",
    toolset=_TOOLSET,
    schema={
        "description": "Install project dependencies. Auto-detects package manager or specify explicitly.",
        "parameters": {
            "type": "object",
            "properties": {
                "project_path": {
                    "type": "string",
                    "description": "Path to project directory (relative to workspace)",
                },
                "package_manager": {
                    "type": "string",
                    "description": "Package manager to use",
                    "enum": ["npm", "pip", "yarn", "pnpm", "auto"],
                },
                "packages": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Specific packages to install (optional — omit to install all from manifest)",
                },
                "dev": {
                    "type": "boolean",
                    "description": "Install as dev dependency",
                    "default": False,
                },
            },
            "required": ["project_path"],
        },
    },
    handler=handle_install_dependencies,
    is_async=True,
    emoji="📦",
)


# ------------------------------------------------------------------
# build_project — LLM-driven entry point for full project builds
# ------------------------------------------------------------------


async def handle_build_project(params: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    """Build a complete project from a description.

    This is the main entry point for the ProjectBuilderPipeline.
    LLM should call this tool when user wants to create/build a full project,
    website, or application.

    Params:
        description: str — Detailed description of the project to build
        template: str — Optional template hint (react-vite-tailwind, next-app, fastapi, fullstack-react-fastapi)
        max_iterations: int — Max iteration cycles (default: 10)

    Returns:
        Detailed build result with project path, server URL, quality score, etc.
    """
    from app.project_builder.pipeline import ProjectBuilderPipeline
    from app.core.execution_context import current_event_callback

    description = params.get("description", "")
    if not description:
        return {"success": False, "output": "Missing required param: description", "data": None, "artifacts": []}

    max_iterations = params.get("max_iterations", 10)

    # Get event callback from execution context (for real-time SSE events)
    event_callback = current_event_callback.get(None)

    pipeline = ProjectBuilderPipeline(
        event_callback=event_callback,
        max_iterations=max_iterations,
    )

    logger.info(
        "build_project_tool_start",
        description_preview=description[:200],
        max_iterations=max_iterations,
    )

    try:
        result = await pipeline.execute(description)
    except Exception as exc:
        logger.error("build_project_tool_failed", error=str(exc))
        return {
            "success": False,
            "output": f"## ⚠️ Project build failed\n\n**Error:** {exc}",
            "data": None,
            "artifacts": [],
        }

    # Format output
    output_parts: list[str] = []
    if result.success:
        output_parts.append("## ✅ Project built successfully!")
        if result.blueprint:
            output_parts.append(f"**Project:** {result.blueprint.name}")
            output_parts.append(
                f"**Tech Stack:** {result.blueprint.tech_stack.frontend} + {result.blueprint.tech_stack.backend}"
            )
        if result.server:
            output_parts.append(f"\n🌐 **Preview URL:** {result.server.url}")
        output_parts.append(f"\n**Quality Score:** {result.final_quality:.0%}")
        output_parts.append(f"**Iterations:** {result.iterations}")
        if result.progress:
            output_parts.append(f"**Files Written:** {result.progress.files_written}")
            output_parts.append(f"**Phases Completed:** {', '.join(result.progress.phases_completed)}")
    else:
        output_parts.append("## ⚠️ Project build encountered issues")
        output_parts.append(f"**Error:** {result.error or 'Unknown error'}")
        if result.progress:
            output_parts.append(f"**Phases Completed:** {', '.join(result.progress.phases_completed)}")

    project_path = str(pipeline.get_project_path()) if pipeline.get_project_path() else ""

    logger.info(
        "build_project_tool_done",
        success=result.success,
        project_path=project_path,
        quality=result.final_quality,
        iterations=result.iterations,
    )

    return {
        "success": result.success,
        "output": "\n".join(output_parts),
        "data": {
            "project_name": result.blueprint.name if result.blueprint else "",
            "project_path": project_path,
            "server_url": result.server.url if result.server else "",
            "quality_score": result.final_quality,
            "iterations": result.iterations,
            "files_written": result.progress.files_written if result.progress else 0,
            "phases_completed": result.progress.phases_completed if result.progress else [],
        },
        "artifacts": [project_path] if project_path else [],
    }


registry.register(
    name="build_project",
    toolset=_TOOLSET,
    schema={
        "description": (
            "Build a complete project (website, web app, API, fullstack application) from a description. "
            "This tool handles the entire process: designing architecture, scaffolding, coding all files, "
            "installing dependencies, building, testing, and starting a preview server. "
            "Use this when the user wants to CREATE, BUILD, or DEVELOP a complete project, application, "
            "or website — regardless of language or how they phrase it."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "description": {
                    "type": "string",
                    "description": "Detailed description of the project to build. Include requirements, features, tech preferences if any.",
                },
                "template": {
                    "type": "string",
                    "description": "Optional template hint",
                    "enum": ["react-vite-tailwind", "next-app", "fastapi", "fullstack-react-fastapi", "auto"],
                    "default": "auto",
                },
                "max_iterations": {
                    "type": "integer",
                    "description": "Maximum fix/iteration cycles",
                    "default": 10,
                },
            },
            "required": ["description"],
        },
    },
    handler=handle_build_project,
    is_async=True,
    emoji="🏗️",
)
