"""
MekongAI Nexus - CI/CD Tools.

Provides tools for generating GitHub Actions workflows, Dockerfiles,
and docker-compose configurations.

Self-registers with the tool registry at import time.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import structlog

from app.mcp.tool_registry import registry
from app.mcp.tools.filesystem import get_task_workspace

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _detect_language(project_path: str) -> str:
    """Auto-detect project language from files."""
    p = Path(project_path)
    if (p / "pyproject.toml").exists() or (p / "requirements.txt").exists():
        return "python"
    if (p / "package.json").exists():
        return "node"
    return "python"  # default fallback


def _truncate(content: str, max_chars: int = 1500) -> str:
    """Truncate content for output display."""
    if len(content) <= max_chars:
        return content
    return content[:max_chars] + "\n... [truncated]"


def _write_or_return(content: str, output_path: str | None) -> str:
    """Write content to file (scoped to task workspace) or return it."""
    if output_path:
        if Path(output_path).is_absolute():
            raise ValueError("Absolute output paths are not allowed. Use a relative path.")
        ws = get_task_workspace()
        p = ws / output_path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return str(p)
    return "inline"


# ---------------------------------------------------------------------------
# GitHub Actions Templates
# ---------------------------------------------------------------------------


def _gh_ci_workflow(language: str, options: dict[str, Any]) -> str:
    """Generate CI workflow YAML."""
    python_version = options.get("python_version", "3.11")
    node_version = options.get("node_version", "20")
    branches = options.get("branches", ["main"])
    branches_str = ", ".join(f'"{b}"' for b in branches)

    if language == "python":
        return f"""name: CI

on:
  push:
    branches: [{branches_str}]
  pull_request:
    branches: [{branches_str}]

jobs:
  ci:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "{python_version}"

      - name: Install dependencies
        run: |
          python -m pip install --upgrade pip
          pip install -e ".[dev]"

      - name: Lint
        run: |
          pip install ruff
          ruff check .

      - name: Test
        run: |
          pip install pytest pytest-cov
          pytest --cov=. --cov-report=xml
"""
    else:  # node
        return f"""name: CI

on:
  push:
    branches: [{branches_str}]
  pull_request:
    branches: [{branches_str}]

jobs:
  ci:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Set up Node.js
        uses: actions/setup-node@v4
        with:
          node-version: "{node_version}"
          cache: "npm"

      - name: Install dependencies
        run: npm ci

      - name: Lint
        run: npm run lint

      - name: Test
        run: npm test
"""


def _gh_cd_workflow(language: str, options: dict[str, Any]) -> str:
    """Generate CD workflow YAML."""
    branches = options.get("branches", ["main"])
    branches_str = ", ".join(f'"{b}"' for b in branches)
    docker_registry = options.get("docker_registry", "ghcr.io")

    return f"""name: CD

on:
  push:
    branches: [{branches_str}]

jobs:
  build-and-push:
    runs-on: ubuntu-latest
    permissions:
      contents: read
      packages: write
    steps:
      - uses: actions/checkout@v4

      - name: Log in to registry
        uses: docker/login-action@v3
        with:
          registry: {docker_registry}
          username: ${{{{ github.actor }}}}
          password: ${{{{ secrets.GITHUB_TOKEN }}}}

      - name: Build and push
        uses: docker/build-push-action@v5
        with:
          context: .
          push: true
          tags: {docker_registry}/${{{{ github.repository }}}}:${{{{ github.sha }}}}
"""


def _gh_test_workflow(language: str, options: dict[str, Any]) -> str:
    """Generate test workflow YAML."""
    python_version = options.get("python_version", "3.11")
    node_version = options.get("node_version", "20")
    branches = options.get("branches", ["main"])
    branches_str = ", ".join(f'"{b}"' for b in branches)

    if language == "python":
        return f"""name: Tests

on:
  push:
    branches: [{branches_str}]
  pull_request:
    branches: [{branches_str}]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "{python_version}"

      - name: Install dependencies
        run: |
          python -m pip install --upgrade pip
          pip install -e ".[dev]"

      - name: Run tests with coverage
        run: |
          pip install pytest pytest-cov
          pytest --cov=. --cov-report=xml --cov-report=html

      - name: Upload coverage
        uses: actions/upload-artifact@v4
        with:
          name: coverage-report
          path: htmlcov/
"""
    else:
        return f"""name: Tests

on:
  push:
    branches: [{branches_str}]
  pull_request:
    branches: [{branches_str}]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Set up Node.js
        uses: actions/setup-node@v4
        with:
          node-version: "{node_version}"
          cache: "npm"

      - name: Install dependencies
        run: npm ci

      - name: Run tests with coverage
        run: npm run test -- --coverage
"""


def _gh_lint_workflow(language: str, options: dict[str, Any]) -> str:
    """Generate lint workflow YAML."""
    python_version = options.get("python_version", "3.11")
    node_version = options.get("node_version", "20")
    branches = options.get("branches", ["main"])
    branches_str = ", ".join(f'"{b}"' for b in branches)

    if language == "python":
        return f"""name: Lint

on:
  push:
    branches: [{branches_str}]
  pull_request:
    branches: [{branches_str}]

jobs:
  lint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "{python_version}"

      - name: Install ruff
        run: pip install ruff

      - name: Run ruff
        run: ruff check . --output-format=github
"""
    else:
        return f"""name: Lint

on:
  push:
    branches: [{branches_str}]
  pull_request:
    branches: [{branches_str}]

jobs:
  lint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Set up Node.js
        uses: actions/setup-node@v4
        with:
          node-version: "{node_version}"
          cache: "npm"

      - name: Install dependencies
        run: npm ci

      - name: Run ESLint
        run: npm run lint
"""


def _gh_deploy_workflow(language: str, options: dict[str, Any]) -> str:
    """Generate deploy workflow YAML."""
    branches = options.get("branches", ["main"])
    branches_str = ", ".join(f'"{b}"' for b in branches)
    docker_registry = options.get("docker_registry", "ghcr.io")

    return f"""name: Deploy

on:
  push:
    branches: [{branches_str}]

jobs:
  deploy:
    runs-on: ubuntu-latest
    permissions:
      contents: read
      packages: write
    steps:
      - uses: actions/checkout@v4

      - name: Log in to registry
        uses: docker/login-action@v3
        with:
          registry: {docker_registry}
          username: ${{{{ github.actor }}}}
          password: ${{{{ secrets.GITHUB_TOKEN }}}}

      - name: Build and push
        uses: docker/build-push-action@v5
        with:
          context: .
          push: true
          tags: |
            {docker_registry}/${{{{ github.repository }}}}:latest
            {docker_registry}/${{{{ github.repository }}}}:${{{{ github.sha }}}}

      - name: Deploy to server
        uses: appleboy/ssh-action@v1
        with:
          host: ${{{{ secrets.DEPLOY_HOST }}}}
          username: ${{{{ secrets.DEPLOY_USER }}}}
          key: ${{{{ secrets.DEPLOY_KEY }}}}
          script: |
            docker pull {docker_registry}/${{{{ github.repository }}}}:latest
            docker compose up -d
"""


_WORKFLOW_GENERATORS = {
    "ci": _gh_ci_workflow,
    "cd": _gh_cd_workflow,
    "test": _gh_test_workflow,
    "lint": _gh_lint_workflow,
    "deploy": _gh_deploy_workflow,
}

# ---------------------------------------------------------------------------
# Dockerfile Templates
# ---------------------------------------------------------------------------


def _generate_python_dockerfile(options: dict[str, Any]) -> str:
    """Generate Python Dockerfile."""
    base_image = options.get("base_image", "python:3.11-slim")
    multi_stage = options.get("multi_stage", True)
    port = options.get("port")
    cmd = options.get("cmd", 'uvicorn app.main:app --host 0.0.0.0 --port 8000')

    if multi_stage:
        lines = f"""# Stage 1: Builder
FROM {base_image} AS builder

WORKDIR /app

COPY pyproject.toml README.md ./
COPY app/ ./app/

RUN pip install --no-cache-dir --upgrade pip && \\
    pip install --no-cache-dir -e .

# Stage 2: Runner
FROM {base_image} AS runner

RUN useradd --create-home --shell /bin/bash appuser

WORKDIR /app

COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin
COPY --from=builder /app /app

USER appuser
"""
    else:
        lines = f"""FROM {base_image}

RUN useradd --create-home --shell /bin/bash appuser

WORKDIR /app

COPY pyproject.toml README.md ./
COPY app/ ./app/

RUN pip install --no-cache-dir --upgrade pip && \\
    pip install --no-cache-dir -e .

USER appuser
"""

    if port:
        lines += f"\nEXPOSE {port}\n"

    lines += f"""
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \\
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:{port or 8000}/health')"

CMD [{', '.join(f'"{c}"' for c in cmd.split())}]
"""
    return lines


def _generate_node_dockerfile(options: dict[str, Any]) -> str:
    """Generate Node.js Dockerfile."""
    base_image = options.get("base_image", "node:20-alpine")
    multi_stage = options.get("multi_stage", True)
    port = options.get("port")
    cmd = options.get("cmd", "node dist/index.js")

    if multi_stage:
        lines = f"""# Stage 1: Builder
FROM {base_image} AS builder

WORKDIR /app

COPY package*.json ./
RUN npm ci

COPY . .
RUN npm run build

# Stage 2: Runner
FROM {base_image} AS runner

RUN addgroup -S appgroup && adduser -S appuser -G appgroup

WORKDIR /app

COPY --from=builder /app/dist ./dist
COPY --from=builder /app/node_modules ./node_modules
COPY --from=builder /app/package*.json ./

USER appuser
"""
    else:
        lines = f"""FROM {base_image}

RUN addgroup -S appgroup && adduser -S appuser -G appgroup

WORKDIR /app

COPY package*.json ./
RUN npm ci --only=production

COPY . .

USER appuser
"""

    if port:
        lines += f"\nEXPOSE {port}\n"

    lines += f"""
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \\
    CMD wget --no-verbose --tries=1 --spider http://localhost:{port or 3000}/health || exit 1

CMD [{', '.join(f'"{c}"' for c in cmd.split())}]
"""
    return lines


# ---------------------------------------------------------------------------
# Tool 1: generate_github_actions
# ---------------------------------------------------------------------------


async def _handle_generate_github_actions(arguments: dict) -> str:
    """Generate GitHub Actions workflow YAML."""
    try:
        project_path = arguments.get("project_path", ".")
        workflow_type = arguments.get("workflow_type", "ci")
        language = arguments.get("language", "auto")
        output_path = arguments.get("output_path")
        options = arguments.get("options", {})

        if language == "auto" or not language:
            language = _detect_language(project_path)

        generator = _WORKFLOW_GENERATORS.get(workflow_type)
        if not generator:
            return f"Error: Unknown workflow_type '{workflow_type}'. Use: ci, cd, test, lint, deploy"

        content = generator(language, options)
        location = _write_or_return(content, output_path)

        return f"Generated {workflow_type} workflow ({language}) | {location}\n{_truncate(content)}"

    except Exception as e:
        logger.error("generate_github_actions failed", error=str(e))
        return f"Error generating GitHub Actions workflow: {e}"


registry.register(
    name="generate_github_actions",
    toolset="cicd",
    schema={
        "type": "object",
        "properties": {
            "project_path": {
                "type": "string",
                "description": "Project root path to analyze",
                "default": ".",
            },
            "workflow_type": {
                "type": "string",
                "enum": ["ci", "cd", "test", "lint", "deploy"],
                "description": "Type of workflow to generate",
            },
            "language": {
                "type": "string",
                "enum": ["python", "node", "auto"],
                "description": "Project language (auto-detect if not specified)",
            },
            "output_path": {
                "type": "string",
                "description": "File path to write (default: return content inline)",
            },
            "options": {
                "type": "object",
                "properties": {
                    "python_version": {"type": "string", "default": "3.11"},
                    "node_version": {"type": "string", "default": "20"},
                    "branches": {
                        "type": "array",
                        "items": {"type": "string"},
                        "default": ["main"],
                    },
                    "docker_registry": {"type": "string"},
                },
                "description": "Additional workflow options",
            },
        },
        "required": ["workflow_type"],
    },
    handler=_handle_generate_github_actions,
    is_async=True,
    emoji="⚙️",
    category="devops",
)

# ---------------------------------------------------------------------------
# Tool 2: generate_dockerfile
# ---------------------------------------------------------------------------


async def _handle_generate_dockerfile(arguments: dict) -> str:
    """Generate optimized multi-stage Dockerfile."""
    try:
        project_path = arguments.get("project_path", ".")
        language = arguments.get("language", "auto")
        output_path = arguments.get("output_path")
        options = arguments.get("options", {})

        if language == "auto" or not language:
            language = _detect_language(project_path)

        multi_stage = options.get("multi_stage", True)

        if language == "python":
            content = _generate_python_dockerfile(options)
        else:
            content = _generate_node_dockerfile(options)

        location = _write_or_return(content, output_path)
        stage_info = "multi-stage" if multi_stage else "single-stage"

        return f"Generated Dockerfile ({language}, {stage_info}) | {location}\n{_truncate(content)}"

    except Exception as e:
        logger.error("generate_dockerfile failed", error=str(e))
        return f"Error generating Dockerfile: {e}"


registry.register(
    name="generate_dockerfile",
    toolset="cicd",
    schema={
        "type": "object",
        "properties": {
            "project_path": {
                "type": "string",
                "description": "Project root path",
                "default": ".",
            },
            "language": {
                "type": "string",
                "enum": ["python", "node", "auto"],
                "description": "Project language (auto-detect if not specified)",
            },
            "output_path": {
                "type": "string",
                "description": "File path to write (default: return content inline)",
            },
            "options": {
                "type": "object",
                "properties": {
                    "base_image": {"type": "string", "description": "Override base image"},
                    "multi_stage": {"type": "boolean", "default": True},
                    "port": {"type": "number", "description": "EXPOSE port"},
                    "cmd": {"type": "string", "description": "Override CMD"},
                },
                "description": "Dockerfile generation options",
            },
        },
        "required": [],
    },
    handler=_handle_generate_dockerfile,
    is_async=True,
    emoji="🐳",
    category="devops",
)

# ---------------------------------------------------------------------------
# Tool 3: generate_docker_compose
# ---------------------------------------------------------------------------


async def _handle_generate_docker_compose(arguments: dict) -> str:
    """Generate docker-compose.yml from services specification."""
    try:
        services = arguments.get("services", [])
        output_path = arguments.get("output_path")
        version = arguments.get("version", "3.8")
        include_networks = arguments.get("include_networks", True)
        include_volumes = arguments.get("include_volumes", False)

        lines = [f'version: "{version}"', "", "services:"]

        if not services:
            # Generate template from common patterns
            services = [
                {
                    "name": "app",
                    "build": ".",
                    "ports": ["8000:8000"],
                    "environment": {"ENV": "production"},
                },
                {
                    "name": "db",
                    "image": "postgres:15-alpine",
                    "ports": ["5432:5432"],
                    "environment": {
                        "POSTGRES_DB": "app",
                        "POSTGRES_USER": "user",
                        "POSTGRES_PASSWORD": "password",
                    },
                    "volumes": ["db_data:/var/lib/postgresql/data"],
                },
            ]
            include_volumes = True

        named_volumes: list[str] = []

        for svc in services:
            name = svc.get("name", "service")
            lines.append(f"  {name}:")

            if svc.get("image"):
                lines.append(f"    image: {svc['image']}")
            if svc.get("build"):
                lines.append(f"    build: {svc['build']}")
            if svc.get("command"):
                lines.append(f"    command: {svc['command']}")

            ports = svc.get("ports", [])
            if ports:
                lines.append("    ports:")
                for port in ports:
                    lines.append(f'      - "{port}"')

            env = svc.get("environment", {})
            if env:
                lines.append("    environment:")
                for k, v in env.items():
                    lines.append(f"      {k}: {v}")

            volumes = svc.get("volumes", [])
            if volumes:
                lines.append("    volumes:")
                for vol in volumes:
                    lines.append(f"      - {vol}")
                    # Track named volumes (format: name:/path)
                    if ":" in vol and not vol.startswith(".") and not vol.startswith("/"):
                        vol_name = vol.split(":")[0]
                        if vol_name not in named_volumes:
                            named_volumes.append(vol_name)

            depends = svc.get("depends_on", [])
            if depends:
                lines.append("    depends_on:")
                for dep in depends:
                    lines.append(f"      - {dep}")

            lines.append("")

        if include_networks:
            lines.append("networks:")
            lines.append("  default:")
            lines.append("    driver: bridge")
            lines.append("")

        if include_volumes and named_volumes:
            lines.append("volumes:")
            for vol_name in named_volumes:
                lines.append(f"  {vol_name}:")
            lines.append("")

        content = "\n".join(lines)
        location = _write_or_return(content, output_path)
        svc_count = len(services)

        return f"Generated docker-compose ({svc_count} services) | {location}\n{_truncate(content)}"

    except Exception as e:
        logger.error("generate_docker_compose failed", error=str(e))
        return f"Error generating docker-compose: {e}"


registry.register(
    name="generate_docker_compose",
    toolset="cicd",
    schema={
        "type": "object",
        "properties": {
            "services": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "Service name"},
                        "image": {"type": "string", "description": "Docker image"},
                        "build": {"type": "string", "description": "Build context path"},
                        "ports": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Port mappings",
                        },
                        "environment": {
                            "type": "object",
                            "description": "Environment variables",
                        },
                        "volumes": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Volume mappings",
                        },
                        "depends_on": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Service dependencies",
                        },
                        "command": {"type": "string", "description": "Override command"},
                    },
                    "required": ["name"],
                },
                "description": "Array of service definitions",
            },
            "output_path": {
                "type": "string",
                "description": "File path to write (default: return content inline)",
            },
            "version": {
                "type": "string",
                "default": "3.8",
                "description": "Compose file version",
            },
            "include_networks": {
                "type": "boolean",
                "default": True,
                "description": "Add default network section",
            },
            "include_volumes": {
                "type": "boolean",
                "default": False,
                "description": "Add named volumes section",
            },
        },
        "required": ["services"],
    },
    handler=_handle_generate_docker_compose,
    is_async=True,
    emoji="🐙",
    category="devops",
)
