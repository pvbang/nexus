"""
MekongAI Nexus - Atomic File Writes (Hermes-agent inspired).

Crash-safe file operations using the write-temp-fsync-replace pattern.
Prevents data corruption from partial writes during crashes, power loss,
or unexpected process termination.

Algorithm:
  1. Write content to a temporary file in the SAME directory as target
  2. ``os.fsync()`` the file descriptor (flush OS buffers to disk)
  3. ``os.replace()`` temp → target (atomic on POSIX, near-atomic on Windows)
  4. ``os.fsync()`` the parent directory (ensure rename is durable)

Inspired by: hermes-agent/utils.py atomic_write_text()
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


def atomic_write_text(
    path: str | Path,
    content: str,
    encoding: str = "utf-8",
    mkdir: bool = True,
) -> None:
    """Write text to a file atomically.

    The file is guaranteed to contain either the old content or the new
    content — never a partial/corrupt mix.

    Parameters
    ----------
    path:
        Target file path.
    content:
        Text content to write.
    encoding:
        Text encoding (default: utf-8).
    mkdir:
        Create parent directories if they don't exist.
    """
    target = Path(path)

    if mkdir:
        target.parent.mkdir(parents=True, exist_ok=True)

    _atomic_write_impl(target, content.encode(encoding))
    logger.debug("atomic_write_text", path=str(target), size=len(content))


def atomic_write_bytes(
    path: str | Path,
    data: bytes,
    mkdir: bool = True,
) -> None:
    """Write bytes to a file atomically.

    Parameters
    ----------
    path:
        Target file path.
    data:
        Raw bytes to write.
    mkdir:
        Create parent directories if they don't exist.
    """
    target = Path(path)

    if mkdir:
        target.parent.mkdir(parents=True, exist_ok=True)

    _atomic_write_impl(target, data)
    logger.debug("atomic_write_bytes", path=str(target), size=len(data))


def atomic_write_json(
    path: str | Path,
    obj: Any,
    indent: int = 2,
    ensure_ascii: bool = False,
    mkdir: bool = True,
) -> None:
    """Serialize an object to JSON and write it atomically.

    Parameters
    ----------
    path:
        Target file path.
    obj:
        Python object to serialize (must be JSON-serializable).
    indent:
        JSON indentation (default: 2 spaces).
    ensure_ascii:
        If False, allow non-ASCII chars in output.
    mkdir:
        Create parent directories if they don't exist.
    """
    content = json.dumps(obj, indent=indent, ensure_ascii=ensure_ascii)
    atomic_write_text(path, content + "\n", mkdir=mkdir)


def _atomic_write_impl(target: Path, data: bytes) -> None:
    """Core atomic write: temp → fsync → replace → fsync dir.

    Uses ``tempfile.NamedTemporaryFile`` in the same directory as the
    target to ensure the temp file is on the same filesystem (required
    for atomic ``os.replace``).
    """
    parent_dir = str(target.parent)

    # Create temp file in same directory as target
    fd = None
    tmp_path = None
    try:
        fd_handle = tempfile.NamedTemporaryFile(
            mode="wb",
            dir=parent_dir,
            prefix=".nexus_tmp_",
            suffix=".tmp",
            delete=False,
        )
        tmp_path = fd_handle.name
        fd = fd_handle.fileno()

        # Write content
        fd_handle.write(data)

        # Flush Python buffers
        fd_handle.flush()

        # Sync to disk (OS-level)
        os.fsync(fd)

        # Close before replace
        fd_handle.close()
        fd = None  # Mark as closed

        # Atomic replace (POSIX: atomic; Windows: near-atomic)
        os.replace(tmp_path, str(target))

        # Sync parent directory to ensure the rename is durable
        _fsync_dir(parent_dir)

    except Exception:
        # Clean up temp file on failure
        if fd is not None:
            try:
                fd_handle.close()
            except Exception:
                pass
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
        raise


def _fsync_dir(dir_path: str) -> None:
    """Fsync a directory to ensure metadata changes (renames) are durable.

    This is important on Linux ext4/xfs — without it, a crash after
    ``os.replace()`` could lose the rename.

    On Windows, directory fsync is not supported and is safely skipped.
    """
    if os.name == "nt":
        # Windows doesn't support fsync on directories
        return

    try:
        dir_fd = os.open(dir_path, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except OSError:
        # Non-critical: some filesystems don't support dir fsync
        pass


def safe_read_json(path: str | Path, default: Any = None) -> Any:
    """Read a JSON file safely, returning default on any error.

    Complement to :func:`atomic_write_json` — provides a safe read
    that won't crash on corrupt/missing files.

    Parameters
    ----------
    path:
        File path to read.
    default:
        Value to return if file doesn't exist or is corrupt.

    Returns
    -------
    Any:
        Parsed JSON content, or *default* on failure.
    """
    target = Path(path)
    if not target.exists():
        return default

    try:
        content = target.read_text(encoding="utf-8")
        return json.loads(content)
    except (json.JSONDecodeError, OSError, UnicodeDecodeError) as exc:
        logger.warning("safe_read_json_failed", path=str(target), error=str(exc))
        return default
