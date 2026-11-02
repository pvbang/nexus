"""
Nexus - Docker Management Tools.

Tools for creating, managing, and interacting with Docker containers and
Docker Compose environments. Essential for:
- Lab environment creation (security testing)
- Service deployment
- Isolated execution environments
- Multi-service architecture testing
"""

import asyncio
import json
import os
from typing import Any

import structlog

from app.mcp.tool_registry import registry, tool_error, tool_result

logger = structlog.get_logger(__name__)

TOOLSET = "docker"
_CATEGORY = "infrastructure"


# ============================================================
# Helper: Check Docker availability
# ============================================================

def _docker_available() -> bool:
    """Check if Docker is available on the system."""
    import shutil
    return shutil.which("docker") is not None


async def _run_docker_cmd(cmd: str, timeout: int = 600) -> dict[str, Any]:
    """Run a docker command and return structured result."""
    try:
        proc = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        output = stdout.decode("utf-8", errors="replace")
        errors = stderr.decode("utf-8", errors="replace")

        return {
            "success": proc.returncode == 0,
            "output": output if output else errors,
            "exit_code": proc.returncode,
            "stderr": errors if proc.returncode != 0 else "",
        }
    except asyncio.TimeoutError:
        return {
            "success": False,
            "output": f"Command timed out after {timeout}s: {cmd[:100]}",
            "exit_code": -1,
            "stderr": "timeout",
        }
    except Exception as e:
        return {
            "success": False,
            "output": f"Error running command: {str(e)}",
            "exit_code": -1,
            "stderr": str(e),
        }


# ============================================================
# Tool 1: docker_compose_up — Start a Docker Compose stack
# ============================================================

async def handle_docker_compose_up(params: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    """Start Docker Compose services from a compose file.

    Creates and starts containers defined in a docker-compose.yml file.
    Supports building images, detached mode, and custom project names.
    """
    compose_file = params.get("compose_file", "docker-compose.yml")
    project_name = params.get("project_name", "")
    build = params.get("build", False)
    detach = params.get("detach", True)
    services = params.get("services", [])
    timeout = params.get("timeout", 120)

    if not os.path.exists(compose_file):
        return tool_error(f"Compose file not found: {compose_file}")

    cmd_parts = ["docker", "compose"]
    if compose_file != "docker-compose.yml":
        cmd_parts.extend(["-f", compose_file])
    if project_name:
        cmd_parts.extend(["-p", project_name])

    cmd_parts.append("up")
    if detach:
        cmd_parts.append("-d")
    if build:
        cmd_parts.append("--build")
    if services:
        cmd_parts.extend(services[:5])  # Max 5 services

    cmd = " ".join(cmd_parts)
    logger.info("docker_compose_up", cmd=cmd[:200])

    result = await _run_docker_cmd(cmd, timeout=timeout)
    return {
        "success": result["success"],
        "output": result["output"][:5000],
        "data": {
            "compose_file": compose_file,
            "project_name": project_name,
            "services": services,
            "detached": detach,
        },
        "artifacts": [],
    }


# ============================================================
# Tool 2: docker_compose_down — Stop and remove containers
# ============================================================

async def handle_docker_compose_down(params: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    """Stop and remove Docker Compose services.

    Stops containers and removes containers, networks, volumes, and images
    created by `docker compose up`.
    """
    compose_file = params.get("compose_file", "docker-compose.yml")
    project_name = params.get("project_name", "")
    volumes = params.get("remove_volumes", False)

    cmd_parts = ["docker", "compose"]
    if compose_file != "docker-compose.yml":
        cmd_parts.extend(["-f", compose_file])
    if project_name:
        cmd_parts.extend(["-p", project_name])

    cmd_parts.append("down")
    if volumes:
        cmd_parts.append("-v")

    cmd = " ".join(cmd_parts)
    result = await _run_docker_cmd(cmd, timeout=60)
    return {
        "success": result["success"],
        "output": result["output"][:3000],
        "data": {"compose_file": compose_file, "volumes_removed": volumes},
        "artifacts": [],
    }


# ============================================================
# Tool 3: docker_build — Build a Docker image
# ============================================================

async def handle_docker_build(params: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    """Build a Docker image from a Dockerfile.

    Supports custom tags, build args, and context paths.
    """
    dockerfile = params.get("dockerfile", "Dockerfile")
    tag = params.get("tag", "nexus-lab:latest")
    context = params.get("context", ".")
    build_args = params.get("build_args", {})
    no_cache = params.get("no_cache", False)
    timeout = params.get("timeout", 300)

    if not os.path.exists(dockerfile) and not os.path.exists(os.path.join(context, dockerfile)):
        return tool_error(f"Dockerfile not found: {dockerfile}")

    cmd_parts = ["docker", "build"]
    cmd_parts.extend(["-t", tag])
    cmd_parts.extend(["-f", dockerfile])
    if no_cache:
        cmd_parts.append("--no-cache")
    for key, value in list(build_args.items())[:10]:
        cmd_parts.extend(["--build-arg", f"{key}={value}"])
    cmd_parts.append(context)

    cmd = " ".join(cmd_parts)
    logger.info("docker_build", tag=tag, dockerfile=dockerfile)

    result = await _run_docker_cmd(cmd, timeout=timeout)
    return {
        "success": result["success"],
        "output": result["output"][:5000],
        "data": {"tag": tag, "dockerfile": dockerfile, "context": context},
        "artifacts": [],
    }


# ============================================================
# Tool 4: docker_run — Run a container
# ============================================================

async def handle_docker_run(params: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    """Run a Docker container with specified configuration.

    Supports port mapping, volume mounts, env vars, and command override.
    """
    image = params.get("image", "")
    command = params.get("command", "")
    name = params.get("name", "")
    ports = params.get("ports", [])  # ["8080:80", "443:443"]
    volumes = params.get("volumes", [])  # ["/host/path:/container/path"]
    env_vars = params.get("env_vars", {})
    detach = params.get("detach", True)
    remove = params.get("remove", True)
    network = params.get("network", "")
    timeout = params.get("timeout", 60)

    if not image:
        return tool_error("image is required")

    cmd_parts = ["docker", "run"]
    if detach:
        cmd_parts.append("-d")
    if remove:
        cmd_parts.append("--rm")
    if name:
        cmd_parts.extend(["--name", name])
    if network:
        cmd_parts.extend(["--network", network])
    for port in ports[:10]:
        cmd_parts.extend(["-p", str(port)])
    for vol in volumes[:5]:
        cmd_parts.extend(["-v", str(vol)])
    for key, value in list(env_vars.items())[:20]:
        cmd_parts.extend(["-e", f"{key}={value}"])

    cmd_parts.append(image)
    if command:
        cmd_parts.append(command)

    cmd = " ".join(cmd_parts)
    logger.info("docker_run", image=image, name=name)

    result = await _run_docker_cmd(cmd, timeout=timeout)
    return {
        "success": result["success"],
        "output": result["output"][:3000],
        "data": {"image": image, "name": name, "detached": detach},
        "artifacts": [],
    }


# ============================================================
# Tool 5: docker_exec — Execute command in running container
# ============================================================

async def handle_docker_exec(params: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    """Execute a command inside a running Docker container.

    Useful for inspecting container state, running tests, or debugging.
    """
    container = params.get("container", "")
    command = params.get("command", "")
    user = params.get("user", "")
    workdir = params.get("workdir", "")
    timeout = params.get("timeout", 30)

    if not container or not command:
        return tool_error("container and command are required")

    cmd_parts = ["docker", "exec"]
    if user:
        cmd_parts.extend(["-u", user])
    if workdir:
        cmd_parts.extend(["-w", workdir])
    cmd_parts.append(container)
    cmd_parts.append(command)

    cmd = " ".join(cmd_parts)
    result = await _run_docker_cmd(cmd, timeout=timeout)
    return {
        "success": result["success"],
        "output": result["output"][:5000],
        "data": {"container": container, "command": command},
        "artifacts": [],
    }


# ============================================================
# Tool 6: docker_logs — Get container logs
# ============================================================

async def handle_docker_logs(params: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    """Get logs from a Docker container.

    Useful for debugging, checking if services started correctly,
    and capturing crash output.
    """
    container = params.get("container", "")
    tail = params.get("tail", 100)
    since = params.get("since", "")  # e.g. "5m", "2h", "2024-01-01"

    if not container:
        return tool_error("container is required")

    cmd_parts = ["docker", "logs"]
    cmd_parts.extend(["--tail", str(min(tail, 500))])
    if since:
        cmd_parts.extend(["--since", since])
    cmd_parts.append(container)

    cmd = " ".join(cmd_parts)
    result = await _run_docker_cmd(cmd, timeout=15)
    return {
        "success": result["success"],
        "output": result["output"][:5000],
        "data": {"container": container, "tail": tail},
        "artifacts": [],
    }


# ============================================================
# Tool 7: docker_ps — List containers
# ============================================================

async def handle_docker_ps(params: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    """List running Docker containers.

    Optionally show all containers (including stopped).
    """
    all_containers = params.get("all", False)
    filter_name = params.get("filter_name", "")

    cmd_parts = ["docker", "ps", "--format", "json"]
    if all_containers:
        cmd_parts.append("-a")
    if filter_name:
        cmd_parts.extend(["--filter", f"name={filter_name}"])

    cmd = " ".join(cmd_parts)
    result = await _run_docker_cmd(cmd, timeout=10)

    # Parse JSON output
    containers = []
    if result["success"] and result["output"]:
        for line in result["output"].strip().split("\n"):
            try:
                containers.append(json.loads(line))
            except json.JSONDecodeError:
                pass

    return {
        "success": result["success"],
        "output": result["output"][:3000] if not containers else json.dumps(containers, indent=2)[:3000],
        "data": {"containers": containers[:20], "total": len(containers)},
        "artifacts": [],
    }


# ============================================================
# Tool 8: docker_network — Manage Docker networks
# ============================================================

async def handle_docker_network(params: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    """Create or inspect Docker networks.

    Useful for creating isolated lab networks for security testing.
    """
    action = params.get("action", "ls")  # "create", "ls", "inspect", "rm"
    name = params.get("name", "")
    driver = params.get("driver", "bridge")
    subnet = params.get("subnet", "")

    if action == "create":
        if not name:
            return tool_error("name is required for network creation")
        cmd_parts = ["docker", "network", "create"]
        if driver != "bridge":
            cmd_parts.extend(["--driver", driver])
        if subnet:
            cmd_parts.extend(["--subnet", subnet])
        cmd_parts.append(name)
    elif action == "ls":
        cmd_parts = ["docker", "network", "ls", "--format", "json"]
    elif action == "inspect" and name:
        cmd_parts = ["docker", "network", "inspect", name]
    elif action == "rm" and name:
        cmd_parts = ["docker", "network", "rm", name]
    else:
        return tool_error(f"Invalid action '{action}' or missing name")

    cmd = " ".join(cmd_parts)
    result = await _run_docker_cmd(cmd, timeout=15)
    return {
        "success": result["success"],
        "output": result["output"][:3000],
        "data": {"action": action, "name": name},
        "artifacts": [],
    }


# ============================================================
# Registration
# ============================================================

registry.register(
    name="docker_compose_up",
    toolset=TOOLSET,
    category=_CATEGORY,
    schema={
        "description": (
            "Start Docker Compose services. Creates and starts containers defined "
            "in a docker-compose.yml file. Use for creating lab environments, "
            "multi-service architectures, and isolated test setups."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "compose_file": {
                    "type": "string",
                    "description": "Path to docker-compose.yml file",
                    "default": "docker-compose.yml",
                },
                "project_name": {
                    "type": "string",
                    "description": "Custom project name (optional)",
                },
                "build": {
                    "type": "boolean",
                    "description": "Build images before starting",
                    "default": False,
                },
                "detach": {
                    "type": "boolean",
                    "description": "Run in background (detached mode)",
                    "default": True,
                },
                "services": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Specific services to start (empty = all)",
                },
                "timeout": {
                    "type": "integer",
                    "description": "Timeout in seconds",
                    "default": 120,
                },
            },
            "required": [],
        },
    },
    handler=handle_docker_compose_up,
    is_async=True,
    emoji="🐳",
    check_fn=_docker_available,
)

registry.register(
    name="docker_compose_down",
    toolset=TOOLSET,
    category=_CATEGORY,
    schema={
        "description": "Stop and remove Docker Compose services. Cleans up containers, networks, and optionally volumes.",
        "parameters": {
            "type": "object",
            "properties": {
                "compose_file": {
                    "type": "string",
                    "description": "Path to docker-compose.yml file",
                    "default": "docker-compose.yml",
                },
                "project_name": {"type": "string", "description": "Project name"},
                "remove_volumes": {
                    "type": "boolean",
                    "description": "Also remove data volumes",
                    "default": False,
                },
            },
            "required": [],
        },
    },
    handler=handle_docker_compose_down,
    is_async=True,
    emoji="🛑",
    check_fn=_docker_available,
)

registry.register(
    name="docker_build",
    toolset=TOOLSET,
    category=_CATEGORY,
    schema={
        "description": "Build a Docker image from a Dockerfile. Use for creating custom lab images, vulnerable service containers, or test environments.",
        "parameters": {
            "type": "object",
            "properties": {
                "dockerfile": {"type": "string", "description": "Path to Dockerfile", "default": "Dockerfile"},
                "tag": {"type": "string", "description": "Image tag (name:version)", "default": "nexus-lab:latest"},
                "context": {"type": "string", "description": "Build context directory", "default": "."},
                "build_args": {"type": "object", "description": "Build arguments (key=value pairs)"},
                "no_cache": {"type": "boolean", "description": "Build without cache", "default": False},
                "timeout": {"type": "integer", "description": "Build timeout in seconds", "default": 300},
            },
            "required": [],
        },
    },
    handler=handle_docker_build,
    is_async=True,
    emoji="🔨",
    check_fn=_docker_available,
)

registry.register(
    name="docker_run",
    toolset=TOOLSET,
    category=_CATEGORY,
    schema={
        "description": (
            "Run a Docker container. Supports port mapping, volume mounts, environment variables. "
            "Use for starting vulnerable services, databases, or test targets."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "image": {"type": "string", "description": "Docker image to run (e.g., 'httpd:2.4.66')"},
                "command": {"type": "string", "description": "Override default command"},
                "name": {"type": "string", "description": "Container name"},
                "ports": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Port mappings (e.g., ['8080:80', '443:443'])",
                },
                "volumes": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Volume mounts (e.g., ['/host/path:/container/path'])",
                },
                "env_vars": {"type": "object", "description": "Environment variables"},
                "detach": {"type": "boolean", "description": "Run in background", "default": True},
                "remove": {"type": "boolean", "description": "Auto-remove when stopped", "default": True},
                "network": {"type": "string", "description": "Docker network to connect to"},
                "timeout": {"type": "integer", "description": "Timeout in seconds", "default": 60},
            },
            "required": ["image"],
        },
    },
    handler=handle_docker_run,
    is_async=True,
    emoji="▶️",
    check_fn=_docker_available,
)

registry.register(
    name="docker_exec",
    toolset=TOOLSET,
    category=_CATEGORY,
    schema={
        "description": "Execute a command inside a running Docker container. Use for inspecting state, running tests, or debugging services.",
        "parameters": {
            "type": "object",
            "properties": {
                "container": {"type": "string", "description": "Container name or ID"},
                "command": {"type": "string", "description": "Command to execute inside container"},
                "user": {"type": "string", "description": "User to run as (e.g., 'root')"},
                "workdir": {"type": "string", "description": "Working directory inside container"},
                "timeout": {"type": "integer", "description": "Timeout in seconds", "default": 30},
            },
            "required": ["container", "command"],
        },
    },
    handler=handle_docker_exec,
    is_async=True,
    emoji="📦",
    check_fn=_docker_available,
)

registry.register(
    name="docker_logs",
    toolset=TOOLSET,
    category=_CATEGORY,
    schema={
        "description": "Get logs from a Docker container. Useful for checking if services started correctly, capturing crash output, and debugging.",
        "parameters": {
            "type": "object",
            "properties": {
                "container": {"type": "string", "description": "Container name or ID"},
                "tail": {"type": "integer", "description": "Number of lines from end", "default": 100},
                "since": {"type": "string", "description": "Show logs since timestamp (e.g., '5m', '2h')"},
            },
            "required": ["container"],
        },
    },
    handler=handle_docker_logs,
    is_async=True,
    emoji="📋",
    check_fn=_docker_available,
)

registry.register(
    name="docker_ps",
    toolset=TOOLSET,
    category=_CATEGORY,
    schema={
        "description": "List Docker containers. Shows running containers, their status, ports, and names.",
        "parameters": {
            "type": "object",
            "properties": {
                "all": {"type": "boolean", "description": "Show all containers (including stopped)", "default": False},
                "filter_name": {"type": "string", "description": "Filter by container name"},
            },
            "required": [],
        },
    },
    handler=handle_docker_ps,
    is_async=True,
    emoji="📊",
    check_fn=_docker_available,
)

registry.register(
    name="docker_network",
    toolset=TOOLSET,
    category=_CATEGORY,
    schema={
        "description": "Manage Docker networks. Create isolated networks for lab environments, list existing networks, or inspect network details.",
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["create", "ls", "inspect", "rm"],
                    "description": "Network action to perform",
                    "default": "ls",
                },
                "name": {"type": "string", "description": "Network name (required for create/inspect/rm)"},
                "driver": {"type": "string", "description": "Network driver", "default": "bridge"},
                "subnet": {"type": "string", "description": "Subnet CIDR (e.g., '172.20.0.0/16')"},
            },
            "required": [],
        },
    },
    handler=handle_docker_network,
    is_async=True,
    emoji="🌐",
    check_fn=_docker_available,
)
