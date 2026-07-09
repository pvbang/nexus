"""
MekongAI Nexus - Archive Tools.

Create and extract ZIP/TAR archives.
Self-registers with the tool registry at import time.
"""

from __future__ import annotations

import os
import shutil
import tarfile
import zipfile
from pathlib import Path
from typing import Any

import structlog

from app.core.command_guard import check_path_safety
from app.mcp.tool_registry import registry, tool_result, tool_error
from app.mcp.tools.filesystem import get_task_workspace, WORKSPACE_DIR

logger = structlog.get_logger(__name__)


async def _handle_compress(args: dict[str, Any]) -> str:
    """Create a ZIP or TAR.GZ archive from files/directories."""
    source = args.get("source", "").strip()
    output = args.get("output", "").strip()
    fmt = args.get("format", "zip").lower()

    if not source:
        return tool_error("Source path is required")

    verdict = check_path_safety(source, str(WORKSPACE_DIR))
    if not verdict.safe:
        return tool_error(f"Source path blocked: {verdict.reason}")

    # Resolve source: try task workspace first, fallback to global (read-only compat)
    ws = get_task_workspace()
    src_path = ws / source
    if not src_path.exists():
        src_path = WORKSPACE_DIR / source
    if not src_path.exists():
        return tool_error(f"Source not found: {source}")

    # Default output name
    if not output:
        ext = ".zip" if fmt == "zip" else ".tar.gz"
        output = f"{src_path.stem}{ext}"

    verdict2 = check_path_safety(output, str(WORKSPACE_DIR))
    if not verdict2.safe:
        return tool_error(f"Output path blocked: {verdict2.reason}")

    # Output always goes to task workspace
    out_path = ws / output
    out_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        if fmt == "zip":
            with zipfile.ZipFile(str(out_path), "w", zipfile.ZIP_DEFLATED) as zf:
                if src_path.is_dir():
                    for root, _dirs, files in os.walk(src_path):
                        for f in files:
                            fp = Path(root) / f
                            zf.write(fp, fp.relative_to(src_path))
                else:
                    zf.write(src_path, src_path.name)
        elif fmt in ("tar", "tar.gz", "tgz"):
            mode = "w:gz" if fmt in ("tar.gz", "tgz") else "w"
            with tarfile.open(str(out_path), mode) as tf:
                tf.add(str(src_path), arcname=src_path.name)
        else:
            return tool_error(f"Unsupported format: {fmt}. Use: zip, tar.gz")

        size = out_path.stat().st_size
        relative_path = out_path.relative_to(WORKSPACE_DIR)
        download_url = f"/api/v1/artifacts/download/{relative_path}"
        return tool_result(
            success=True,
            output=f"📦 Archive created: {output} ({size:,} bytes)",
            data={"path": str(relative_path), "download_url": download_url, "size": size, "format": fmt},
        )
    except Exception as e:
        return tool_error(f"Compression failed: {e}")


async def _handle_extract(args: dict[str, Any]) -> str:
    """Extract a ZIP or TAR archive."""
    archive = args.get("archive", "").strip()
    destination = args.get("destination", "").strip()

    if not archive:
        return tool_error("Archive path is required")

    verdict = check_path_safety(archive, str(WORKSPACE_DIR))
    if not verdict.safe:
        return tool_error(f"Archive path blocked: {verdict.reason}")

    # Resolve archive: try task workspace first, fallback to global (read-only compat)
    ws = get_task_workspace()
    arc_path = ws / archive
    if not arc_path.exists():
        arc_path = WORKSPACE_DIR / archive
    if not arc_path.exists():
        return tool_error(f"Archive not found: {archive}")

    if not destination:
        destination = arc_path.stem

    verdict2 = check_path_safety(destination, str(WORKSPACE_DIR))
    if not verdict2.safe:
        return tool_error(f"Destination path blocked: {verdict2.reason}")

    # Output always goes to task workspace
    dst_path = ws / destination
    dst_path.mkdir(parents=True, exist_ok=True)

    try:
        suffix = arc_path.suffix.lower()
        suffixes = "".join(s.lower() for s in arc_path.suffixes)

        if suffix == ".zip":
            with zipfile.ZipFile(str(arc_path), "r") as zf:
                zf.extractall(str(dst_path))
                file_count = len(zf.namelist())
        elif suffixes.endswith(".tar.gz") or suffix in (".tgz", ".tar"):
            with tarfile.open(str(arc_path), "r:*") as tf:
                tf.extractall(str(dst_path), filter="data")
                file_count = len(tf.getnames())
        else:
            return tool_error(f"Unsupported archive format: {suffix}")

        return tool_result(
            success=True,
            output=f"📂 Extracted {file_count} items to {destination}/",
            data={"destination": destination, "file_count": file_count},
        )
    except Exception as e:
        return tool_error(f"Extraction failed: {e}")


# ------------------------------------------------------------------
# Registration
# ------------------------------------------------------------------

registry.register(
    name="compress",
    toolset="filesystem",
    schema={
        "description": "Create a ZIP or TAR.GZ archive from files/directories in workspace.",
        "parameters": {
            "type": "object",
            "properties": {
                "source": {"type": "string", "description": "Source file/directory to compress"},
                "output": {"type": "string", "description": "Output archive filename (auto-generated if empty)"},
                "format": {"type": "string", "enum": ["zip", "tar.gz"], "description": "Archive format (default: zip)"},
            },
            "required": ["source"],
        },
    },
    handler=_handle_compress,
    is_async=True,
    emoji="📦",
)

registry.register(
    name="extract",
    toolset="filesystem",
    schema={
        "description": "Extract a ZIP or TAR archive in workspace.",
        "parameters": {
            "type": "object",
            "properties": {
                "archive": {"type": "string", "description": "Archive file path to extract"},
                "destination": {"type": "string", "description": "Destination directory (default: archive name)"},
            },
            "required": ["archive"],
        },
    },
    handler=_handle_extract,
    is_async=True,
    emoji="📂",
)
