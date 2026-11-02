"""
Nexus - Device Bridge Tools (Tier 2).

ADB/SSH connection to rooted Android devices for:
- Frida script deployment and execution
- App manipulation (install, launch, interact)
- Traffic capture and analysis
- File push/pull between workspace and device
"""

import asyncio
import json
import os
import shutil
from typing import Any

import structlog

from app.mcp.tool_registry import registry

logger = structlog.get_logger(__name__)

_DEVICE_TIMEOUT = int(os.environ.get("DEVICE_EXEC_TIMEOUT", "60"))


def _get_devices() -> list[dict]:
    """Parse configured devices from env."""
    raw = os.environ.get("NEXUS_DEVICES", "[]")
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []


def _device_available() -> bool:
    return bool(_get_devices()) and shutil.which("adb") is not None


def _get_device_address(device_id: str = "default") -> str:
    """Resolve device address from config."""
    devices = _get_devices()
    if not devices:
        return ""
    if device_id == "default":
        return devices[0].get("address", "")
    for d in devices:
        if d.get("id") == device_id:
            return d.get("address", "")
    return devices[0].get("address", "")


# === device_exec ===
registry.register(
    name="device_exec",
    toolset="device",
    category="mobile-testing",
    schema={
        "type": "function",
        "function": {
            "name": "device_exec",
            "description": (
                "Execute shell command on connected Android device via ADB. "
                "Device must be rooted with Frida server running. "
                "Use for: running frida, logcat, pm/am commands, dumpsys, "
                "file operations on device, app manipulation."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "ADB shell command to execute",
                    },
                    "device_id": {
                        "type": "string",
                        "description": "Device identifier (default: first configured device)",
                        "default": "default",
                    },
                    "as_root": {
                        "type": "boolean",
                        "description": "Run as root (su -c)",
                        "default": False,
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "Timeout in seconds",
                        "default": 60,
                    },
                },
                "required": ["command"],
            },
        },
    },
    handler=None,
    check_fn=_device_available,
    requires_env=["NEXUS_DEVICES"],
    emoji="📱",
)


async def _device_exec_handler(
    command: str,
    device_id: str = "default",
    as_root: bool = False,
    timeout: int = 0,
) -> dict[str, Any]:
    """Execute command on Android device via ADB."""
    address = _get_device_address(device_id)
    if not address:
        return {"success": False, "output": "No device configured. Set NEXUS_DEVICES env var."}

    timeout = timeout or _DEVICE_TIMEOUT
    shell_cmd = f"su -c '{command}'" if as_root else command
    adb_cmd = ["adb", "-s", address, "shell", shell_cmd]

    try:
        proc = await asyncio.create_subprocess_exec(
            *adb_cmd,
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
        return {"success": False, "output": f"Device command timed out after {timeout}s"}
    except Exception as e:
        return {"success": False, "output": f"ADB error: {e}"}


registry._tools["device_exec"].handler = _device_exec_handler
registry._tools["device_exec"].is_async = True


# === frida_run ===
registry.register(
    name="frida_run",
    toolset="device",
    category="mobile-testing",
    schema={
        "type": "function",
        "function": {
            "name": "frida_run",
            "description": (
                "Run Frida script against app on connected rooted device. "
                "Pushes script to device, spawns/attaches to app, captures output. "
                "Use for: SSL pinning bypass, auth token capture, hook methods, "
                "bypass root detection, intercept API calls, WebView bridge enum."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "script_path": {
                        "type": "string",
                        "description": "Path to Frida JS script (in workspace)",
                    },
                    "package": {
                        "type": "string",
                        "description": "Target app package name (e.g. com.newleaf.app.android.victor)",
                    },
                    "spawn": {
                        "type": "boolean",
                        "description": "Spawn app (true) or attach to running (false)",
                        "default": True,
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "How long to let script run (seconds)",
                        "default": 30,
                    },
                    "device_id": {
                        "type": "string",
                        "default": "default",
                    },
                },
                "required": ["script_path", "package"],
            },
        },
    },
    handler=None,
    check_fn=_device_available,
    requires_env=["NEXUS_DEVICES"],
    emoji="🪝",
)


async def _frida_run_handler(
    script_path: str,
    package: str,
    spawn: bool = True,
    timeout: int = 30,
    device_id: str = "default",
) -> dict[str, Any]:
    """Deploy and run Frida script on device."""
    address = _get_device_address(device_id)
    if not address:
        return {"success": False, "output": "No device configured."}

    # Resolve script path
    from app.core.execution_context import current_task_id
    task_id = current_task_id.get("")
    if task_id and not os.path.isabs(script_path):
        from app.core import task_paths
        script_path = str(task_paths.work_dir(task_id) / script_path)

    if not os.path.exists(script_path):
        return {"success": False, "output": f"Script not found: {script_path}"}

    # Push script to device
    remote_path = f"/data/local/tmp/nexus_hook.js"
    push_cmd = ["adb", "-s", address, "push", script_path, remote_path]
    try:
        proc = await asyncio.create_subprocess_exec(*push_cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        await proc.communicate()
    except Exception as e:
        return {"success": False, "output": f"Failed to push script: {e}"}

    # Run frida
    spawn_flag = "-f" if spawn else ""
    frida_cmd = ["adb", "-s", address, "shell",
                 f"frida -U {spawn_flag} {package} -l {remote_path} --no-pause -q"]

    try:
        proc = await asyncio.create_subprocess_exec(
            *frida_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        output = stdout.decode("utf-8", errors="replace")
        errors = stderr.decode("utf-8", errors="replace")

        return {
            "success": True,
            "output": f"[Frida output for {package}]\n{output}\n{errors}"[:30000],
        }
    except asyncio.TimeoutError:
        return {
            "success": True,
            "output": f"Frida ran for {timeout}s (normal for hooks). Check logcat for continued output.",
        }
    except Exception as e:
        return {"success": False, "output": f"Frida error: {e}"}


registry._tools["frida_run"].handler = _frida_run_handler
registry._tools["frida_run"].is_async = True


# === device_push / device_pull ===
registry.register(
    name="device_push",
    toolset="device",
    category="mobile-testing",
    schema={
        "type": "function",
        "function": {
            "name": "device_push",
            "description": "Push file from workspace to Android device",
            "parameters": {
                "type": "object",
                "properties": {
                    "local_path": {"type": "string", "description": "Local file path"},
                    "remote_path": {"type": "string", "description": "Path on device"},
                    "device_id": {"type": "string", "default": "default"},
                },
                "required": ["local_path", "remote_path"],
            },
        },
    },
    handler=None,
    check_fn=_device_available,
    requires_env=["NEXUS_DEVICES"],
    emoji="📤",
)


async def _device_push_handler(local_path: str, remote_path: str, device_id: str = "default") -> dict:
    address = _get_device_address(device_id)
    if not address:
        return {"success": False, "output": "No device configured."}
    try:
        proc = await asyncio.create_subprocess_exec(
            "adb", "-s", address, "push", local_path, remote_path,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
        return {"success": proc.returncode == 0, "output": stdout.decode() + stderr.decode()}
    except Exception as e:
        return {"success": False, "output": str(e)}


registry._tools["device_push"].handler = _device_push_handler
registry._tools["device_push"].is_async = True


registry.register(
    name="device_pull",
    toolset="device",
    category="mobile-testing",
    schema={
        "type": "function",
        "function": {
            "name": "device_pull",
            "description": "Pull file from Android device to workspace",
            "parameters": {
                "type": "object",
                "properties": {
                    "remote_path": {"type": "string", "description": "Path on device"},
                    "local_path": {"type": "string", "description": "Local destination path"},
                    "device_id": {"type": "string", "default": "default"},
                },
                "required": ["remote_path", "local_path"],
            },
        },
    },
    handler=None,
    check_fn=_device_available,
    requires_env=["NEXUS_DEVICES"],
    emoji="📥",
)


async def _device_pull_handler(remote_path: str, local_path: str, device_id: str = "default") -> dict:
    address = _get_device_address(device_id)
    if not address:
        return {"success": False, "output": "No device configured."}
    try:
        proc = await asyncio.create_subprocess_exec(
            "adb", "-s", address, "pull", remote_path, local_path,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
        return {"success": proc.returncode == 0, "output": stdout.decode() + stderr.decode()}
    except Exception as e:
        return {"success": False, "output": str(e)}


registry._tools["device_pull"].handler = _device_pull_handler
registry._tools["device_pull"].is_async = True
