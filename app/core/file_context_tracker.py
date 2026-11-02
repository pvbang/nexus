"""
Nexus - File Context Tracker.

Tracks all file operations within a task to:
- Know what files exist in workspace
- Know what files agent has read/edited
- Detect stale context (file modified since last read)
- Provide workspace inventory for environment details
"""

import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional

import structlog

logger = structlog.get_logger(__name__)


class FileOperation(str, Enum):
    """Types of file operations tracked."""

    READ = "read"  # Agent read the file
    EDITED = "edited"  # Agent edited the file
    CREATED = "created"  # Agent created the file
    DELETED = "deleted"  # Agent deleted the file
    EXTERNAL_CHANGE = "external_change"  # File changed outside of agent


@dataclass
class FileRecord:
    """Metadata about a tracked file."""

    path: str  # Relative path from workspace
    last_read_at: Optional[float] = None  # Unix timestamp of last read
    last_edited_at: Optional[float] = None  # Unix timestamp of last edit
    last_external_change_at: Optional[float] = None  # Unix timestamp of external change
    created_at: Optional[float] = None  # When file was first tracked
    total_lines: Optional[int] = None  # Last known line count
    size_bytes: Optional[int] = None  # Last known size
    is_deleted: bool = False  # Whether file was deleted
    operations: list[tuple[float, FileOperation]] = field(default_factory=list)

    @property
    def is_stale(self) -> bool:
        """File is stale if it was modified externally after agent's last read."""
        if self.last_external_change_at is None:
            return False
        if self.last_read_at is None:
            return True
        return self.last_external_change_at > self.last_read_at


class FileContextTracker:
    """
    Track all file operations within a task/session.

    Usage:
        tracker = FileContextTracker(
            task_id="abc123",
            workspace_path=Path("/workspace/tasks/abc1"),
        )
        await tracker.track_read("report.md", total_lines=50, size_bytes=1200)
        await tracker.track_edit("report.md")

        # Get workspace state for environment details
        summary = tracker.get_workspace_summary()
        stale = tracker.get_stale_files()
    """

    def __init__(self, task_id: str, workspace_path: Path):
        self.task_id = task_id
        self.workspace_path = workspace_path
        self._files: dict[str, FileRecord] = {}  # path → FileRecord
        self._created_at = time.time()

    def _ensure_record(self, path: str) -> FileRecord:
        """Get or create a FileRecord for a path."""
        if path not in self._files:
            self._files[path] = FileRecord(path=path, created_at=time.time())
        return self._files[path]

    async def track_read(
        self,
        path: str,
        total_lines: Optional[int] = None,
        size_bytes: Optional[int] = None,
    ) -> None:
        """Record that agent read a file."""
        record = self._ensure_record(path)
        now = time.time()
        record.last_read_at = now
        record.operations.append((now, FileOperation.READ))
        if total_lines is not None:
            record.total_lines = total_lines
        if size_bytes is not None:
            record.size_bytes = size_bytes
        logger.debug("file_tracked_read", task_id=self.task_id, path=path)

    async def track_edit(
        self,
        path: str,
        total_lines: Optional[int] = None,
        size_bytes: Optional[int] = None,
    ) -> None:
        """Record that agent edited a file."""
        record = self._ensure_record(path)
        now = time.time()
        record.last_edited_at = now
        record.last_read_at = now  # Edit implies re-read
        record.operations.append((now, FileOperation.EDITED))
        if total_lines is not None:
            record.total_lines = total_lines
        if size_bytes is not None:
            record.size_bytes = size_bytes
        logger.debug("file_tracked_edit", task_id=self.task_id, path=path)

    async def track_create(self, path: str, size_bytes: Optional[int] = None) -> None:
        """Record that agent created a new file."""
        record = self._ensure_record(path)
        now = time.time()
        record.created_at = now
        record.last_edited_at = now
        record.last_read_at = now
        record.is_deleted = False
        record.operations.append((now, FileOperation.CREATED))
        if size_bytes is not None:
            record.size_bytes = size_bytes
        logger.debug("file_tracked_create", task_id=self.task_id, path=path)

    async def track_delete(self, path: str) -> None:
        """Record that a file was deleted."""
        record = self._ensure_record(path)
        now = time.time()
        record.is_deleted = True
        record.operations.append((now, FileOperation.DELETED))
        logger.debug("file_tracked_delete", task_id=self.task_id, path=path)

    async def track_external_change(self, path: str) -> None:
        """Record that a file was modified outside agent control."""
        record = self._ensure_record(path)
        now = time.time()
        record.last_external_change_at = now
        record.operations.append((now, FileOperation.EXTERNAL_CHANGE))
        logger.debug("file_tracked_external", task_id=self.task_id, path=path)

    def get_stale_files(self) -> list[str]:
        """Get list of files that changed since agent last read them."""
        return [
            path
            for path, record in self._files.items()
            if record.is_stale and not record.is_deleted
        ]

    def get_recently_edited(self, limit: int = 10) -> list[FileRecord]:
        """Get most recently edited files, sorted by last_edited_at descending."""
        edited = [
            r for r in self._files.values() if r.last_edited_at and not r.is_deleted
        ]
        edited.sort(key=lambda r: r.last_edited_at or 0, reverse=True)
        return edited[:limit]

    def get_recently_read(self, limit: int = 20) -> list[FileRecord]:
        """Get most recently read files."""
        read_files = [
            r for r in self._files.values() if r.last_read_at and not r.is_deleted
        ]
        read_files.sort(key=lambda r: r.last_read_at or 0, reverse=True)
        return read_files[:limit]

    def get_all_active_files(self) -> list[FileRecord]:
        """Get all non-deleted tracked files."""
        return [r for r in self._files.values() if not r.is_deleted]

    def get_workspace_summary(self) -> str:
        """Generate workspace file listing text for environment details injection."""
        active_files = self.get_all_active_files()
        if not active_files:
            return "(No files tracked in workspace yet)"

        # Sort by path for readability
        active_files.sort(key=lambda r: r.path)

        lines = []
        for record in active_files:
            parts = [f"  {record.path}"]
            meta_parts: list[str] = []
            if record.total_lines is not None:
                meta_parts.append(f"{record.total_lines} lines")
            if record.size_bytes is not None:
                if record.size_bytes < 1024:
                    meta_parts.append(f"{record.size_bytes}b")
                else:
                    meta_parts.append(f"{record.size_bytes // 1024}kb")
            if record.is_stale:
                meta_parts.append("⚠️ STALE")
            if meta_parts:
                parts.append(f" ({', '.join(meta_parts)})")
            lines.append("".join(parts))

        return "\n".join(lines)

    def get_stale_files_text(self) -> str:
        """Generate stale file warning text."""
        stale = self.get_stale_files()
        if not stale:
            return "(None)"
        return "\n".join(f"  ⚠️ {path} — re-read before editing!" for path in stale)

    def get_recently_edited_text(self) -> str:
        """Generate recently edited files text."""
        edited = self.get_recently_edited(5)
        if not edited:
            return "(None)"
        return "\n".join(f"  ✏️ {r.path}" for r in edited)

    @property
    def tracked_count(self) -> int:
        """Number of tracked files (including deleted)."""
        return len(self._files)

    @property
    def active_count(self) -> int:
        """Number of active (non-deleted) tracked files."""
        return len([r for r in self._files.values() if not r.is_deleted])
