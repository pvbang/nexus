"""
Nexus - Health Metrics & System Monitoring Tools.

Provides tools for health checking, process monitoring, disk usage,
port availability, latency measurement, and system resource snapshots.

Cross-platform: Works on Linux (Docker) and Windows (dev).
"""

from __future__ import annotations

import asyncio
import json
import os
import platform
import shutil
import socket
import subprocess
import sys
import time
from typing import Any

import structlog

from app.mcp.tool_registry import registry, tool_error, tool_result

logger = structlog.get_logger(__name__)

TOOLSET = "monitoring"
_IS_WINDOWS = platform.system() == "Windows"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _check_url_safety(url: str) -> str | None:
    """Validate URL via SSRF guard. Returns error message or None if safe."""
    try:
        from app.security.ssrf_guard import get_ssrf_guard
        guard = get_ssrf_guard()
        result = await guard.validate_url(url)
        if not result.safe:
            return f"URL blocked: {result.reason}"
    except ImportError:
        # SSRF guard not available — allow but log
        logger.warning("ssrf_guard_unavailable", url=url)
    return None


def _run_subprocess(cmd: list[str], timeout: int = 5) -> str:
    """Run a subprocess safely, return stdout."""
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return result.stdout
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return ""


# ---------------------------------------------------------------------------
# Tool 1: check_health
# ---------------------------------------------------------------------------

async def _handle_check_health(args: dict[str, Any]) -> str:
    """Check health of an HTTP endpoint."""
    url = args.get("url", "").strip()
    if not url:
        return tool_error("url is required")

    expected_status = int(args.get("expected_status", 200))
    timeout = min(int(args.get("timeout", 5)), 30)
    check_body = args.get("check_body", "")

    # Security: validate URL
    safety_err = await _check_url_safety(url)
    if safety_err:
        return tool_error(safety_err)

    try:
        import httpx
    except ImportError:
        return tool_error("httpx not installed")

    start = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            resp = await client.get(url)
        elapsed_ms = round((time.perf_counter() - start) * 1000, 1)
    except httpx.TimeoutException:
        return tool_result(
            success=False,
            output=f"❌ Timeout after {timeout}s connecting to {url}",
            data={"status": "timeout", "url": url},
        )
    except httpx.RequestError as e:
        return tool_result(
            success=False,
            output=f"❌ Connection error: {e}",
            data={"status": "error", "url": url, "error": str(e)},
        )

    body_preview = resp.text[:200] if resp.text else ""
    status_ok = resp.status_code == expected_status
    body_ok = (not check_body) or (check_body in resp.text)
    healthy = status_ok and body_ok

    data = {
        "healthy": healthy,
        "status_code": resp.status_code,
        "expected_status": expected_status,
        "response_time_ms": elapsed_ms,
        "body_preview": body_preview,
        "body_check_passed": body_ok,
    }

    emoji = "✅" if healthy else "❌"
    output = f"{emoji} {url} → {resp.status_code} ({elapsed_ms}ms)"
    if not body_ok:
        output += f" [body check failed: '{check_body}' not found]"

    return tool_result(success=healthy, output=output, data=data)


# ---------------------------------------------------------------------------
# Tool 2: monitor_process
# ---------------------------------------------------------------------------

async def _handle_monitor_process(args: dict[str, Any]) -> str:
    """Check if a process is running and get resource usage."""
    name = args.get("name", "").strip()
    pid = args.get("pid")

    if not name and not pid:
        return tool_error("Either 'name' or 'pid' is required")

    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, _find_process, name, pid)
    if result is None:
        target = f"PID {pid}" if pid else f"'{name}'"
        return tool_result(
            success=True,
            output=f"⚠️ Process {target} not found",
            data={"found": False},
        )

    return tool_result(success=True, output=result["summary"], data=result)


def _find_process(name: str | None, pid: int | None) -> dict[str, Any] | None:
    """Find process info cross-platform using subprocess."""
    if _IS_WINDOWS:
        return _find_process_windows(name, pid)
    return _find_process_unix(name, pid)


def _find_process_windows(name: str | None, pid: int | None) -> dict[str, Any] | None:
    """Use tasklist/wmic on Windows."""
    if pid:
        out = _run_subprocess(
            ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
        )
        if not out.strip() or "No tasks" in out:
            return None
        parts = out.strip().split(",")
        proc_name = parts[0].strip('"') if parts else "unknown"
        mem_str = parts[4].strip('"').replace(" K", "").replace(",", "") if len(parts) > 4 else "0"
        mem_mb = round(int(mem_str) / 1024, 1) if mem_str.isdigit() else 0.0
        return {
            "found": True,
            "pid": pid,
            "name": proc_name,
            "memory_mb": mem_mb,
            "cpu_percent": None,
            "command_line": proc_name,
            "summary": f"✅ Process PID={pid} ({proc_name}) — {mem_mb}MB RAM",
        }
    else:
        out = _run_subprocess(
            ["tasklist", "/FI", f"IMAGENAME eq {name}*", "/FO", "CSV", "/NH"],
        )
        if not out.strip() or "No tasks" in out:
            return None
        first_line = out.strip().splitlines()[0]
        parts = first_line.split(",")
        proc_name = parts[0].strip('"') if parts else name
        proc_pid = int(parts[1].strip('"')) if len(parts) > 1 else 0
        mem_str = parts[4].strip('"').replace(" K", "").replace(",", "") if len(parts) > 4 else "0"
        mem_mb = round(int(mem_str) / 1024, 1) if mem_str.isdigit() else 0.0
        return {
            "found": True,
            "pid": proc_pid,
            "name": proc_name,
            "memory_mb": mem_mb,
            "cpu_percent": None,
            "command_line": proc_name,
            "summary": f"✅ Process '{proc_name}' PID={proc_pid} — {mem_mb}MB RAM",
        }


def _find_process_unix(name: str | None, pid: int | None) -> dict[str, Any] | None:
    """Use ps on Unix/Linux."""
    if pid:
        out = _run_subprocess(
            ["ps", "-p", str(pid), "-o", "pid,pcpu,rss,etime,comm", "--no-headers"],
        )
        if not out.strip():
            return None
        return _parse_ps_line(out.strip().splitlines()[0])
    else:
        out = _run_subprocess(
            ["ps", "aux"],
        )
        for line in out.strip().splitlines()[1:]:
            if name and name.lower() in line.lower():
                cols = line.split(None, 10)
                if len(cols) >= 11:
                    return {
                        "found": True,
                        "pid": int(cols[1]),
                        "name": name,
                        "cpu_percent": float(cols[2]),
                        "memory_mb": round(int(cols[5]) / 1024, 1),
                        "uptime": cols[9],
                        "command_line": cols[10][:120],
                        "summary": (
                            f"✅ Process '{name}' PID={cols[1]} — "
                            f"CPU {cols[2]}%, {round(int(cols[5]) / 1024, 1)}MB RAM"
                        ),
                    }
        return None


def _parse_ps_line(line: str) -> dict[str, Any] | None:
    """Parse a ps output line: pid, %cpu, rss(kb), etime, command."""
    parts = line.split(None, 4)
    if len(parts) < 5:
        return None
    pid_val = int(parts[0])
    cpu = float(parts[1])
    mem_mb = round(int(parts[2]) / 1024, 1)
    uptime = parts[3]
    cmd = parts[4][:120]
    return {
        "found": True,
        "pid": pid_val,
        "name": cmd.split("/")[-1],
        "cpu_percent": cpu,
        "memory_mb": mem_mb,
        "uptime": uptime,
        "command_line": cmd,
        "summary": f"✅ PID={pid_val} ({cmd.split('/')[-1]}) — CPU {cpu}%, {mem_mb}MB RAM",
    }


# ---------------------------------------------------------------------------
# Tool 3: check_disk_usage
# ---------------------------------------------------------------------------

async def _handle_check_disk_usage(args: dict[str, Any]) -> str:
    """Check disk space on a path."""
    path = args.get("path", ".")
    if not os.path.exists(path):
        return tool_error(f"Path does not exist: {path}")

    try:
        usage = shutil.disk_usage(path)
    except OSError as e:
        return tool_error(f"Cannot read disk usage: {e}")

    total_gb = round(usage.total / (1024**3), 2)
    used_gb = round(usage.used / (1024**3), 2)
    free_gb = round(usage.free / (1024**3), 2)
    percent_used = round((usage.used / usage.total) * 100, 1)
    warning = percent_used >= 85.0

    data = {
        "path": path,
        "total_gb": total_gb,
        "used_gb": used_gb,
        "free_gb": free_gb,
        "percent_used": percent_used,
        "warning": warning,
        "warning_threshold": 85.0,
    }

    emoji = "⚠️" if warning else "💾"
    output = f"{emoji} Disk {path}: {used_gb}/{total_gb}GB ({percent_used}%) — {free_gb}GB free"
    return tool_result(success=True, output=output, data=data)


# ---------------------------------------------------------------------------
# Tool 4: check_port_available
# ---------------------------------------------------------------------------

async def _handle_check_port_available(args: dict[str, Any]) -> str:
    """Check if a port is free or occupied on a host."""
    port = args.get("port")
    if port is None:
        return tool_error("port is required")
    port = int(port)
    host = args.get("host", "localhost")

    # Try connecting to the port
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(1)
    try:
        result_code = sock.connect_ex((host, port))
    finally:
        sock.close()

    available = result_code != 0
    data: dict[str, Any] = {
        "port": port,
        "host": host,
        "available": available,
    }

    if not available:
        # Try to find which process is using the port
        pid_info = _find_port_process(port)
        data.update(pid_info)

    emoji = "✅" if available else "🔒"
    status = "available" if available else "in use"
    output = f"{emoji} Port {port} on {host} is {status}"
    if not available and data.get("process_name"):
        output += f" (by {data['process_name']}, PID {data.get('pid_using', '?')})"

    return tool_result(success=True, output=output, data=data)


def _find_port_process(port: int) -> dict[str, Any]:
    """Find which process is using a port."""
    if _IS_WINDOWS:
        out = _run_subprocess(["netstat", "-ano", "-p", "TCP"])
        for line in out.splitlines():
            if f":{port}" in line and "LISTENING" in line:
                parts = line.split()
                if parts:
                    pid_str = parts[-1]
                    if pid_str.isdigit():
                        return {"pid_using": int(pid_str), "process_name": _pid_to_name(int(pid_str))}
    else:
        out = _run_subprocess(["ss", "-tlnp", f"sport = :{port}"])
        if not out:
            out = _run_subprocess(["netstat", "-tlnp"])
        for line in out.splitlines():
            if f":{port}" in line:
                # Extract pid/program from ss or netstat output
                if "pid=" in line:
                    start = line.index("pid=") + 4
                    pid_str = ""
                    for ch in line[start:]:
                        if ch.isdigit():
                            pid_str += ch
                        else:
                            break
                    if pid_str:
                        return {"pid_using": int(pid_str), "process_name": _pid_to_name(int(pid_str))}
    return {"pid_using": None, "process_name": None}


def _pid_to_name(pid: int) -> str:
    """Get process name from PID."""
    if _IS_WINDOWS:
        out = _run_subprocess(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"])
        if out.strip() and "No tasks" not in out:
            parts = out.strip().split(",")
            return parts[0].strip('"') if parts else "unknown"
    else:
        out = _run_subprocess(["ps", "-p", str(pid), "-o", "comm", "--no-headers"])
        if out.strip():
            return out.strip()
    return "unknown"


# ---------------------------------------------------------------------------
# Tool 5: measure_latency
# ---------------------------------------------------------------------------

async def _handle_measure_latency(args: dict[str, Any]) -> str:
    """Measure round-trip latency to a URL or host."""
    target = args.get("target", "").strip()
    if not target:
        return tool_error("target is required")

    samples = min(int(args.get("samples", 3)), 10)
    timeout = min(int(args.get("timeout", 5)), 15)

    # If it looks like a URL, use HTTP; otherwise try TCP connect
    is_url = target.startswith("http://") or target.startswith("https://")

    if is_url:
        safety_err = await _check_url_safety(target)
        if safety_err:
            return tool_error(safety_err)

    results: list[float] = []
    errors = 0

    for _ in range(samples):
        if is_url:
            ms = await _http_latency(target, timeout)
        else:
            ms = await _tcp_latency(target, timeout)
        if ms is not None:
            results.append(ms)
        else:
            errors += 1

    if not results:
        return tool_result(
            success=False,
            output=f"❌ All {samples} samples failed for {target}",
            data={"target": target, "error": "all_samples_failed"},
        )

    min_ms = round(min(results), 1)
    max_ms = round(max(results), 1)
    avg_ms = round(sum(results) / len(results), 1)

    data = {
        "target": target,
        "min_ms": min_ms,
        "max_ms": max_ms,
        "avg_ms": avg_ms,
        "samples_taken": len(results),
        "errors": errors,
        "all_results": [round(r, 1) for r in results],
    }

    output = f"📡 {target}: avg={avg_ms}ms min={min_ms}ms max={max_ms}ms ({len(results)}/{samples} OK)"
    return tool_result(success=True, output=output, data=data)


async def _http_latency(url: str, timeout: int) -> float | None:
    """Measure HTTP GET latency in ms."""
    try:
        import httpx
        start = time.perf_counter()
        async with httpx.AsyncClient(timeout=timeout) as client:
            await client.get(url)
        return (time.perf_counter() - start) * 1000
    except Exception:
        return None


async def _tcp_latency(host: str, timeout: int) -> float | None:
    """Measure TCP connect latency to host:80 in ms."""
    target_host = host
    target_port = 80
    if ":" in host:
        parts = host.rsplit(":", 1)
        target_host = parts[0]
        try:
            target_port = int(parts[1])
        except ValueError:
            pass

    try:
        start = time.perf_counter()
        _, writer = await asyncio.wait_for(
            asyncio.open_connection(target_host, target_port),
            timeout=timeout,
        )
        elapsed = (time.perf_counter() - start) * 1000
        writer.close()
        await writer.wait_closed()
        return elapsed
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Tool 6: system_resources
# ---------------------------------------------------------------------------

async def _handle_system_resources(args: dict[str, Any]) -> str:
    """Get comprehensive system resource snapshot."""
    loop = asyncio.get_event_loop()
    data = await loop.run_in_executor(None, _collect_system_resources)
    lines = [
        f"🖥️ System Resources ({platform.system()} {platform.machine()})",
        f"  CPU: {data.get('cpu_percent', '?')}%",
        f"  RAM: {data.get('memory_used_gb', '?')}/{data.get('memory_total_gb', '?')}GB "
        f"({data.get('memory_percent', '?')}%)",
        f"  Disk: {data.get('disk_used_gb', '?')}/{data.get('disk_total_gb', '?')}GB "
        f"({data.get('disk_percent', '?')}%)",
        f"  Python RSS: {data.get('python_memory_mb', '?')}MB",
    ]
    output = "\n".join(lines)
    return tool_result(success=True, output=output, data=data)


def _collect_system_resources() -> dict[str, Any]:
    """Collect system metrics cross-platform."""
    data: dict[str, Any] = {
        "platform": platform.system(),
        "architecture": platform.machine(),
        "python_version": platform.python_version(),
    }

    # CPU percent (approximate via /proc/stat on Linux or wmic on Windows)
    data["cpu_percent"] = _get_cpu_percent()

    # Memory
    mem = _get_memory_info()
    data.update(mem)

    # Disk
    try:
        usage = shutil.disk_usage(".")
        data["disk_total_gb"] = round(usage.total / (1024**3), 2)
        data["disk_used_gb"] = round(usage.used / (1024**3), 2)
        data["disk_free_gb"] = round(usage.free / (1024**3), 2)
        data["disk_percent"] = round((usage.used / usage.total) * 100, 1)
    except OSError:
        pass

    # Python process memory
    data["python_memory_mb"] = _get_python_memory_mb()

    # Load average (Unix only)
    if not _IS_WINDOWS:
        try:
            load = os.getloadavg()
            data["load_average"] = [round(x, 2) for x in load]
        except (OSError, AttributeError):
            pass

    return data


def _get_cpu_percent() -> float | None:
    """Get CPU usage percentage."""
    if not _IS_WINDOWS:
        # Read /proc/stat twice with 0.1s interval
        try:
            with open("/proc/stat") as f:
                line1 = f.readline()
            time.sleep(0.1)
            with open("/proc/stat") as f:
                line2 = f.readline()
            vals1 = [int(x) for x in line1.split()[1:]]
            vals2 = [int(x) for x in line2.split()[1:]]
            idle1, idle2 = vals1[3], vals2[3]
            total1, total2 = sum(vals1), sum(vals2)
            diff_total = total2 - total1
            diff_idle = idle2 - idle1
            if diff_total > 0:
                return round((1 - diff_idle / diff_total) * 100, 1)
        except (FileNotFoundError, IndexError, ValueError):
            pass
    else:
        out = _run_subprocess(
            ["wmic", "cpu", "get", "loadpercentage", "/value"], timeout=3,
        )
        for line in out.splitlines():
            if "LoadPercentage=" in line:
                val = line.split("=")[1].strip()
                if val.isdigit():
                    return float(val)
    return None


def _get_memory_info() -> dict[str, Any]:
    """Get memory usage info."""
    if not _IS_WINDOWS:
        try:
            with open("/proc/meminfo") as f:
                lines = f.readlines()
            info: dict[str, int] = {}
            for line in lines:
                parts = line.split()
                if len(parts) >= 2:
                    key = parts[0].rstrip(":")
                    info[key] = int(parts[1])  # kB
            total = info.get("MemTotal", 0)
            available = info.get("MemAvailable", info.get("MemFree", 0))
            used = total - available
            return {
                "memory_total_gb": round(total / (1024**2), 2),
                "memory_used_gb": round(used / (1024**2), 2),
                "memory_free_gb": round(available / (1024**2), 2),
                "memory_percent": round((used / total) * 100, 1) if total else 0,
            }
        except (FileNotFoundError, ValueError):
            pass
    else:
        out = _run_subprocess(
            ["wmic", "os", "get", "TotalVisibleMemorySize,FreePhysicalMemory", "/value"],
            timeout=3,
        )
        vals: dict[str, int] = {}
        for line in out.splitlines():
            if "=" in line:
                key, val = line.split("=", 1)
                if val.strip().isdigit():
                    vals[key.strip()] = int(val.strip())
        total = vals.get("TotalVisibleMemorySize", 0)
        free = vals.get("FreePhysicalMemory", 0)
        used = total - free
        if total:
            return {
                "memory_total_gb": round(total / (1024**2), 2),
                "memory_used_gb": round(used / (1024**2), 2),
                "memory_free_gb": round(free / (1024**2), 2),
                "memory_percent": round((used / total) * 100, 1),
            }
    return {}


def _get_python_memory_mb() -> float:
    """Get current Python process memory in MB."""
    # Try resource module (Unix)
    try:
        import resource as res_mod
        usage = res_mod.getrusage(res_mod.RUSAGE_SELF)
        # maxrss is in KB on Linux, bytes on macOS
        if platform.system() == "Darwin":
            return round(usage.ru_maxrss / (1024**2), 1)
        return round(usage.ru_maxrss / 1024, 1)
    except (ImportError, AttributeError):
        pass
    # Fallback: sys.getsizeof approximation
    return round(sys.getsizeof(sys.modules) / (1024**2), 1)


# ===========================================================================
# Registration
# ===========================================================================

registry.register(
    name="check_health",
    toolset=TOOLSET,
    schema={
        "name": "check_health",
        "description": "Check health of an HTTP endpoint (healthcheck pattern).",
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "URL to check (e.g. http://localhost:8000/health)"},
                "expected_status": {"type": "integer", "description": "Expected HTTP status code", "default": 200},
                "timeout": {"type": "integer", "description": "Request timeout in seconds", "default": 5},
                "check_body": {"type": "string", "description": "Optional substring to find in response body"},
            },
            "required": ["url"],
        },
    },
    handler=_handle_check_health,
    is_async=True,
    emoji="💓",
    category="observability",
)

registry.register(
    name="monitor_process",
    toolset=TOOLSET,
    schema={
        "name": "monitor_process",
        "description": "Check if a process is running and get its resource usage.",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Process name to search for (e.g. python, node)"},
                "pid": {"type": "integer", "description": "Specific PID to check"},
            },
        },
    },
    handler=_handle_monitor_process,
    is_async=True,
    emoji="🔍",
    category="observability",
)

registry.register(
    name="check_disk_usage",
    toolset=TOOLSET,
    schema={
        "name": "check_disk_usage",
        "description": "Check disk space on a path.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path to check (default: workspace root)", "default": "."},
            },
        },
    },
    handler=_handle_check_disk_usage,
    is_async=True,
    emoji="💾",
    category="observability",
)

registry.register(
    name="check_port_available",
    toolset=TOOLSET,
    schema={
        "name": "check_port_available",
        "description": "Check if a port is free or in-use on a host.",
        "parameters": {
            "type": "object",
            "properties": {
                "port": {"type": "integer", "description": "Port number to check"},
                "host": {"type": "string", "description": "Hostname to check", "default": "localhost"},
            },
            "required": ["port"],
        },
    },
    handler=_handle_check_port_available,
    is_async=True,
    emoji="🔌",
    category="observability",
)

registry.register(
    name="measure_latency",
    toolset=TOOLSET,
    schema={
        "name": "measure_latency",
        "description": "Measure round-trip latency to a URL or host (multiple samples).",
        "parameters": {
            "type": "object",
            "properties": {
                "target": {"type": "string", "description": "URL or hostname to measure"},
                "samples": {"type": "integer", "description": "Number of measurements (max 10)", "default": 3},
                "timeout": {"type": "integer", "description": "Per-request timeout in seconds", "default": 5},
            },
            "required": ["target"],
        },
    },
    handler=_handle_measure_latency,
    is_async=True,
    emoji="📡",
    category="observability",
)

registry.register(
    name="system_resources",
    toolset=TOOLSET,
    schema={
        "name": "system_resources",
        "description": "Get comprehensive system resource snapshot (CPU, RAM, disk, network).",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    handler=_handle_system_resources,
    is_async=True,
    emoji="🖥️",
    category="observability",
)
