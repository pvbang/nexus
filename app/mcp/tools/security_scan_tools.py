"""
Nexus - Security Scanning Tools.

Provides tools for dependency vulnerability scanning, secret detection,
and HTTP security header analysis.

Self-registers with the tool registry at import time.
"""

from __future__ import annotations

import asyncio
import os
import re
from pathlib import Path
from typing import Any

import structlog

from app.mcp.tool_registry import registry

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Secret detection patterns
# ---------------------------------------------------------------------------

_SECRET_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("AWS Access Key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("Generic API Key", re.compile(r"(?i)(api[_\-]?key|apikey)\s*[=:]\s*['\"]?[a-zA-Z0-9]{20,}")),
    ("Password", re.compile(r"(?i)(password|passwd|pwd)\s*[=:]\s*['\"]?[^\s'\"]{8,}")),
    ("Secret/Token", re.compile(r"(?i)(secret|token)\s*[=:]\s*['\"]?[a-zA-Z0-9_\-]{20,}")),
    ("Private Key", re.compile(r"-----BEGIN (RSA |EC |DSA )?PRIVATE KEY-----")),
    ("Connection String", re.compile(r"(?i)(mongodb|postgres|mysql|redis)://[^\s'\"]+")),
]

_SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", ".tox"}
_SKIP_FILES = {".env.example", ".env.sample"}
_BINARY_EXTENSIONS = {
    ".pyc", ".pyo", ".so", ".dll", ".exe", ".bin", ".png", ".jpg",
    ".jpeg", ".gif", ".ico", ".pdf", ".zip", ".tar", ".gz", ".whl",
}

# ---------------------------------------------------------------------------
# Expected security headers
# ---------------------------------------------------------------------------

_SECURITY_HEADERS = [
    "Strict-Transport-Security",
    "Content-Security-Policy",
    "X-Content-Type-Options",
    "X-Frame-Options",
    "X-XSS-Protection",
    "Referrer-Policy",
    "Permissions-Policy",
    "Cache-Control",
]

# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


async def _handle_scan_dependencies(arguments: dict) -> str:
    """Check pip/npm packages for known vulnerabilities."""
    file_path = arguments.get("file_path", "")
    fmt = arguments.get("format", "pip")

    if not file_path or not os.path.isfile(file_path):
        return f"Error: file not found: {file_path}"

    try:
        content = Path(file_path).read_text(encoding="utf-8", errors="ignore")
    except OSError as e:
        return f"Error reading file: {e}"

    packages: list[str] = []

    if fmt == "pip":
        for line in content.splitlines():
            line = line.strip()
            if line and not line.startswith("#") and not line.startswith("-"):
                packages.append(line.split(";")[0].strip())
    elif fmt == "npm":
        try:
            import json
            data = json.loads(content)
            deps = data.get("dependencies", {})
            deps.update(data.get("devDependencies", {}))
            for pkg, ver in deps.items():
                packages.append(f"{pkg}@{ver}")
        except (json.JSONDecodeError, AttributeError) as e:
            return f"Error parsing package.json: {e}"
    else:
        return f"Error: unsupported format '{fmt}'. Use 'pip' or 'npm'."

    if not packages:
        return "No packages found in file."

    # Try CLI audit tools
    vulnerabilities: list[str] = []
    cli_used = False

    if fmt == "pip":
        try:
            proc = await asyncio.create_subprocess_exec(
                "pip-audit", "--requirement", file_path, "--format", "json",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=60)
            if stdout:
                import json
                results = json.loads(stdout.decode())
                for vuln in results if isinstance(results, list) else results.get("vulnerabilities", []):
                    name = vuln.get("name", "unknown")
                    version = vuln.get("version", "?")
                    vuln_id = vuln.get("id", vuln.get("vulnerability_id", "N/A"))
                    vulnerabilities.append(f"  - {name}=={version}: {vuln_id}")
                cli_used = True
        except (FileNotFoundError, asyncio.TimeoutError, Exception):
            pass

    elif fmt == "npm":
        try:
            dir_path = str(Path(file_path).parent)
            proc = await asyncio.create_subprocess_exec(
                "npm", "audit", "--json",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=dir_path,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=60)
            if stdout:
                import json
                data = json.loads(stdout.decode())
                advisories = data.get("vulnerabilities", data.get("advisories", {}))
                for key, info in (advisories.items() if isinstance(advisories, dict) else []):
                    severity = info.get("severity", "unknown") if isinstance(info, dict) else "unknown"
                    vulnerabilities.append(f"  - {key}: {severity}")
                cli_used = True
        except (FileNotFoundError, asyncio.TimeoutError, Exception):
            pass

    # Build output
    lines = [f"Scanned {len(packages)} packages | {len(vulnerabilities)} vulnerabilities found"]
    if vulnerabilities:
        lines.extend(vulnerabilities[:20])
        if len(vulnerabilities) > 20:
            lines.append(f"  ... and {len(vulnerabilities) - 20} more")
    elif cli_used:
        lines.append("No known vulnerabilities detected.")
    else:
        hint = "pip-audit" if fmt == "pip" else "npm audit"
        lines.append(f"Note: install {hint} for full CVE scan. Packages listed:")
        for pkg in packages[:15]:
            lines.append(f"  - {pkg}")
        if len(packages) > 15:
            lines.append(f"  ... and {len(packages) - 15} more")

    return "\n".join(lines)


async def _handle_scan_secrets(arguments: dict) -> str:
    """Scan files/directories for leaked secrets using regex patterns."""
    path_str = arguments.get("path", "")
    recursive = arguments.get("recursive", True)
    max_files = arguments.get("max_files", 100)

    if not path_str:
        return "Error: path is required."

    target = Path(path_str)
    if not target.exists():
        return f"Error: path not found: {path_str}"

    # Collect files
    files: list[Path] = []
    if target.is_file():
        files.append(target)
    else:
        try:
            iterator = target.rglob("*") if recursive else target.glob("*")
            for f in iterator:
                if len(files) >= max_files:
                    break
                if not f.is_file():
                    continue
                # Skip dirs
                parts = set(f.parts)
                if parts & _SKIP_DIRS:
                    continue
                if f.name in _SKIP_FILES:
                    continue
                if f.suffix.lower() in _BINARY_EXTENSIONS:
                    continue
                files.append(f)
        except PermissionError:
            pass

    findings: list[str] = []
    scanned = 0

    for fpath in files:
        try:
            content = fpath.read_text(encoding="utf-8", errors="ignore")
        except (OSError, PermissionError):
            continue
        scanned += 1

        for line_no, line in enumerate(content.splitlines(), 1):
            for secret_type, pattern in _SECRET_PATTERNS:
                match = pattern.search(line)
                if match:
                    value = match.group(0)
                    masked = value[:4] + "****" if len(value) > 4 else "****"
                    rel_path = str(fpath)
                    findings.append(f"  - {rel_path}:{line_no} [{secret_type}] {masked}")
                    break  # one finding per line

        if len(findings) >= 50:
            break

    lines = [f"Scanned {scanned} files | {len(findings)} secrets found"]
    if findings:
        lines.extend(findings[:30])
        if len(findings) > 30:
            lines.append(f"  ... and {len(findings) - 30} more")
    else:
        lines.append("No secrets detected.")

    return "\n".join(lines)


async def _handle_scan_headers(arguments: dict) -> str:
    """Check HTTP security headers of a URL."""
    url = arguments.get("url", "")
    follow_redirects = arguments.get("follow_redirects", True)

    if not url:
        return "Error: url is required."

    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    try:
        import aiohttp
    except ImportError:
        return "Error: aiohttp not installed. Run: pip install aiohttp"

    try:
        timeout = aiohttp.ClientTimeout(total=15)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url, allow_redirects=follow_redirects, ssl=False) as resp:
                headers = resp.headers
                results: list[str] = []
                present = 0

                for header in _SECURITY_HEADERS:
                    value = headers.get(header)
                    if value:
                        present += 1
                        display = value if len(value) <= 80 else value[:77] + "..."
                        results.append(f"  ✓ {header}: {display}")
                    else:
                        results.append(f"  ✗ {header}: missing")

                lines = [f"{url} | score: {present}/{len(_SECURITY_HEADERS)} headers"]
                lines.extend(results)
                return "\n".join(lines)

    except aiohttp.ClientError as e:
        return f"Error connecting to {url}: {e}"
    except asyncio.TimeoutError:
        return f"Error: timeout connecting to {url}"
    except Exception as e:
        return f"Error: {e}"


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

registry.register(
    name="scan_dependencies",
    toolset="security_scan",
    schema={
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "Path to requirements.txt or package.json",
            },
            "format": {
                "type": "string",
                "description": "Package format: 'pip' or 'npm'",
                "enum": ["pip", "npm"],
                "default": "pip",
            },
        },
        "required": ["file_path"],
    },
    handler=_handle_scan_dependencies,
    is_async=True,
    emoji="🔍",
    category="security",
)

registry.register(
    name="scan_secrets",
    toolset="security_scan",
    schema={
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "File or directory path to scan for secrets",
            },
            "recursive": {
                "type": "boolean",
                "description": "Scan subdirectories recursively",
                "default": True,
            },
            "max_files": {
                "type": "number",
                "description": "Maximum number of files to scan",
                "default": 100,
            },
        },
        "required": ["path"],
    },
    handler=_handle_scan_secrets,
    is_async=True,
    emoji="🔐",
    category="security",
)

registry.register(
    name="scan_headers",
    toolset="security_scan",
    schema={
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "URL to check for security headers",
            },
            "follow_redirects": {
                "type": "boolean",
                "description": "Follow HTTP redirects",
                "default": True,
            },
        },
        "required": ["url"],
    },
    handler=_handle_scan_headers,
    is_async=True,
    emoji="🛡️",
    category="security",
)
