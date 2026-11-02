"""
Nexus - Docker Execution Tool (Tier 1).

Allows agents to execute commands in pre-built Docker containers
with security tools (jadx, apktool, nuclei, frida-tools, nmap, etc).

This gives agents "hands" — ability to run binaries, download files,
decompile APKs, scan networks, all in an isolated container.
"""

import asyncio
import os
import shutil
from typing import Any

import structlog

from app.mcp.tool_registry import registry

logger = structlog.get_logger(__name__)

_DEFAULT_IMAGE = os.environ.get("DOCKER_SECURITY_IMAGE", "nexus-security-toolkit")
_DOCKER_TIMEOUT = int(os.environ.get("DOCKER_EXEC_TIMEOUT", "3000"))
_WORKSPACE_DIR = os.environ.get("WORKSPACE_DIR", "workspace_data")


def _docker_available() -> bool:
    return shutil.which("docker") is not None


registry.register(
    name="docker_exec",
    toolset="docker",
    category="execution",
    schema={
        "type": "function",
        "function": {
            "name": "docker_exec",
            "description": (
                "Execute command in Docker container with security tools. "
                "Available tools: jadx, apktool, nuclei, ffuf, nmap, sqlmap, "
                "frida-tools, mitmproxy, pwntools, impacket, wget, curl, git, "
                "python3, go tools. Workspace is mounted at /workspace. "
                "Use for: APK decompilation, vulnerability scanning, network recon, "
                "exploit development, file downloads, binary analysis."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "Shell command to execute in container",
                    },
                    "image": {
                        "type": "string",
                        "description": (
                            "Docker image. Options: nexus-security-toolkit (default, all tools), "
                            "nexus-android-re (Android-focused), nexus-exploit-dev (pwn/exploit), "
                            "nexus-network (traffic analysis). Or any public image."
                        ),
                        "default": "nexus-security-toolkit",
                    },
                    "workdir": {
                        "type": "string",
                        "description": "Working directory inside container",
                        "default": "/workspace",
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "Timeout in seconds (default 300)",
                        "default": 300,
                    },
                    "network": {
                        "type": "string",
                        "description": "Network mode: 'host' (full access) or 'bridge' (isolated)",
                        "default": "host",
                    },
                },
                "required": ["command"],
            },
        },
    },
    handler=None,  # Set below
    check_fn=_docker_available,
    requires_env=[],
    emoji="🐳",
)


async def _docker_exec_handler(
    command: str,
    image: str = "",
    workdir: str = "/workspace",
    timeout: int = 0,
    network: str = "host",
) -> dict[str, Any]:
    """Execute command in Docker container."""
    if not _docker_available():
        return {
            "success": False,
            "output": (
                "Docker is not available. Install Docker or use execute_code for Python-only tasks. "
                "Alternatively, use scaffold_custom_tool_module() to create a tool that works without Docker."
            ),
        }

    image = image or _DEFAULT_IMAGE
    timeout = timeout or _DOCKER_TIMEOUT

    # Resolve workspace path for mounting
    from app.core.execution_context import current_task_id
    task_id = current_task_id.get("")
    if task_id:
        from app.core import task_paths
        workspace_path = os.path.abspath(str(task_paths.work_dir(task_id)))
    else:
        workspace_path = os.path.abspath(_WORKSPACE_DIR)

    os.makedirs(workspace_path, exist_ok=True)

    # Build docker command
    docker_cmd = [
        "docker", "run", "--rm",
        f"--network={network}",
        f"-w", workdir,
        "-v", f"{workspace_path}:/workspace",
        "--memory=2g",
        "--cpus=2",
        image,
        "bash", "-c", command,
    ]

    logger.info(
        "docker_exec_start",
        image=image,
        command=command[:200],
        workspace=workspace_path,
    )

    try:
        proc = await asyncio.create_subprocess_exec(
            *docker_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=timeout
        )

        output = stdout.decode("utf-8", errors="replace")
        errors = stderr.decode("utf-8", errors="replace")

        success = proc.returncode == 0

        result_text = output
        if errors and not success:
            result_text += f"\n[STDERR]: {errors}"
        elif errors:
            result_text += f"\n[STDERR (warnings)]: {errors[:500]}"

        logger.info(
            "docker_exec_done",
            success=success,
            return_code=proc.returncode,
            output_len=len(output),
        )

        return {
            "success": success,
            "output": result_text[:50000],
            "return_code": proc.returncode,
        }

    except asyncio.TimeoutError:
        return {
            "success": False,
            "output": f"Command timed out after {timeout}s. Try increasing timeout or breaking into smaller commands.",
        }
    except FileNotFoundError:
        return {
            "success": False,
            "output": "Docker binary not found. Ensure Docker is installed and in PATH.",
        }
    except Exception as e:
        return {
            "success": False,
            "output": f"Docker execution error: {e}",
        }


# Bind handler
registry._tools["docker_exec"].handler = _docker_exec_handler
registry._tools["docker_exec"].is_async = True
