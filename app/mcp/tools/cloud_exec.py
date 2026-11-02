"""
Nexus - Cloud Execution Tools (Tier 3).

Execute cloud CLI commands for offensive security testing:
- Alibaba Cloud (aliyun CLI)
- AWS (aws CLI)
- GCP (gcloud CLI)

Agents can: enumerate resources, get metadata, provision sandboxes,
test for privilege escalation, exploit misconfigurations.
"""

import asyncio
import os
import shutil
from typing import Any

import structlog

from app.mcp.tool_registry import registry

logger = structlog.get_logger(__name__)

_CLOUD_TIMEOUT = int(os.environ.get("CLOUD_EXEC_TIMEOUT", "60"))


def _cloud_available() -> bool:
    """Check if any cloud CLI is available."""
    return any([
        shutil.which("aliyun"),
        shutil.which("aws"),
        shutil.which("gcloud"),
        os.environ.get("ALIBABA_ACCESS_KEY_ID"),
        os.environ.get("AWS_ACCESS_KEY_ID"),
    ])


registry.register(
    name="cloud_exec",
    toolset="cloud",
    category="infrastructure",
    schema={
        "type": "function",
        "function": {
            "name": "cloud_exec",
            "description": (
                "Execute cloud CLI command for security testing. "
                "Supports: alibaba (aliyun), aws, gcp (gcloud). "
                "Use for: enumerate resources, get STS tokens, check IAM policies, "
                "test privilege escalation, discover misconfigurations, "
                "provision temporary attack infrastructure."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "provider": {
                        "type": "string",
                        "enum": ["alibaba", "aws", "gcp"],
                        "description": "Cloud provider",
                    },
                    "command": {
                        "type": "string",
                        "description": (
                            "CLI command (without the CLI prefix). "
                            "Examples: 'sts GetCallerIdentity', 's3 ls', 'ecs DescribeInstances'"
                        ),
                    },
                    "timeout": {
                        "type": "integer",
                        "default": 60,
                    },
                },
                "required": ["provider", "command"],
            },
        },
    },
    handler=None,
    check_fn=_cloud_available,
    requires_env=[],
    emoji="☁️",
)


_CLI_MAP = {
    "alibaba": "aliyun",
    "aws": "aws",
    "gcp": "gcloud",
}


async def _cloud_exec_handler(
    provider: str,
    command: str,
    timeout: int = 0,
) -> dict[str, Any]:
    """Execute cloud CLI command."""
    cli = _CLI_MAP.get(provider)
    if not cli:
        return {"success": False, "output": f"Unknown provider: {provider}. Use: alibaba, aws, gcp"}

    if not shutil.which(cli):
        return {
            "success": False,
            "output": (
                f"{cli} CLI not installed. "
                f"Use docker_exec to run in a container with cloud CLIs, "
                f"or install {cli} locally."
            ),
        }

    timeout = timeout or _CLOUD_TIMEOUT
    full_cmd = f"{cli} {command}"

    try:
        proc = await asyncio.create_subprocess_shell(
            full_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        output = stdout.decode("utf-8", errors="replace")
        errors = stderr.decode("utf-8", errors="replace")

        return {
            "success": proc.returncode == 0,
            "output": (output + (f"\n[STDERR]: {errors}" if errors else ""))[:30000],
        }
    except asyncio.TimeoutError:
        return {"success": False, "output": f"Cloud command timed out after {timeout}s"}
    except Exception as e:
        return {"success": False, "output": f"Cloud exec error: {e}"}


registry._tools["cloud_exec"].handler = _cloud_exec_handler
registry._tools["cloud_exec"].is_async = True


# === cloud_provision ===
registry.register(
    name="cloud_provision",
    toolset="cloud",
    category="infrastructure",
    schema={
        "type": "function",
        "function": {
            "name": "cloud_provision",
            "description": (
                "Provision temporary cloud resource for testing (auto-destroyed after TTL). "
                "Use for: attack box VM, callback receiver, temporary storage, "
                "C2 simulation endpoint."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "provider": {
                        "type": "string",
                        "enum": ["alibaba", "aws"],
                    },
                    "resource_type": {
                        "type": "string",
                        "enum": ["sandbox_vm", "callback_endpoint", "storage_bucket"],
                        "description": "Type of resource to provision",
                    },
                    "ttl_minutes": {
                        "type": "integer",
                        "description": "Time to live in minutes (auto-destroy after)",
                        "default": 60,
                    },
                    "config": {
                        "type": "object",
                        "description": "Additional configuration (region, size, etc.)",
                    },
                },
                "required": ["provider", "resource_type"],
            },
        },
    },
    handler=None,
    check_fn=_cloud_available,
    requires_env=[],
    emoji="🏗️",
)


async def _cloud_provision_handler(
    provider: str,
    resource_type: str,
    ttl_minutes: int = 60,
    config: dict = None,
) -> dict[str, Any]:
    """Provision temporary cloud resource."""
    config = config or {}

    if provider == "alibaba":
        return await _provision_alibaba(resource_type, ttl_minutes, config)
    elif provider == "aws":
        return await _provision_aws(resource_type, ttl_minutes, config)
    else:
        return {"success": False, "output": f"Provisioning not supported for: {provider}"}


async def _provision_alibaba(resource_type: str, ttl: int, config: dict) -> dict:
    """Provision Alibaba Cloud resource."""
    if not os.environ.get("ALIBABA_ACCESS_KEY_ID"):
        return {
            "success": False,
            "output": "Alibaba Cloud credentials not configured. Set ALIBABA_ACCESS_KEY_ID + ALIBABA_ACCESS_KEY_SECRET",
        }

    if resource_type == "sandbox_vm":
        region = config.get("region", os.environ.get("ALIBABA_REGION", "cn-hangzhou"))
        cmd = (
            f"aliyun ecs RunInstances "
            f"--RegionId {region} "
            f"--ImageId ubuntu_22_04_x64_20G "
            f"--InstanceType ecs.t6-c1m1.large "
            f"--SecurityGroupId default "
            f"--InstanceName nexus-sandbox-{ttl}min "
            f"--Tags.1.Key auto-destroy --Tags.1.Value {ttl} "
            f"--Amount 1"
        )
        result = await _cloud_exec_handler("alibaba", cmd.replace("aliyun ", ""))
        return result

    return {"success": False, "output": f"Resource type '{resource_type}' not yet implemented for Alibaba"}


async def _provision_aws(resource_type: str, ttl: int, config: dict) -> dict:
    """Provision AWS resource."""
    if not os.environ.get("AWS_ACCESS_KEY_ID"):
        return {"success": False, "output": "AWS credentials not configured."}

    if resource_type == "callback_endpoint":
        # Use Lambda function URL as callback
        return {"success": False, "output": "AWS callback endpoint provisioning: use expose_port() for simpler alternative"}

    return {"success": False, "output": f"Resource type '{resource_type}' not yet implemented for AWS"}


registry._tools["cloud_provision"].handler = _cloud_provision_handler
registry._tools["cloud_provision"].is_async = True
