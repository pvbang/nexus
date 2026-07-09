"""
MekongAI Nexus - Infrastructure Map (Self-Awareness Layer).

Builds a dynamic, live-status map of what the agent CAN and CANNOT do
in its current environment. Injected into agent prompts so they:
- Know which services/credentials are available
- Know how to self-bootstrap missing capabilities
- Never silently fail due to missing infrastructure
"""

from __future__ import annotations

import os
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


class InfrastructureMap:
    """Dynamic infrastructure awareness for agents.

    Checks real availability of tools, services, and credentials,
    then produces a prompt-injectable summary that tells agents:
    - What's available and working
    - What's missing and HOW TO FIX IT (bootstrap instructions)
    """

    def __init__(self) -> None:
        self._cache: str = ""
        self._cache_valid: bool = False

    def invalidate(self) -> None:
        self._cache_valid = False

    async def build_map(self) -> str:
        """Build current infrastructure state for prompt injection.

        Returns concise markdown (~1-2KB) describing live capabilities.
        """
        if self._cache_valid and self._cache:
            return self._cache

        sections: list[str] = []
        sections.append("## INFRASTRUCTURE STATUS (Live)")
        sections.append(self._check_llm_providers())
        sections.append(self._check_cloud_providers())
        sections.append(self._check_toolset_health())
        sections.append(self._check_execution_env())
        sections.append(self._bootstrap_options())

        self._cache = "\n".join(s for s in sections if s)
        self._cache_valid = True
        return self._cache

    def _check_llm_providers(self) -> str:
        """Check which LLM providers are configured."""
        lines = ["### LLM Providers"]
        providers = {
            "OpenAI": "OPENAI_API_KEY",
            "Anthropic": "ANTHROPIC_API_KEY",
            "AWS Bedrock": "AWS_ACCESS_KEY_ID",
            "OpenRouter": "OPENROUTER_API_KEY",
            "Pollinations": "POLLINATIONS_API_KEY",
        }
        for name, env_var in providers.items():
            if os.environ.get(env_var):
                lines.append(f"- {name}: ACTIVE")
            else:
                lines.append(f"- {name}: NOT CONFIGURED (set {env_var})")
        return "\n".join(lines)

    def _check_cloud_providers(self) -> str:
        """Check cloud provider credentials and connectivity."""
        lines = ["### Cloud Providers"]

        # Alibaba Cloud
        ali_key = os.environ.get("ALIBABA_ACCESS_KEY_ID") or os.environ.get("ALICLOUD_ACCESS_KEY")
        if ali_key:
            lines.append("- Alibaba Cloud: CONFIGURED (credentials present)")
        else:
            lines.append(
                "- Alibaba Cloud: NOT CONFIGURED\n"
                "  → Bootstrap: use `create_cloud_sandbox(provider='alibaba')` "
                "or set ALIBABA_ACCESS_KEY_ID + ALIBABA_ACCESS_KEY_SECRET"
            )

        # AWS
        aws_key = os.environ.get("AWS_ACCESS_KEY_ID")
        if aws_key:
            region = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
            lines.append(f"- AWS: CONFIGURED (region: {region})")
        else:
            lines.append("- AWS: NOT CONFIGURED (set AWS_ACCESS_KEY_ID + AWS_SECRET_ACCESS_KEY)")

        # GCP
        gcp = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS") or os.environ.get("GCP_PROJECT_ID")
        if gcp:
            lines.append("- GCP: CONFIGURED")
        else:
            lines.append("- GCP: NOT CONFIGURED")

        return "\n".join(lines)

    def _check_toolset_health(self) -> str:
        """Check which toolsets are actually available."""
        lines = ["### Toolset Availability"]
        try:
            from app.mcp.tool_registry import registry
            status = registry.check_toolset_requirements()
            for toolset, available in sorted(status.items()):
                icon = "READY" if available else "UNAVAILABLE"
                lines.append(f"- {toolset}: {icon}")
        except Exception:
            lines.append("- (Could not query tool registry)")
        return "\n".join(lines)

    def _check_execution_env(self) -> str:
        """Check execution environment capabilities."""
        lines = ["### Execution Environment"]

        has_network = _check_network()

        # Docker
        docker_on_path = os.environ.get("DOCKER_HOST") or _cmd_exists("docker")
        if docker_on_path:
            lines.append("- Docker: ON PATH (ready to use)")
        elif has_network:
            lines.append("- Docker: not on PATH (CAN BE INSTALLED — network available)")
        else:
            lines.append("- Docker: not on PATH (install manually if needed)")

        # Network
        lines.append(f"- Network: {'AVAILABLE (can install any tool on demand)' if has_network else 'RESTRICTED'}")

        # Workspace
        workspace = os.environ.get("WORKSPACE_DIR", "workspace_data")
        lines.append(f"- Workspace: {workspace}")

        # Sandbox
        sandbox = os.environ.get("DOCKER_SANDBOX_ENABLED", "false")
        lines.append(f"- Code Sandbox: {'ENABLED' if sandbox.lower() == 'true' else 'LOCAL EXECUTION'}")

        return "\n".join(lines)

    def _bootstrap_options(self) -> str:
        """List self-bootstrap capabilities and execution tiers."""
        docker_status = "ON PATH" if _cmd_exists("docker") else "NOT ON PATH (install if needed)"

        import json
        devices = []
        try:
            devices = json.loads(os.environ.get("NEXUS_DEVICES", "[]"))
        except (json.JSONDecodeError, TypeError):
            pass
        device_status = f"CONNECTED ({len(devices)} devices)" if devices else "NO DEVICES"

        return f"""### Execution Tiers (YOUR CAPABILITIES)
- **Tier 0** (In-Process): Python, file I/O, web_search, http_request — ALWAYS available
- **Tier 1** (Docker): `docker_exec(command)` — {docker_status}
  Tools inside: jadx, apktool, nuclei, nmap, sqlmap, frida-tools, mitmproxy, wget, curl
  Use for: APK decompile, vuln scan, network recon, binary analysis, file download
- **Tier 2** (Device): `device_exec(command)`, `frida_run(script, package)` — {device_status}
  Use for: Frida hooks, app manipulation, traffic capture, root exploitation
- **Tier 3** (Cloud): `cloud_exec(provider, command)` — check individual providers above
  Use for: cloud enumeration, STS tokens, privilege escalation, resource provisioning
- **Tier 4** (Self-Evolve): `scaffold_custom_tool_module(...)`, `create_execution_capability(...)`
  Use for: ANYTHING ELSE — create new tools on demand, wrap Docker commands as tools

### Self-Bootstrap (MANDATORY when something is missing)
- Missing Node.js/npm? → Install it: `apt-get install nodejs npm` or `nvm install node` or download binary
- Missing Docker? → Install it: `curl -fsSL https://get.docker.com | sh` or download binary
- Missing binary tool? → `docker_exec("apt-get install X && X ...")` or install directly
- Missing Python lib? → `install_packages(packages=[...])`
- Missing custom tool? → `scaffold_custom_tool_module(...)` creates one instantly
- Missing cloud access? → `cloud_provision(...)` or ask user for credentials
- Missing device? → document steps for user, or use Docker emulator
- Missing skill? → `scaffold_skill(...)` + `reload_skills()`

**DOCTRINE: Never say "I cannot do X" or "X is not available". INSTALL IT. Bootstrap it.
Create a tool for it. 'Not pre-installed' is NEVER a reason to downgrade the solution.
Try EVERY possible way to make it work before even considering alternatives.
Only if genuinely impossible after multiple attempts: produce a script the user can run.

EXCELLENCE STANDARD: Always choose the BEST, most MODERN, most PROFESSIONAL approach.
A single HTML file is never acceptable when a proper framework project is feasible.
Think like a senior engineer shipping to production, not a student making a quick demo.**"""


def _cmd_exists(cmd: str) -> bool:
    """Check if a command exists on PATH."""
    import shutil
    return shutil.which(cmd) is not None


def _check_network() -> bool:
    """Quick network connectivity check."""
    import socket
    try:
        socket.create_connection(("8.8.8.8", 53), timeout=2).close()
        return True
    except OSError:
        return False


# Singleton
_infrastructure_map = InfrastructureMap()


def get_infrastructure_map() -> InfrastructureMap:
    return _infrastructure_map
