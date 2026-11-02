# DEPRECATED: Functionality covered by docker_tools.py
"""
Nexus - Deployment Tools.

Register docker_build, docker_deploy into the global tool registry.

DEPRECATED: Use docker_tools.py (docker_build, docker_compose_up/down, etc.) instead.
"""

import warnings
warnings.warn(
    "deployment.py is deprecated, use docker_tools.py",
    DeprecationWarning,
    stacklevel=2,
)

import ast
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import structlog

from app.mcp.tool_registry import registry
from app.sandbox.manager import SandboxManager

logger = structlog.get_logger(__name__)

WORKSPACE_DIR = Path(os.environ.get("WORKSPACE_DIR", "workspace_data"))
WORKSPACE_DIR.mkdir(exist_ok=True)

_sandbox: SandboxManager | None = None


def _get_sandbox() -> SandboxManager:
    global _sandbox
    if _sandbox is None:
        _sandbox = SandboxManager()
    return _sandbox


# ------------------------------------------------------------------
# Pre-deploy Completeness Check
# ------------------------------------------------------------------

# Standard library modules (subset for quick check)
_STDLIB_MODULES = frozenset({
    "os", "sys", "re", "json", "math", "time", "datetime", "pathlib",
    "typing", "collections", "functools", "itertools", "io", "logging",
    "subprocess", "shutil", "tempfile", "hashlib", "base64", "uuid",
    "copy", "enum", "dataclasses", "abc", "contextlib", "asyncio",
    "threading", "multiprocessing", "socket", "http", "urllib",
    "email", "html", "xml", "csv", "sqlite3", "pickle", "struct",
    "traceback", "inspect", "importlib", "pkgutil", "unittest",
    "argparse", "configparser", "string", "textwrap", "operator",
    "decimal", "fractions", "random", "statistics", "secrets",
    "glob", "fnmatch", "platform", "signal", "warnings",
})


def pre_deploy_check(workspace_path: str) -> tuple[bool, list[str]]:
    """Run pre-deployment completeness checks on a workspace.

    Scans workspace for:
    1. All Python imports resolvable (local or in requirements.txt)
    2. All file references in code exist
    3. requirements.txt contains all imported third-party packages
    4. data/ directory exists if code references it
    5. Static files referenced in HTML/JS exist

    Args:
        workspace_path: Path to the workspace directory to check.

    Returns:
        Tuple of (ready: bool, missing_items: list of issue descriptions).
    """
    ws = Path(workspace_path)
    missing: list[str] = []

    if not ws.exists() or not ws.is_dir():
        return False, [f"Workspace path does not exist: {workspace_path}"]

    # Collect all Python files
    py_files: list[Path] = list(ws.rglob("*.py"))

    # Parse requirements.txt
    requirements: set[str] = set()
    req_file = ws / "requirements.txt"
    if req_file.exists():
        for line in req_file.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                pkg = re.split(r"[>=<!~\[]", line)[0].strip().lower()
                pkg = pkg.replace("-", "_")
                if pkg:
                    requirements.add(pkg)
    else:
        if py_files:
            missing.append("requirements.txt not found but Python files exist")

    # Check 1 & 3: Python imports resolvable
    all_imports: set[str] = set()
    local_modules: set[str] = set()

    # Build local module set
    for py_file in py_files:
        rel = py_file.relative_to(ws)
        parts = list(rel.with_suffix("").parts)
        if parts[-1] == "__init__":
            parts = parts[:-1]
        if parts:
            local_modules.add(parts[0])

    for py_file in py_files:
        try:
            source = py_file.read_text(encoding="utf-8", errors="replace")
            tree = ast.parse(source, filename=str(py_file))
        except SyntaxError:
            missing.append(f"Python syntax error in: {py_file.relative_to(ws)}")
            continue
        except Exception:
            continue

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    top_module = alias.name.split(".")[0]
                    all_imports.add(top_module)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    top_module = node.module.split(".")[0]
                    all_imports.add(top_module)

    # Check third-party imports against requirements
    for imp in all_imports:
        imp_lower = imp.lower().replace("-", "_")
        if imp_lower in _STDLIB_MODULES:
            continue
        if imp_lower in local_modules:
            continue
        if imp_lower not in requirements:
            aliases = {
                "pil": "pillow", "cv2": "opencv_python", "sklearn": "scikit_learn",
                "yaml": "pyyaml", "bs4": "beautifulsoup4", "dotenv": "python_dotenv",
                "jwt": "pyjwt", "gi": "pygobject",
            }
            aliased = aliases.get(imp_lower, imp_lower)
            if aliased not in requirements:
                missing.append(f"Import '{imp}' not found in requirements.txt")

    # Check 2: File references in code exist
    file_ref_pattern = re.compile(
        r'''(?:open|send_from_directory|send_file|load|read_text)\s*\(\s*['"]([^'"]+)['"]''',
    )
    for py_file in py_files:
        try:
            source = py_file.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        for match in file_ref_pattern.finditer(source):
            ref_path = match.group(1)
            if ref_path.startswith(("http://", "https://", "/")):
                continue
            if "{" in ref_path or "%" in ref_path:
                continue
            full_ref = ws / ref_path
            if not full_ref.exists():
                alt_ref = py_file.parent / ref_path
                if not alt_ref.exists():
                    missing.append(f"Referenced file not found: '{ref_path}' (from {py_file.relative_to(ws)})")

    # Check 4: data/ directory exists if code references it
    data_ref_pattern = re.compile(r'''['"]data/[^'"]*['"]''')
    code_references_data = False
    for py_file in py_files:
        try:
            source = py_file.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        if data_ref_pattern.search(source):
            code_references_data = True
            break

    if code_references_data and not (ws / "data").exists():
        missing.append("Code references 'data/' directory but it does not exist")

    # Check 5: Static files referenced in HTML/JS exist
    html_files = list(ws.rglob("*.html")) + list(ws.rglob("*.js"))
    static_ref_pattern = re.compile(
        r'''(?:src|href)\s*=\s*['"](?!\s*(?:http|//|data:|#|{))([^'"]+)['"]''',
    )
    for html_file in html_files[:50]:
        try:
            source = html_file.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        for match in static_ref_pattern.finditer(source):
            ref = match.group(1)
            if ref.startswith(("{{", "{%", "<%")):
                continue
            candidates = [
                ws / ref,
                ws / "static" / ref,
                ws / "templates" / ref,
                html_file.parent / ref,
            ]
            if not any(c.exists() for c in candidates):
                missing.append(f"Static file not found: '{ref}' (from {html_file.relative_to(ws)})")

    ready = len(missing) == 0

    if not ready:
        logger.warning(
            "pre_deploy_check_failed",
            workspace=workspace_path,
            issue_count=len(missing),
            issues=missing[:10],
        )
    else:
        logger.info("pre_deploy_check_passed", workspace=workspace_path)

    return ready, missing


# ------------------------------------------------------------------
# Handlers
# ------------------------------------------------------------------


async def handle_docker_build(params: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    """Build Docker image từ Dockerfile trong workspace."""
    logger.warning("deprecated_tool_called", tool="deploy_docker_build", replacement="docker_build")
    dockerfile = params.get("dockerfile", "Dockerfile")
    image_name = params.get("image_name", f"nexus-app-{datetime.now().strftime('%Y%m%d%H%M%S')}")
    context_dir = params.get("context_dir", ".")

    build_path = WORKSPACE_DIR / context_dir
    result = await _get_sandbox().execute_code(
        "bash",
        f"cd {build_path} && docker build -f {dockerfile} -t {image_name} .",
        timeout=1200,
    )
    return {
        "success": result.success,
        "output": result.output or result.error,
        "data": {"image_name": image_name},
        "artifacts": [{"type": "docker_image", "name": image_name}] if result.success else [],
    }


async def handle_docker_deploy(params: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    """Deploy service bằng docker-compose hoặc docker run."""
    logger.warning("deprecated_tool_called", tool="docker_deploy", replacement="docker_compose_up")
    mode = params.get("mode", "compose")
    compose_file = params.get("compose_file", "docker-compose.yml")
    image_name = params.get("image_name", "")
    port = params.get("port", 8080)
    container_name = params.get("container_name", "nexus-deployed-app")

    # Pre-deploy completeness check
    deploy_workspace = str(WORKSPACE_DIR)
    ready, issues = pre_deploy_check(deploy_workspace)
    if not ready:
        issues_text = "\n".join(f"  - {item}" for item in issues[:15])
        return {
            "success": False,
            "output": (
                f"\u274c Pre-deploy check failed ({len(issues)} issues):\n{issues_text}\n\n"
                "Fix these issues before deploying."
            ),
            "data": {"pre_deploy_issues": issues},
            "artifacts": [],
        }

    if mode == "compose":
        result = await _get_sandbox().execute_code(
            "bash",
            f"cd {WORKSPACE_DIR} && docker compose -f {compose_file} up -d",
            timeout=1200,
        )
    else:
        if not image_name:
            return {"success": False, "output": "image_name required", "data": None, "artifacts": []}
        result = await _get_sandbox().execute_code(
            "bash",
            f"docker run -d --name {container_name} -p {port}:{port} {image_name}",
            timeout=600,
        )

    return {
        "success": result.success,
        "output": result.output or result.error,
        "data": {"mode": mode, "port": port},
        "artifacts": [],
    }


# ------------------------------------------------------------------
# Registration
# ------------------------------------------------------------------

_TOOLSET = "deployment"

registry.register(
    name="deploy_docker_build",
    toolset=_TOOLSET,
    schema={
        "description": "Build a Docker image from a Dockerfile in workspace (deployment toolset).",
        "parameters": {
            "type": "object",
            "properties": {
                "dockerfile": {"type": "string", "default": "Dockerfile"},
                "image_name": {"type": "string", "description": "Docker image name:tag"},
                "context_dir": {"type": "string", "default": "."},
            },
            "required": ["image_name"],
        },
    },
    handler=handle_docker_build,
    is_async=True,
    emoji="🐳",
)

registry.register(
    name="docker_deploy",
    toolset=_TOOLSET,
    schema={
        "description": "Deploy a service using docker-compose or docker run.",
        "parameters": {
            "type": "object",
            "properties": {
                "mode": {"type": "string", "enum": ["compose", "run"], "default": "compose"},
                "compose_file": {"type": "string", "default": "docker-compose.yml"},
                "image_name": {"type": "string"},
                "port": {"type": "integer", "default": 8080},
                "container_name": {"type": "string", "default": "nexus-deployed-app"},
            },
        },
    },
    handler=handle_docker_deploy,
    is_async=True,
    emoji="🚀",
)
