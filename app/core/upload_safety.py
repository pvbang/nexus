"""Hardening helpers for handling uploaded / staged files.

Defends file-handling paths against two classic attacks:

- **Path traversal**: a crafted filename (``../../etc/passwd``) escaping the
  intended base directory.
- **Symlink escape / TOCTOU**: a staged entry that is actually a symlink to a
  sensitive file outside the upload tree, so a naive ``shutil.copy2`` would read
  the link target.

``normalize_filename`` reduces a name to its basename and rejects traversal.
``safe_copy_no_symlink`` refuses to copy when the source is a symlink and
verifies the destination stays within its base.

Ported/adapted from deer-flow ``uploads/manager.py``.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import structlog

logger = structlog.get_logger(__name__)


class PathTraversalError(Exception):
    """Raised when a path escapes its intended base directory or is a symlink."""


def normalize_filename(filename: str) -> str:
    """Strip directory components; reject empty / dot-only / traversal names."""
    name = os.path.basename(str(filename).replace("\\", "/").rstrip("/"))
    if not name or name in (".", ".."):
        raise ValueError(f"Unsafe filename: {filename!r}")
    return name


def is_within_base(path: Path, base: Path) -> bool:
    """True if ``path`` resolves to a location inside ``base``."""
    try:
        Path(path).resolve().relative_to(Path(base).resolve())
        return True
    except ValueError:
        return False


def validate_path_traversal(path: Path, base: Path) -> None:
    """Raise PathTraversalError if ``path`` escapes ``base``."""
    if not is_within_base(path, base):
        raise PathTraversalError(f"Path traversal detected: {path}")


def safe_copy_no_symlink(src: Path, dest: Path, *, base: Path | None = None) -> Path:
    """Copy ``src`` → ``dest``, refusing symlinked sources and dest escapes.

    - If ``src`` is a symlink, raise PathTraversalError (don't follow it).
    - If ``base`` is given, ``dest`` must resolve inside it.
    - Copies file bytes + metadata (like copy2) but never through a link.
    """
    src = Path(src)
    dest = Path(dest)

    # Reject symlinked source (read its real target would be an escape).
    try:
        if os.path.islink(src):
            raise PathTraversalError(f"Refusing to copy symlink source: {src}")
    except OSError as exc:
        raise PathTraversalError(f"Cannot stat source: {src}") from exc

    if base is not None:
        validate_path_traversal(dest, base)

    dest.parent.mkdir(parents=True, exist_ok=True)
    # follow_symlinks=False on the copy guards against the source mutating into
    # a link between the check above and the copy (narrows the TOCTOU window).
    shutil.copy2(src, dest, follow_symlinks=False)
    return dest
