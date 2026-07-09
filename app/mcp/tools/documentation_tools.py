"""
MekongAI Nexus - Documentation Generation Tools.

Provides tools for auto-generating API docs, README files, and changelogs
from source code, project structure, and git history.

Self-registers with the tool registry at import time.
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any

import structlog

from app.mcp.tool_registry import registry
from app.mcp.tools.filesystem import get_task_workspace, WORKSPACE_DIR

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_MAX_OUTPUT = 1500
_ROUTE_PATTERN = re.compile(
    r'@(?:app|router|blueprint)\.(get|post|put|patch|delete|route)\(\s*["\']([^"\']+)["\']'
)
_CONVENTIONAL_PATTERN = re.compile(
    r"^(\w+)(?:\(([^)]*)\))?:\s*(.+)$"
)
_MANIFEST_FILES = {
    "pyproject.toml": "Python",
    "setup.py": "Python",
    "package.json": "Node.js",
    "Cargo.toml": "Rust",
    "go.mod": "Go",
    "pom.xml": "Java",
    "build.gradle": "Java/Kotlin",
    "Gemfile": "Ruby",
    "composer.json": "PHP",
}


def _truncate(text: str, limit: int = _MAX_OUTPUT) -> str:
    """Truncate text with ellipsis indicator."""
    if len(text) <= limit:
        return text
    return text[:limit] + "..."


# ---------------------------------------------------------------------------
# Tool 1: generate_api_docs
# ---------------------------------------------------------------------------


async def _handle_generate_api_docs(arguments: dict) -> str:
    """Generate OpenAPI/Swagger documentation from Python source code."""
    try:
        source_path_str = arguments["source_path"]
        framework = arguments.get("framework", "fastapi")
        output_path_str = arguments.get("output_path")
        output_format = arguments.get("format", "yaml")

        # Reject absolute paths
        if Path(source_path_str).is_absolute():
            return "Error: Absolute source paths are not allowed. Use a relative path."
        if output_path_str and Path(output_path_str).is_absolute():
            return "Error: Absolute output paths are not allowed. Use a relative path."

        # Resolve source: try task workspace first, then global
        ws = get_task_workspace()
        source_path = ws / source_path_str
        if not source_path.exists():
            source_path = WORKSPACE_DIR / source_path_str

        if not source_path.exists():
            return f"Error: source_path '{source_path_str}' does not exist"

        # Collect Python files
        py_files: list[Path] = []
        if source_path.is_file():
            py_files = [source_path]
        else:
            py_files = list(source_path.rglob("*.py"))

        endpoints: list[dict[str, Any]] = []

        for py_file in py_files:
            try:
                content = py_file.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue

            lines = content.split("\n")
            for i, line in enumerate(lines):
                match = _ROUTE_PATTERN.search(line)
                if not match:
                    continue

                method = match.group(1).upper()
                path = match.group(2)
                if method == "ROUTE":
                    method = "GET"

                # Extract function name and docstring
                func_name = ""
                docstring = ""
                for j in range(i + 1, min(i + 10, len(lines))):
                    func_match = re.match(r"\s*(?:async\s+)?def\s+(\w+)", lines[j])
                    if func_match:
                        func_name = func_match.group(1)
                        # Look for docstring
                        for k in range(j + 1, min(j + 5, len(lines))):
                            doc_match = re.match(r'\s*"""(.+?)"""', lines[k])
                            if doc_match:
                                docstring = doc_match.group(1).strip()
                                break
                            doc_start = re.match(r'\s*"""(.+)', lines[k])
                            if doc_start:
                                docstring = doc_start.group(1).strip()
                                break
                        break

                endpoints.append({
                    "path": path,
                    "method": method,
                    "operation_id": func_name,
                    "description": docstring,
                    "source": str(py_file),
                })

        # Build OpenAPI 3.0 skeleton
        openapi_doc: dict[str, Any] = {
            "openapi": "3.0.0",
            "info": {
                "title": "Auto-generated API Documentation",
                "version": "1.0.0",
            },
            "paths": {},
        }

        for ep in endpoints:
            path_key = ep["path"]
            if path_key not in openapi_doc["paths"]:
                openapi_doc["paths"][path_key] = {}

            operation: dict[str, Any] = {
                "operationId": ep["operation_id"],
                "summary": ep["description"] or ep["operation_id"],
                "responses": {"200": {"description": "Successful response"}},
            }
            openapi_doc["paths"][path_key][ep["method"].lower()] = operation

        # Serialize
        if output_format == "json":
            content_str = json.dumps(openapi_doc, indent=2)
        else:
            # Simple YAML-like output without requiring PyYAML
            try:
                import yaml  # type: ignore[import-untyped]
                content_str = yaml.dump(openapi_doc, default_flow_style=False, sort_keys=False)
            except ImportError:
                content_str = json.dumps(openapi_doc, indent=2)

        # Output: ALWAYS in task workspace
        output_target = "inline"
        if output_path_str:
            out = ws / output_path_str
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(content_str, encoding="utf-8")
            output_target = str(out)

        n = len(endpoints)
        result = f"Generated API docs: {n} endpoints found | output: {output_target}"
        if output_target == "inline":
            result += f"\n{_truncate(content_str)}"
        return result

    except Exception as e:
        return f"Error generating API docs: {e}"


# ---------------------------------------------------------------------------
# Tool 2: generate_readme
# ---------------------------------------------------------------------------


async def _handle_generate_readme(arguments: dict) -> str:
    """Auto-generate README.md from project structure analysis."""
    try:
        project_path_str = arguments.get("project_path", ".")
        output_path_str = arguments.get("output_path")
        sections = arguments.get("sections") or [
            "title", "description", "installation", "usage",
            "structure", "tech_stack", "license",
        ]

        # Reject absolute paths
        if Path(project_path_str).is_absolute():
            return "Error: Absolute project paths are not allowed. Use a relative path."
        if output_path_str and Path(output_path_str).is_absolute():
            return "Error: Absolute output paths are not allowed. Use a relative path."

        # Resolve source: try task workspace first, then global
        ws = get_task_workspace()
        project_path = ws / project_path_str
        if not project_path.exists():
            project_path = WORKSPACE_DIR / project_path_str

        if not project_path.exists():
            return f"Error: project_path '{project_path_str}' does not exist"

        # Detect project type and info
        project_name = project_path.resolve().name
        description = ""
        tech_stack: list[str] = []
        install_cmd = ""

        for manifest, lang in _MANIFEST_FILES.items():
            manifest_path = project_path / manifest
            if manifest_path.exists():
                tech_stack.append(lang)
                try:
                    manifest_content = manifest_path.read_text(encoding="utf-8", errors="ignore")
                    if manifest == "pyproject.toml":
                        name_match = re.search(r'name\s*=\s*"([^"]+)"', manifest_content)
                        if name_match:
                            project_name = name_match.group(1)
                        desc_match = re.search(r'description\s*=\s*"([^"]+)"', manifest_content)
                        if desc_match:
                            description = desc_match.group(1)
                        install_cmd = "pip install -e ."
                    elif manifest == "package.json":
                        try:
                            pkg = json.loads(manifest_content)
                            project_name = pkg.get("name", project_name)
                            description = pkg.get("description", "")
                        except json.JSONDecodeError:
                            pass
                        install_cmd = "npm install"
                except OSError:
                    pass

        # Scan directory structure (top 2 levels)
        structure_lines: list[str] = []
        try:
            for item in sorted(project_path.iterdir()):
                if item.name.startswith("."):
                    continue
                prefix = "📁" if item.is_dir() else "📄"
                structure_lines.append(f"{prefix} {item.name}/")
                if item.is_dir():
                    try:
                        for sub in sorted(item.iterdir()):
                            if sub.name.startswith("."):
                                continue
                            sub_prefix = "  📁" if sub.is_dir() else "  📄"
                            structure_lines.append(f"{sub_prefix} {sub.name}")
                    except PermissionError:
                        pass
        except PermissionError:
            pass

        # Build README sections
        parts: list[str] = []

        if "title" in sections:
            parts.append(f"# {project_name}\n")

        if "description" in sections:
            desc = description or f"A {'/'.join(tech_stack) if tech_stack else ''} project."
            parts.append(f"{desc}\n")

        if "installation" in sections:
            parts.append("## Installation\n")
            if install_cmd:
                parts.append(f"```bash\n{install_cmd}\n```\n")
            else:
                parts.append("```bash\n# TODO: Add installation instructions\n```\n")

        if "usage" in sections:
            parts.append("## Usage\n")
            parts.append("```bash\n# TODO: Add usage examples\n```\n")

        if "structure" in sections and structure_lines:
            parts.append("## Project Structure\n")
            parts.append("```\n" + "\n".join(structure_lines[:30]) + "\n```\n")

        if "tech_stack" in sections and tech_stack:
            parts.append("## Tech Stack\n")
            for t in tech_stack:
                parts.append(f"- {t}")
            parts.append("")

        if "license" in sections:
            license_file = project_path / "LICENSE"
            if license_file.exists():
                parts.append("## License\n")
                parts.append("See [LICENSE](./LICENSE) for details.\n")

        content = "\n".join(parts)
        section_count = sum(1 for p in parts if p.startswith("## ") or p.startswith("# "))

        # Output: ALWAYS in task workspace
        output_target = "inline"
        if output_path_str:
            out = ws / output_path_str
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(content, encoding="utf-8")
            output_target = str(out)

        result = f"Generated README ({section_count} sections) | {output_target}"
        if output_target == "inline":
            result += f"\n{_truncate(content)}"
        return result

    except Exception as e:
        return f"Error generating README: {e}"


# ---------------------------------------------------------------------------
# Tool 3: generate_changelog
# ---------------------------------------------------------------------------


async def _handle_generate_changelog(arguments: dict) -> str:
    """Generate CHANGELOG from git commit history."""
    try:
        repo_path_str = arguments.get("repo_path", ".")
        since = arguments.get("since")
        fmt = arguments.get("format", "conventional")
        output_path_str = arguments.get("output_path")

        # Reject absolute paths
        if Path(repo_path_str).is_absolute():
            return "Error: Absolute repo paths are not allowed. Use a relative path."
        if output_path_str and Path(output_path_str).is_absolute():
            return "Error: Absolute output paths are not allowed. Use a relative path."

        # Resolve source: try task workspace first, then global
        ws = get_task_workspace()
        repo_path = ws / repo_path_str
        if not repo_path.exists():
            repo_path = WORKSPACE_DIR / repo_path_str

        if not (repo_path / ".git").exists():
            return f"Error: '{repo_path_str}' is not a git repository"

        # Build git log command
        cmd = ["git", "log", "--oneline", "--no-merges"]
        if since:
            # Check if it's a tag or date
            if re.match(r"^v?\d", since):
                cmd = ["git", "log", "--oneline", "--no-merges", f"{since}..HEAD"]
            else:
                cmd.extend([f"--since={since}"])

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(repo_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()

        if proc.returncode != 0:
            err_msg = stderr.decode(errors="ignore").strip()
            return f"Error running git log: {err_msg}"

        raw_commits = stdout.decode(errors="ignore").strip().split("\n")
        raw_commits = [c for c in raw_commits if c.strip()]
        n_commits = len(raw_commits)

        if n_commits == 0:
            return f"Changelog: 0 commits since {since or 'beginning'}"

        # Parse commits
        if fmt == "conventional":
            groups: dict[str, list[str]] = {
                "Features": [],
                "Bug Fixes": [],
                "Documentation": [],
                "Refactor": [],
                "Other": [],
            }
            type_map = {
                "feat": "Features",
                "fix": "Bug Fixes",
                "docs": "Documentation",
                "refactor": "Refactor",
                "chore": "Other",
                "style": "Other",
                "test": "Other",
                "ci": "Other",
                "perf": "Other",
            }

            for line in raw_commits:
                # Remove hash prefix
                parts = line.split(" ", 1)
                if len(parts) < 2:
                    continue
                msg = parts[1]
                match = _CONVENTIONAL_PATTERN.match(msg)
                if match:
                    commit_type = match.group(1).lower()
                    scope = match.group(2) or ""
                    desc = match.group(3)
                    group = type_map.get(commit_type, "Other")
                    scope_str = f"**{scope}**: " if scope else ""
                    groups[group].append(f"- {scope_str}{desc}")
                else:
                    groups["Other"].append(f"- {msg}")

            content_parts = ["# Changelog\n"]
            for group_name, items in groups.items():
                if items:
                    content_parts.append(f"## {group_name}\n")
                    content_parts.extend(items)
                    content_parts.append("")
            content = "\n".join(content_parts)

        elif fmt == "grouped":
            # Group by date - use full git log with dates
            cmd_dated = ["git", "log", "--format=%ad %s", "--date=short", "--no-merges"]
            if since:
                if re.match(r"^v?\d", since):
                    cmd_dated = ["git", "log", "--format=%ad %s", "--date=short", "--no-merges", f"{since}..HEAD"]
                else:
                    cmd_dated.extend([f"--since={since}"])

            proc2 = await asyncio.create_subprocess_exec(
                *cmd_dated,
                cwd=str(repo_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout2, _ = await proc2.communicate()
            dated_lines = stdout2.decode(errors="ignore").strip().split("\n")

            date_groups: dict[str, list[str]] = {}
            for line in dated_lines:
                if not line.strip():
                    continue
                parts = line.split(" ", 1)
                if len(parts) >= 2:
                    date_str = parts[0]
                    msg = parts[1]
                    date_groups.setdefault(date_str, []).append(f"- {msg}")

            content_parts = ["# Changelog\n"]
            for date_str, items in date_groups.items():
                content_parts.append(f"## {date_str}\n")
                content_parts.extend(items)
                content_parts.append("")
            content = "\n".join(content_parts)

        else:  # simple
            content_parts = ["# Changelog\n"]
            for line in raw_commits:
                parts = line.split(" ", 1)
                if len(parts) >= 2:
                    content_parts.append(f"- {parts[1]}")
                else:
                    content_parts.append(f"- {line}")
            content = "\n".join(content_parts)

        # Output: ALWAYS in task workspace
        if output_path_str:
            out = ws / output_path_str
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(content, encoding="utf-8")

        since_label = since or "beginning"
        result = f"Changelog: {n_commits} commits since {since_label}"
        result += f"\n{_truncate(content)}"
        return result

    except Exception as e:
        return f"Error generating changelog: {e}"


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

registry.register(
    name="generate_api_docs",
    toolset="documentation",
    schema={
        "type": "object",
        "properties": {
            "source_path": {
                "type": "string",
                "description": "Path to source directory or file to scan for API routes",
            },
            "framework": {
                "type": "string",
                "description": "Web framework: 'fastapi', 'flask', or 'auto'",
                "default": "fastapi",
            },
            "output_path": {
                "type": "string",
                "description": "File path to write output (omit to return inline)",
            },
            "format": {
                "type": "string",
                "description": "Output format: 'yaml' or 'json'",
                "default": "yaml",
            },
        },
        "required": ["source_path"],
    },
    handler=_handle_generate_api_docs,
    is_async=True,
    emoji="📋",
    category="documentation",
)

registry.register(
    name="generate_readme",
    toolset="documentation",
    schema={
        "type": "object",
        "properties": {
            "project_path": {
                "type": "string",
                "description": "Project root directory path",
                "default": ".",
            },
            "output_path": {
                "type": "string",
                "description": "File path to write README (omit to return inline)",
            },
            "sections": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Sections to include: title, description, installation, usage, structure, tech_stack, license",
            },
        },
        "required": [],
    },
    handler=_handle_generate_readme,
    is_async=True,
    emoji="📖",
    category="documentation",
)

registry.register(
    name="generate_changelog",
    toolset="documentation",
    schema={
        "type": "object",
        "properties": {
            "repo_path": {
                "type": "string",
                "description": "Git repository path",
                "default": ".",
            },
            "since": {
                "type": "string",
                "description": "Since date (e.g. '2024-01-01') or tag (e.g. 'v1.0.0')",
            },
            "format": {
                "type": "string",
                "description": "Output format: 'conventional', 'simple', or 'grouped'",
                "default": "conventional",
            },
            "output_path": {
                "type": "string",
                "description": "File path to write changelog (omit to return inline)",
            },
        },
        "required": [],
    },
    handler=_handle_generate_changelog,
    is_async=True,
    emoji="📝",
    category="documentation",
)
