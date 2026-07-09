"""
MekongAI Nexus - Artifacts Endpoints.

Quản lý kết quả thực thi: files, reports, code output
mà agents tạo ra trong workspace.

Supports binary files (DOCX, PDF, images) with proper handling:
- Binary files return metadata + download link (not raw content)
- Text files return full content for inline preview

Session-based grouping:
- Tasks are grouped by session_id (conversation)
- Uses .task_meta.json for fast title/session resolution
- Fallback to DB prefix matching when meta file doesn't exist
"""

import asyncio
import io
import json
import mimetypes
import os
import zipfile
from datetime import datetime, timezone
from functools import partial
from pathlib import Path
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy import cast, select, String
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_download_user
from app.core.dependencies import get_db
from app.models.database import ConversationMessageModel, TaskModel

# Optional: aiofiles for async file I/O
try:
    import aiofiles
    HAS_AIOFILES = True
except ImportError:
    HAS_AIOFILES = False

logger = structlog.get_logger(__name__)
router = APIRouter()
download_router = APIRouter(tags=["artifacts"])

WORKSPACE_DIR = Path(os.environ.get("WORKSPACE_DIR", "workspace_data")).resolve()

# Binary extensions that should NOT be read as text
_BINARY_EXTENSIONS = frozenset({
    ".docx", ".xlsx", ".pptx", ".doc", ".xls", ".ppt",
    ".pdf", ".zip", ".tar", ".gz", ".rar", ".7z",
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".ico", ".svg",
    ".mp3", ".wav", ".ogg", ".mp4", ".avi", ".mkv",
    ".woff", ".woff2", ".ttf", ".otf", ".eot",
    ".exe", ".dll", ".so", ".dylib",
    ".sqlite", ".db",
})

# Preview type mapping by extension
_PREVIEW_TYPE_MAP: dict[str, str] = {}
_PREVIEW_EXTENSIONS = {
    "image": {"png", "jpg", "jpeg", "gif", "svg", "webp", "bmp", "ico"},
    "video": {"mp4", "webm", "ogg", "mov", "avi", "mkv"},
    "audio": {"mp3", "wav", "ogg", "flac", "aac", "m4a"},
    "pdf": {"pdf"},
    "markdown": {"md", "mdx"},
    "html": {"html", "htm"},
    "document": {"docx", "doc", "pptx", "ppt"},
    "spreadsheet": {"xlsx", "xls"},
    "code": {
        "py", "js", "ts", "tsx", "jsx", "json", "jsonl", "ndjson",
        "yaml", "yml", "toml",
        "xml", "css", "scss", "sql", "sh", "bash", "go", "rs", "java",
        "c", "cpp", "h", "hpp", "rb", "php", "swift", "kt",
    },
    "text": {"txt", "log", "csv", "env", "cfg", "ini", "conf"},
}
for _ptype, _exts in _PREVIEW_EXTENSIONS.items():
    for _ext in _exts:
        _PREVIEW_TYPE_MAP[_ext] = _ptype


def _is_binary_file(filename: str) -> bool:
    """Check if a file is binary based on extension."""
    return Path(filename).suffix.lower() in _BINARY_EXTENSIONS


def _validate_path_within_workspace(base_dir: Path, rel_path: str) -> Path:
    """Validate and resolve a path, ensuring it stays within the workspace.

    Raises HTTPException 403 on path traversal attempts.
    """
    full_path = (base_dir / rel_path).resolve()
    if not str(full_path).startswith(str(base_dir.resolve())):
        raise HTTPException(status_code=403, detail="Access denied: path traversal")
    return full_path


def _resolve_workspace(task_id: str | None) -> Path:
    """Resolve the base directory: task workspace or global workspace.

    Uses the central resolver so nested subtasks and legacy-flat tasks both
    resolve correctly. Falls back to the canonical (possibly not-yet-created)
    task dir when nothing exists on disk.
    """
    if task_id:
        from app.core import task_paths
        return task_paths.find_task_dir(task_id) or task_paths.task_dir(task_id)
    return WORKSPACE_DIR


def _get_preview_type(filename: str) -> str:
    """Determine preview type based on file extension."""
    ext = Path(filename).suffix.lower().lstrip(".")
    if ext in _PREVIEW_TYPE_MAP:
        return _PREVIEW_TYPE_MAP[ext]
    if _is_binary_file(filename):
        return "unknown"
    return "text"


def _detect_encoding(file_path: Path) -> str | None:
    """Return encoding for text files, None for binary."""
    if _is_binary_file(file_path.name):
        return None
    return "utf-8"


def _count_lines(file_path: Path, max_size: int = 10 * 1024 * 1024) -> int | None:
    """Count lines in a text file. Returns None for binary or files > max_size."""
    if _is_binary_file(file_path.name):
        return None
    if file_path.stat().st_size > max_size:
        return None
    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            return sum(1 for _ in f)
    except Exception:
        return None


# ============================================================
# NEW: GET /artifacts/tree — Lazy Directory Listing
# ============================================================


@router.get("/tree")
async def get_directory_tree(
    path: str = "",
    depth: int = Query(default=1, ge=1, le=10),
    show_hidden: bool = False,
    task_id: str | None = None,
) -> list[dict[str, Any]]:
    """Lazy directory listing with configurable depth.

    Args:
        path: Relative path from workspace root ("" = root).
        depth: How many levels deep to list (default 1 = immediate children).
        show_hidden: Whether to include hidden files (starting with '.').
        task_id: Optional task ID to scope to task workspace.

    Returns:
        List of directory entries sorted: directories first, then alphabetical.
    """
    base_dir = _resolve_workspace(task_id)
    base_dir.mkdir(parents=True, exist_ok=True)

    target_dir = _validate_path_within_workspace(base_dir, path)
    if not target_dir.exists():
        raise HTTPException(status_code=404, detail="Directory not found")
    if not target_dir.is_dir():
        raise HTTPException(status_code=400, detail="Path is not a directory")

    loop = asyncio.get_event_loop()
    entries = await loop.run_in_executor(
        None,
        partial(_scan_directory, target_dir, base_dir, depth, show_hidden),
    )
    return entries


def _scan_directory(
    target_dir: Path,
    base_dir: Path,
    depth: int,
    show_hidden: bool,
) -> list[dict[str, Any]]:
    """Scan directory using os.scandir() — non-recursive per level."""
    entries: list[dict[str, Any]] = []

    try:
        with os.scandir(target_dir) as it:
            for entry in it:
                # Skip hidden files unless requested
                if not show_hidden and entry.name.startswith("."):
                    continue

                try:
                    stat = entry.stat()
                    modified = datetime.fromtimestamp(
                        stat.st_mtime, tz=timezone.utc
                    ).isoformat()
                    rel_path = str(Path(entry.path).relative_to(base_dir))

                    if entry.is_dir(follow_symlinks=False):
                        # Count immediate children
                        children_count = _count_children(entry.path, show_hidden)
                        item: dict[str, Any] = {
                            "name": entry.name,
                            "path": rel_path,
                            "type": "directory",
                            "modified": modified,
                            "children_count": children_count,
                        }
                        # Recursive if depth > 1
                        if depth > 1:
                            item["children"] = _scan_directory(
                                Path(entry.path), base_dir, depth - 1, show_hidden
                            )
                        entries.append(item)
                    elif entry.is_file(follow_symlinks=False):
                        entries.append({
                            "name": entry.name,
                            "path": rel_path,
                            "type": "file",
                            "size": stat.st_size,
                            "modified": modified,
                            "is_binary": _is_binary_file(entry.name),
                        })
                except (OSError, PermissionError, ValueError):
                    continue
    except (OSError, PermissionError):
        pass

    # Sort: tasks directory by modified DESC (newest first), others alphabetical
    if _is_tasks_directory(target_dir, base_dir):
        entries = _enrich_task_entries(entries, target_dir)
        entries.sort(key=lambda e: e.get("modified", ""), reverse=True)
    else:
        entries.sort(key=lambda e: (0 if e["type"] == "directory" else 1, e["name"].lower()))
    return entries


def _is_tasks_directory(target_dir: Path, base_dir: Path) -> bool:
    """Check if we're listing the tasks directory."""
    return target_dir.name == "tasks" and target_dir.parent == base_dir


def _enrich_task_entries(entries: list[dict[str, Any]], tasks_dir: Path) -> list[dict[str, Any]]:
    """Add display_name from .task_meta.json for task directories."""
    for entry in entries:
        if entry["type"] == "directory":
            meta = _read_task_meta(tasks_dir / entry["name"])
            if meta:
                title = meta.get("title", "")
                if title:
                    entry["display_name"] = title
                created = meta.get("created_at")
                if created:
                    entry["modified"] = created
    return entries


def _count_children(dir_path: str, show_hidden: bool) -> int:
    """Count immediate children in a directory."""
    count = 0
    try:
        with os.scandir(dir_path) as it:
            for entry in it:
                if not show_hidden and entry.name.startswith("."):
                    continue
                count += 1
    except (OSError, PermissionError):
        pass
    return count


# ============================================================
# NEW: GET /artifacts/meta/{path:path} — File Metadata
# ============================================================


@router.get("/meta/{file_path:path}")
async def get_file_metadata(
    file_path: str,
    task_id: str | None = None,
) -> dict[str, Any]:
    """Get detailed metadata for a file without reading its content.

    Returns file info including MIME type, encoding, line count,
    and preview type for frontend rendering decisions.
    """
    base_dir = _resolve_workspace(task_id)
    full_path = _validate_path_within_workspace(base_dir, file_path)

    if not full_path.exists():
        raise HTTPException(status_code=404, detail="File not found")
    if not full_path.is_file():
        raise HTTPException(status_code=400, detail="Path is not a file")

    loop = asyncio.get_event_loop()
    metadata = await loop.run_in_executor(
        None, partial(_build_file_metadata, full_path, file_path)
    )
    return metadata


def _build_file_metadata(full_path: Path, rel_path: str) -> dict[str, Any]:
    """Build metadata dict for a file (runs in executor)."""
    stat = full_path.stat()
    is_binary = _is_binary_file(full_path.name)
    mime_type, _ = mimetypes.guess_type(full_path.name)
    if mime_type is None:
        mime_type = "application/octet-stream"

    encoding = _detect_encoding(full_path)
    line_count = _count_lines(full_path) if not is_binary else None
    preview_type = _get_preview_type(full_path.name)
    can_preview = preview_type != "unknown"

    modified = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat()

    return {
        "name": full_path.name,
        "path": rel_path,
        "size": stat.st_size,
        "modified": modified,
        "mime_type": mime_type,
        "is_binary": is_binary,
        "encoding": encoding,
        "line_count": line_count,
        "can_preview": can_preview,
        "preview_type": preview_type,
    }


# ============================================================
# NEW: GET /artifacts/stream/{path:path} — Range-based Streaming
# ============================================================


@download_router.get("/stream/{file_path:path}")
async def stream_file(
    file_path: str,
    task_id: str | None = None,
    request: Request = None,
    user: str = Depends(get_download_user),
):
    """Stream a file with HTTP Range support for video/audio/PDF.

    Supports:
    - Full file streaming (200) when no Range header present.
    - Partial content (206) with proper Range/Content-Range headers.
    - Chunk size: 64KB.

    Registered on ``download_router`` (not the JWT-protected router) so that
    browser media elements (``<img>``/``<video>``/``<audio>``), which cannot
    send an Authorization header, can authenticate via the ``?token=`` query
    parameter through :func:`get_download_user`.
    """
    base_dir = _resolve_workspace(task_id)
    full_path = _validate_path_within_workspace(base_dir, file_path)

    if not full_path.exists():
        raise HTTPException(status_code=404, detail="File not found")
    if not full_path.is_file():
        raise HTTPException(status_code=400, detail="Path is not a file")

    file_size = full_path.stat().st_size
    mime_type, _ = mimetypes.guess_type(full_path.name)
    if mime_type is None:
        mime_type = "application/octet-stream"

    # Parse Range header
    range_header = request.headers.get("range") if request else None

    if range_header:
        # Parse "bytes=start-end"
        try:
            range_spec = range_header.strip().lower()
            if not range_spec.startswith("bytes="):
                raise ValueError("Invalid range format")
            range_val = range_spec[6:]
            parts = range_val.split("-", 1)
            start = int(parts[0]) if parts[0] else 0
            end = int(parts[1]) if parts[1] else file_size - 1
        except (ValueError, IndexError):
            raise HTTPException(status_code=416, detail="Invalid Range header")

        # Validate range
        if start >= file_size or end >= file_size or start > end:
            raise HTTPException(
                status_code=416,
                detail="Range not satisfiable",
                headers={"Content-Range": f"bytes */{file_size}"},
            )

        content_length = end - start + 1
        headers = {
            "Content-Range": f"bytes {start}-{end}/{file_size}",
            "Accept-Ranges": "bytes",
            "Content-Length": str(content_length),
            "Content-Type": mime_type,
        }

        return StreamingResponse(
            _file_stream_generator(full_path, start, end),
            status_code=206,
            headers=headers,
            media_type=mime_type,
        )

    # No Range header: stream full file
    headers = {
        "Accept-Ranges": "bytes",
        "Content-Length": str(file_size),
        "Content-Type": mime_type,
    }
    return StreamingResponse(
        _file_stream_generator(full_path, 0, file_size - 1),
        status_code=200,
        headers=headers,
        media_type=mime_type,
    )


async def _file_stream_generator(
    path: Path, start: int, end: int, chunk_size: int = 65536
):
    """Async generator that streams file bytes in chunks.

    Uses aiofiles if available, otherwise falls back to sync read in executor.
    """
    if HAS_AIOFILES:
        async with aiofiles.open(path, "rb") as f:
            await f.seek(start)
            remaining = end - start + 1
            while remaining > 0:
                read_size = min(chunk_size, remaining)
                data = await f.read(read_size)
                if not data:
                    break
                remaining -= len(data)
                yield data
    else:
        # Sync fallback: read in executor per chunk
        loop = asyncio.get_event_loop()
        offset = start
        remaining = end - start + 1
        while remaining > 0:
            read_size = min(chunk_size, remaining)
            data = await loop.run_in_executor(
                None, partial(_sync_read_chunk, path, offset, read_size)
            )
            if not data:
                break
            remaining -= len(data)
            offset += len(data)
            yield data


def _sync_read_chunk(path: Path, offset: int, size: int) -> bytes:
    """Read a chunk of bytes from a file synchronously."""
    with open(path, "rb") as f:
        f.seek(offset)
        return f.read(size)


# ============================================================
# Original endpoints (preserved)
# ============================================================


@router.get("/")
async def list_artifacts() -> list[dict[str, Any]]:
    """Liệt kê tất cả files trong workspace (artifacts do agents tạo)."""
    WORKSPACE_DIR.mkdir(exist_ok=True)
    artifacts = []

    for root, dirs, files in os.walk(WORKSPACE_DIR):
        for f in files:
            full_path = Path(root) / f
            rel_path = full_path.relative_to(WORKSPACE_DIR)
            file_type = _guess_type(f)
            is_binary = _is_binary_file(f)
            artifacts.append({
                "path": str(rel_path),
                "name": f,
                "size": full_path.stat().st_size,
                "modified": full_path.stat().st_mtime,
                "type": file_type,
                "is_binary": is_binary,
                "download_url": f"/api/v1/artifacts/download/{rel_path}",
            })

    return sorted(artifacts, key=lambda x: x["modified"], reverse=True)


@router.get("/read/{path:path}")
async def read_artifact(
    path: str,
    task_id: str | None = None,
    offset: int = Query(default=0, ge=0, description="Byte offset to start reading"),
    limit: int | None = Query(default=None, ge=1, description="Max bytes to read"),
    lines_offset: int | None = Query(default=None, ge=0, description="Start from line N"),
    lines_limit: int | None = Query(default=None, ge=1, description="Read N lines"),
) -> dict[str, Any]:
    """
    Đọc nội dung artifact.

    - Text files: returns full content for inline preview
    - Binary files: returns metadata + download_url (NOT raw bytes)

    Supports chunked reading via offset/limit (byte-based) or
    lines_offset/lines_limit (line-based, takes priority).
    When called with default params (offset=0, limit=None), behavior
    is identical to the original endpoint (backward-compatible).
    """
    base_dir = _resolve_workspace(task_id)
    full_path = _validate_path_within_workspace(base_dir, path)

    if not full_path.exists():
        raise HTTPException(status_code=404, detail="Artifact not found")

    is_binary = _is_binary_file(full_path.name)
    file_size = full_path.stat().st_size

    if is_binary:
        # Binary file: return metadata only (no garbled content)
        return {
            "path": path,
            "content": None,
            "size": file_size,
            "total_size": file_size,
            "type": _guess_type(path),
            "is_binary": True,
            "download_url": f"/api/v1/artifacts/download/{path}",
            "message": f"Binary file ({_guess_mime_type(full_path.name)}). Use download to view.",
        }

    # Determine if chunked reading is requested
    is_chunked = (offset > 0 or limit is not None or
                  lines_offset is not None or lines_limit is not None)

    loop = asyncio.get_event_loop()

    if is_chunked and (lines_offset is not None or lines_limit is not None):
        # Line-based reading (takes priority over byte-based)
        result = await loop.run_in_executor(
            None,
            partial(_read_lines, full_path, lines_offset or 0, lines_limit),
        )
        return {
            "path": path,
            "content": result["content"],
            "total_size": file_size,
            "offset": result["byte_offset"],
            "length": len(result["content"]),
            "has_more": result["has_more"],
            "total_lines": result["total_lines"],
            "encoding": "utf-8",
            "type": _guess_type(path),
            "is_binary": False,
        }
    elif is_chunked:
        # Byte-based chunked reading
        result = await loop.run_in_executor(
            None,
            partial(_read_bytes, full_path, offset, limit),
        )
        return {
            "path": path,
            "content": result["content"],
            "total_size": file_size,
            "offset": offset,
            "length": len(result["content"]),
            "has_more": result["has_more"],
            "total_lines": result["total_lines"],
            "encoding": "utf-8",
            "type": _guess_type(path),
            "is_binary": False,
        }
    else:
        # Original behavior: read full file
        try:
            content = await loop.run_in_executor(
                None,
                partial(full_path.read_text, encoding="utf-8", errors="replace"),
            )
            return {
                "path": path,
                "content": content,
                "size": len(content),
                "type": _guess_type(path),
                "is_binary": False,
            }
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))


def _read_bytes(file_path: Path, offset: int, limit: int | None) -> dict[str, Any]:
    """Read file content by byte range (runs in executor)."""
    file_size = file_path.stat().st_size
    with open(file_path, "r", encoding="utf-8", errors="replace") as f:
        f.seek(offset)
        if limit is not None:
            content = f.read(limit)
        else:
            content = f.read()

    actual_end = offset + len(content.encode("utf-8"))
    has_more = actual_end < file_size

    # Count total lines only for files < 10MB
    total_lines = None
    if file_size < 10 * 1024 * 1024:
        total_lines = _count_lines(file_path)

    return {
        "content": content,
        "has_more": has_more,
        "total_lines": total_lines,
    }


def _read_lines(
    file_path: Path, lines_offset: int, lines_limit: int | None
) -> dict[str, Any]:
    """Read file content by line range (runs in executor)."""
    file_size = file_path.stat().st_size
    lines: list[str] = []
    total_lines = 0
    byte_offset = 0

    with open(file_path, "r", encoding="utf-8", errors="replace") as f:
        # Skip to lines_offset
        for i in range(lines_offset):
            line = f.readline()
            if not line:
                break
            byte_offset += len(line.encode("utf-8"))
            total_lines += 1

        # Read lines_limit lines
        count = 0
        while True:
            line = f.readline()
            if not line:
                break
            total_lines += 1
            if lines_limit is None or count < lines_limit:
                lines.append(line)
                count += 1

        # Count remaining lines
        has_more = False
        if lines_limit is not None and count >= lines_limit:
            remaining = f.readline()
            if remaining:
                has_more = True
                total_lines += 1
                # Count rest
                for _ in f:
                    total_lines += 1

    content = "".join(lines)

    # Total lines for small files
    if file_size >= 10 * 1024 * 1024:
        total_lines_result = None
    else:
        total_lines_result = total_lines

    return {
        "content": content,
        "byte_offset": byte_offset,
        "has_more": has_more,
        "total_lines": total_lines_result,
    }


@download_router.get("/download-zip")
async def download_directory_zip(
    path: str = Query(default="", description="Relative path of directory to zip"),
    task_id: str | None = None,
    user: str = Depends(get_download_user),
):
    """Download an arbitrary directory as a ZIP archive.

    Args:
        path: Relative path within workspace (empty string = workspace root).
        task_id: Optional task ID to scope to task workspace.
    """
    base_dir = _resolve_workspace(task_id)
    base_dir.mkdir(parents=True, exist_ok=True)

    target_dir = _validate_path_within_workspace(base_dir, path) if path else base_dir
    if not target_dir.exists():
        raise HTTPException(status_code=404, detail="Directory not found")
    if not target_dir.is_dir():
        raise HTTPException(status_code=400, detail="Path is not a directory")

    # Build ZIP in memory
    zip_buffer = io.BytesIO()
    file_count = 0
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, _dirs, files in os.walk(target_dir):
            for f in files:
                if f.startswith("."):
                    continue
                full_path = Path(root) / f
                rel_path = full_path.relative_to(target_dir)
                zf.write(full_path, arcname=str(rel_path))
                file_count += 1

    if file_count == 0:
        raise HTTPException(status_code=404, detail="No files found in directory")

    zip_buffer.seek(0)
    dir_name = Path(path).name if path else "workspace"
    filename = f"{dir_name}.zip"

    return StreamingResponse(
        zip_buffer,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@download_router.get("/download/{path:path}")
async def download_artifact(path: str, user: str = Depends(get_download_user)):
    """Download artifact file (supports DOCX, PDF, images, etc.)."""
    full_path = WORKSPACE_DIR / path
    if not full_path.exists():
        raise HTTPException(status_code=404, detail="Artifact not found")
    if not str(full_path.resolve()).startswith(str(WORKSPACE_DIR.resolve())):
        raise HTTPException(status_code=403, detail="Access denied")

    media_type = _guess_mime_type(full_path.name)
    return FileResponse(
        full_path,
        filename=full_path.name,
        media_type=media_type,
    )


def _guess_mime_type(filename: str) -> str:
    """Get MIME type for proper browser download handling."""
    ext = Path(filename).suffix.lower()
    mime_map = {
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        ".pdf": "application/pdf",
        ".md": "text/markdown",
        ".txt": "text/plain",
        ".json": "application/json",
        ".csv": "text/csv",
        ".html": "text/html",
        ".py": "text/x-python",
        ".js": "application/javascript",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".svg": "image/svg+xml",
        ".mp3": "audio/mpeg",
        ".wav": "audio/wav",
        ".zip": "application/zip",
    }
    return mime_map.get(ext, "application/octet-stream")


# ============================================================
# Task-scoped artifact endpoints
# ============================================================


@router.get("/task/{task_id}")
async def list_task_artifacts(task_id: str) -> list[dict[str, Any]]:
    """List files belonging to a specific task workspace."""
    task_dir = _resolve_workspace(task_id)
    if not task_dir.exists():
        return []

    artifacts = []
    for root, _dirs, files in os.walk(task_dir):
        for f in files:
            # Skip hidden meta files from listing
            if f.startswith("."):
                continue
            full_path = Path(root) / f
            rel_path = full_path.relative_to(task_dir)
            file_type = _guess_type(f)
            is_binary = _is_binary_file(f)
            artifacts.append({
                "path": str(rel_path),
                "name": f,
                "size": full_path.stat().st_size,
                "modified": full_path.stat().st_mtime,
                "type": file_type,
                "is_binary": is_binary,
                "task_id": task_id[:8],
                "download_url": f"/api/v1/artifacts/task/{task_id[:8]}/download/{rel_path}",
            })

    return sorted(artifacts, key=lambda x: x["modified"], reverse=True)


@router.get("/task/{task_id}/read/{path:path}")
async def read_task_artifact(task_id: str, path: str) -> dict[str, Any]:
    """Read a file from task workspace."""
    task_dir = _resolve_workspace(task_id)
    full_path = task_dir / path
    if not full_path.exists():
        raise HTTPException(status_code=404, detail="Artifact not found")
    if not str(full_path.resolve()).startswith(str(WORKSPACE_DIR.resolve())):
        raise HTTPException(status_code=403, detail="Access denied")

    is_binary = _is_binary_file(full_path.name)
    if is_binary:
        return {
            "path": path,
            "content": None,
            "size": full_path.stat().st_size,
            "type": _guess_type(path),
            "is_binary": True,
            "download_url": f"/api/v1/artifacts/task/{task_id[:8]}/download/{path}",
            "message": f"Binary file ({_guess_mime_type(full_path.name)}). Use download to view.",
        }

    try:
        content = full_path.read_text(encoding="utf-8", errors="replace")
        return {
            "path": path,
            "content": content,
            "size": len(content),
            "type": _guess_type(path),
            "is_binary": False,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/task/{task_id}/download/{path:path}")
async def download_task_artifact(task_id: str, path: str):
    """Download a file from task workspace."""
    task_dir = _resolve_workspace(task_id)
    full_path = task_dir / path
    if not full_path.exists():
        raise HTTPException(status_code=404, detail="Artifact not found")
    if not str(full_path.resolve()).startswith(str(WORKSPACE_DIR.resolve())):
        raise HTTPException(status_code=403, detail="Access denied")

    media_type = _guess_mime_type(full_path.name)
    return FileResponse(full_path, filename=full_path.name, media_type=media_type)


@download_router.get("/task/{task_id}/download-zip")
async def download_task_zip(task_id: str, user: str = Depends(get_download_user)):
    """Download all files in a task workspace as a single ZIP archive.

    Registered on ``download_router`` (not the JWT-protected router) so that
    browser-initiated downloads (``<a href download>``, which cannot set an
    Authorization header) can authenticate via the ``?token=`` query parameter
    through :func:`get_download_user`.
    """
    task_dir = _resolve_workspace(task_id)
    if not task_dir.exists():
        raise HTTPException(status_code=404, detail="Task not found")
    if not str(task_dir.resolve()).startswith(str(WORKSPACE_DIR.resolve())):
        raise HTTPException(status_code=403, detail="Access denied")

    # Build ZIP in memory
    zip_buffer = io.BytesIO()
    file_count = 0
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, _dirs, files in os.walk(task_dir):
            for f in files:
                # Skip hidden meta files
                if f.startswith("."):
                    continue
                full_path = Path(root) / f
                rel_path = full_path.relative_to(task_dir)
                zf.write(full_path, arcname=str(rel_path))
                file_count += 1

    if file_count == 0:
        raise HTTPException(status_code=404, detail="No files found in task")

    zip_buffer.seek(0)
    filename = f"task-{task_id[:8]}.zip"

    return StreamingResponse(
        zip_buffer,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@download_router.get("/session/{session_id}/download-zip")
async def download_session_zip(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    user: str = Depends(get_download_user),
):
    """Download all files from ALL tasks in a session as a single ZIP archive.

    Aggregates files from every task belonging to this session.
    Each task's files are placed under a subfolder named by task_id.

    Registered on ``download_router`` so browser ``<a href download>`` requests
    can authenticate via the ``?token=`` query parameter (they cannot send an
    Authorization header), matching the other download endpoints.
    """
    WORKSPACE_DIR.mkdir(exist_ok=True)
    tasks_dir = WORKSPACE_DIR / "tasks"

    if not tasks_dir.exists():
        raise HTTPException(status_code=404, detail="No tasks found")

    # Find task dirs belonging to this session via .task_meta.json
    matching_task_dirs: list[tuple[str, Path]] = []

    for task_dir in tasks_dir.iterdir():
        if not task_dir.is_dir():
            continue
        meta = _read_task_meta(task_dir)
        if meta and meta.get("session_id") == session_id:
            matching_task_dirs.append((task_dir.name, task_dir))

    # Fallback: DB lookup if no meta files found
    if not matching_task_dirs:
        try:
            result = await db.execute(
                select(TaskModel.id)
                .where(TaskModel.session_id == session_id)
                .order_by(TaskModel.created_at.asc())
            )
            task_rows = result.scalars().all()
            from app.core import task_paths
            for tid in task_rows:
                short_id = str(tid)[:8]
                task_dir = task_paths.find_task_dir(str(tid))
                if task_dir and task_dir.exists():
                    matching_task_dirs.append((short_id, task_dir))
        except Exception:
            pass

    if not matching_task_dirs:
        raise HTTPException(status_code=404, detail="No tasks found for this session")

    # Build ZIP in memory
    zip_buffer = io.BytesIO()
    file_count = 0
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for task_id, task_dir in matching_task_dirs:
            for root, _dirs, files in os.walk(task_dir):
                for f in files:
                    if f.startswith("."):
                        continue
                    full_path = Path(root) / f
                    rel_path = full_path.relative_to(task_dir)
                    # Place under task subfolder to avoid name collisions
                    arcname = f"{task_id}/{rel_path}" if len(matching_task_dirs) > 1 else str(rel_path)
                    zf.write(full_path, arcname=arcname)
                    file_count += 1

    if file_count == 0:
        raise HTTPException(status_code=404, detail="No files found in session")

    zip_buffer.seek(0)
    filename = f"session-{session_id[:8]}.zip"

    return StreamingResponse(
        zip_buffer,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ============================================================
# Session-scoped artifact endpoint
# ============================================================


@router.get("/session/{session_id}")
async def list_session_artifacts(
    session_id: str,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """List all artifacts for a specific session (conversation).

    Finds all tasks belonging to this session and aggregates their files.
    """
    WORKSPACE_DIR.mkdir(exist_ok=True)
    tasks_dir = WORKSPACE_DIR / "tasks"

    if not tasks_dir.exists():
        return {"session_id": session_id, "tasks": [], "total_files": 0}

    # Find task dirs belonging to this session via .task_meta.json
    matching_tasks: list[dict[str, Any]] = []

    for task_dir in tasks_dir.iterdir():
        if not task_dir.is_dir():
            continue
        meta = _read_task_meta(task_dir)
        if meta and meta.get("session_id") == session_id:
            task_files = _scan_task_dir(task_dir, task_dir.name)
            if task_files:
                matching_tasks.append({
                    "task_id": task_dir.name,
                    "task_title": meta.get("title", f"Task #{task_dir.name}"),
                    "files": task_files,
                    "file_count": len(task_files),
                })

    # Fallback: if no meta files found, try DB lookup
    if not matching_tasks:
        try:
            result = await db.execute(
                select(TaskModel.id)
                .where(TaskModel.session_id == session_id)
                .order_by(TaskModel.created_at.asc())
            )
            task_rows = result.scalars().all()
            from app.core import task_paths
            for tid in task_rows:
                short_id = str(tid)[:8]
                task_dir = task_paths.find_task_dir(str(tid))
                if task_dir and task_dir.exists():
                    task_files = _scan_task_dir(task_dir, short_id)
                    if task_files:
                        matching_tasks.append({
                            "task_id": short_id,
                            "task_title": f"Task #{short_id}",
                            "files": task_files,
                            "file_count": len(task_files),
                        })
        except Exception:
            pass

    total_files = sum(t["file_count"] for t in matching_tasks)
    return {
        "session_id": session_id,
        "tasks": matching_tasks,
        "total_files": total_files,
    }


# ============================================================
# Grouped view — all tasks grouped by SESSION
# ============================================================


@router.get("/grouped")
async def list_artifacts_grouped(db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    """List all artifacts grouped by session (conversation).

    Returns:
        {
            "sessions": [
                {
                    "session_id": "abc-123-...",
                    "session_title": "Research CVE-2026-23918",
                    "task_count": 2,
                    "file_count": 8,
                    "total_size": 110500,
                    "latest_modified": 1715133000.0,
                    "tasks": [
                        {
                            "task_id": "f407230c",
                            "task_title": "Analyze CVE details",
                            "context_docs": [...],
                            "artifacts": [...]
                        }
                    ]
                },
                ...
            ],
            "orphan_tasks": [...],   // tasks without session_id
            "global": [...],         // files not in any task
            "total_files": 42
        }
    """
    WORKSPACE_DIR.mkdir(exist_ok=True)
    tasks_dir = WORKSPACE_DIR / "tasks"

    # Intermediate: task_id → {meta, files}
    task_data: list[dict[str, Any]] = []

    # 1. Scan all task directories
    if tasks_dir.exists():
        for task_dir in sorted(tasks_dir.iterdir(), reverse=True):
            if not task_dir.is_dir():
                continue
            task_id = task_dir.name

            # Read .task_meta.json for title + session_id resolution
            meta = _read_task_meta(task_dir)

            # Scan files
            files = _scan_task_dir(task_dir, task_id)
            if not files:
                continue

            # Separate context docs from artifacts
            context_docs = [f for f in files if f.get("is_context_doc")]
            artifacts = [f for f in files if not f.get("is_context_doc")]

            total_size = sum(f["size"] for f in files)
            latest_mod = max((f["modified"] for f in files), default=0.0)

            task_entry = {
                "task_id": task_id,
                "task_title": meta.get("title", "") if meta else "",
                "session_id": meta.get("session_id", "") if meta else "",
                "full_task_id": meta.get("task_id", "") if meta else "",
                "file_count": len(files),
                "total_size": total_size,
                "latest_modified": latest_mod,
                "context_docs": context_docs,
                "artifacts": artifacts,
                "files": files,
            }
            task_data.append(task_entry)

    # 2. For tasks without meta, try DB prefix matching
    _tasks_needing_db = [t for t in task_data if not t["session_id"]]
    if _tasks_needing_db:
        await _resolve_task_info_from_db(db, _tasks_needing_db)

    # 3. Group tasks by session_id
    session_map: dict[str, list[dict[str, Any]]] = {}  # session_id → [task_entries]
    orphan_tasks: list[dict[str, Any]] = []

    for td in task_data:
        sid = td.get("session_id", "")
        if sid:
            session_map.setdefault(sid, []).append(td)
        else:
            orphan_tasks.append(td)

    # 4. Build session groups with conversation titles
    session_groups: list[dict[str, Any]] = []
    session_ids = list(session_map.keys())

    # Batch lookup session titles from DB
    session_title_map = await _get_session_titles(db, session_ids)

    for sid, tasks in session_map.items():
        # Sort tasks within session by latest_modified (newest first)
        tasks.sort(key=lambda t: t["latest_modified"], reverse=True)

        # Session-level aggregation
        total_files = sum(t["file_count"] for t in tasks)
        total_size = sum(t["total_size"] for t in tasks)
        latest_mod = max((t["latest_modified"] for t in tasks), default=0.0)

        # Session title: from DB title → from first task's user message → fallback
        session_title = session_title_map.get(sid, "")
        if not session_title:
            # Use first task's title (user message) as session title
            session_title = tasks[0].get("task_title", "") if tasks else ""
        if not session_title:
            session_title = f"Session {sid[:8]}..."

        session_groups.append({
            "session_id": sid,
            "session_title": session_title,
            "task_count": len(tasks),
            "file_count": total_files,
            "total_size": total_size,
            "latest_modified": latest_mod,
            "tasks": [
                {
                    "task_id": t["task_id"],
                    "task_title": t["task_title"] or f"Task #{t['task_id']}",
                    "file_count": t["file_count"],
                    "total_size": t["total_size"],
                    "latest_modified": t["latest_modified"],
                    "context_docs": t["context_docs"],
                    "artifacts": t["artifacts"],
                    "files": t["files"],
                }
                for t in tasks
            ],
        })

    # Sort sessions by latest_modified (newest first)
    session_groups.sort(key=lambda s: s["latest_modified"], reverse=True)

    # 5. Collect global (non-task) files
    global_files: list[dict[str, Any]] = []
    skip_dirs = {"tasks", ".agent_memories", ".agent_memory", ".user_profiles", ".screenshots"}
    for item in WORKSPACE_DIR.iterdir():
        if item.is_dir() and item.name in skip_dirs:
            continue
        if item.is_file():
            rel_path = item.relative_to(WORKSPACE_DIR)
            file_type = _guess_type(item.name)
            is_binary = _is_binary_file(item.name)
            global_files.append({
                "path": str(rel_path),
                "name": item.name,
                "size": item.stat().st_size,
                "modified": item.stat().st_mtime,
                "type": file_type,
                "is_binary": is_binary,
                "download_url": f"/api/v1/artifacts/download/{rel_path}",
            })

    global_files.sort(key=lambda x: x["modified"], reverse=True)

    total_files = sum(s["file_count"] for s in session_groups) + \
        sum(t["file_count"] for t in orphan_tasks) + len(global_files)

    return {
        "sessions": session_groups,
        "orphan_tasks": [
            {
                "task_id": t["task_id"],
                "task_title": t["task_title"] or f"Task #{t['task_id']}",
                "file_count": t["file_count"],
                "total_size": t["total_size"],
                "latest_modified": t["latest_modified"],
                "context_docs": t["context_docs"],
                "artifacts": t["artifacts"],
                "files": t["files"],
            }
            for t in orphan_tasks
        ],
        "global": global_files,
        "total_files": total_files,
    }


# ============================================================
# Helpers
# ============================================================


def _read_task_meta(task_dir: Path) -> dict[str, Any] | None:
    """Read .task_meta.json from a task directory.

    Returns None if file doesn't exist or can't be parsed.
    """
    meta_path = task_dir / ".task_meta.json"
    if not meta_path.exists():
        return None
    try:
        return json.loads(meta_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _scan_task_dir(task_dir: Path, task_id: str) -> list[dict[str, Any]]:
    """Scan a task directory and return all visible files with metadata."""
    files: list[dict[str, Any]] = []

    for root, _dirs, filenames in os.walk(task_dir):
        for f in filenames:
            # Skip hidden files (.task_meta.json, .gitkeep, etc.)
            if f.startswith("."):
                continue
            full_path = Path(root) / f
            rel_path = full_path.relative_to(task_dir)
            stat = full_path.stat()
            file_type = _guess_type(f)
            is_binary = _is_binary_file(f)
            is_context = str(rel_path).startswith("_context")

            files.append({
                "path": str(rel_path),
                "name": f,
                "size": stat.st_size,
                "modified": stat.st_mtime,
                "type": file_type,
                "is_binary": is_binary,
                "is_context_doc": is_context,
                "task_id": task_id,
                "download_url": f"/api/v1/artifacts/task/{task_id}/download/{rel_path}",
            })

    files.sort(key=lambda x: x["modified"], reverse=True)
    return files


async def _resolve_task_info_from_db(
    db: AsyncSession,
    tasks: list[dict[str, Any]],
) -> None:
    """Resolve title and session_id from DB for tasks without .task_meta.json.

    Uses prefix matching: TaskModel.id::text LIKE 'prefix%'
    Falls back to TASK_BRIEF.md parsing as last resort.
    """
    for task_entry in tasks:
        prefix = task_entry["task_id"]

        # Try DB prefix match
        try:
            result = await db.execute(
                select(TaskModel.id, TaskModel.title, TaskModel.session_id)
                .where(cast(TaskModel.id, String).like(f"{prefix}%"))
                .limit(1)
            )
            row = result.first()
            if row:
                task_entry["task_title"] = row.title or ""
                task_entry["session_id"] = row.session_id or ""
                task_entry["full_task_id"] = str(row.id)
                continue
        except Exception:
            pass

        # Last resort: parse TASK_BRIEF.md for title
        if not task_entry.get("task_title"):
            task_entry["task_title"] = _extract_title_from_brief(prefix)


def _extract_title_from_brief(task_id: str) -> str:
    """Extract a meaningful title from TASK_BRIEF.md (last resort fallback).

    Improved parsing: skip known headers, metadata, and generic text.
    """
    from app.core import task_paths
    brief_path = (task_paths.find_task_dir(task_id) or task_paths.task_dir(task_id)) / "_context" / "TASK_BRIEF.md"
    if not brief_path.exists():
        return f"Task #{task_id}"

    try:
        content = brief_path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return f"Task #{task_id}"

    # Skip lines that are clearly NOT a title
    _skip_patterns = {
        "task brief", "description", "goals", "constraints",
        "success criteria", "analysis dimensions", "tool plan",
        "prior knowledge", "previous task context",
    }

    # Strategy 1: Find "## Description" section, take first content line
    in_description = False
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.lower().startswith("## description"):
            in_description = True
            continue
        if in_description and stripped and not stripped.startswith("##"):
            # Skip lines that look like metadata or generic labels
            lower = stripped.lower().lstrip("#").strip()
            if lower in _skip_patterns:
                continue
            if stripped.startswith("**") and ":" in stripped:
                continue
            # Skip lines containing only "PRIOR KNOWLEDGE" type text
            if "prior knowledge" in lower or "from past tasks" in lower:
                continue
            return stripped[:80]
        if in_description and stripped.startswith("##"):
            break

    # Strategy 2: Scan for first meaningful content line
    for line in content.splitlines():
        stripped = line.strip().lstrip("#").strip()
        if not stripped or stripped.startswith("---"):
            continue
        lower = stripped.lower()
        if lower in _skip_patterns:
            continue
        if stripped.startswith("**") and ":" in stripped:
            continue
        if "prior knowledge" in lower or "from past tasks" in lower:
            continue
        return stripped[:80]

    return f"Task #{task_id}"


async def _get_session_titles(
    db: AsyncSession,
    session_ids: list[str],
) -> dict[str, str]:
    """Batch lookup conversation titles for session_ids.

    Checks ConversationMessageModel for 'title' role messages first,
    then falls back to first user message.
    """
    if not session_ids:
        return {}

    title_map: dict[str, str] = {}

    try:
        # First: look for explicit title messages (role='title')
        result = await db.execute(
            select(
                ConversationMessageModel.session_id,
                ConversationMessageModel.content,
            )
            .where(
                ConversationMessageModel.session_id.in_(session_ids),
                ConversationMessageModel.role == "title",
            )
        )
        for row in result.all():
            if row.content and row.content.strip():
                title_map[row.session_id] = row.content.strip()[:80]

        # For sessions without title, get first user message
        missing = [s for s in session_ids if s not in title_map]
        if missing:
            for sid in missing:
                result2 = await db.execute(
                    select(ConversationMessageModel.content)
                    .where(
                        ConversationMessageModel.session_id == sid,
                        ConversationMessageModel.role == "user",
                    )
                    .order_by(ConversationMessageModel.created_at.asc())
                    .limit(1)
                )
                row2 = result2.scalar_one_or_none()
                if row2:
                    title_map[sid] = row2.strip()[:80]
    except Exception as e:
        logger.debug("session_title_lookup_failed", error=str(e))

    return title_map


def _guess_type(filename: str) -> str:
    ext = Path(filename).suffix.lower()
    type_map = {
        ".md": "markdown", ".txt": "text", ".json": "json",
        ".py": "python", ".js": "javascript", ".ts": "typescript",
        ".html": "html", ".css": "css", ".csv": "csv",
        ".yaml": "yaml", ".yml": "yaml", ".sh": "bash",
        ".docx": "docx", ".xlsx": "excel", ".pdf": "pdf",
        ".png": "image", ".jpg": "image", ".jpeg": "image",
        ".mp3": "audio", ".wav": "audio",
    }
    return type_map.get(ext, "file")
