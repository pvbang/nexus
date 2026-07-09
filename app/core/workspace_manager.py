"""
MekongAI Nexus - Workspace Manager.

Manages per-task workspace directories with:
- File naming conventions/policies
- Path validation (prevent traversal attacks)
- File inventory scanning
- Naming suggestions for agents
"""

import asyncio
import os
import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

import structlog

logger = structlog.get_logger(__name__)

# Root workspace directory
WORKSPACE_ROOT = Path(os.environ.get("WORKSPACE_DIR", "workspace_data"))


class FileLock:
    """Simple advisory file lock for multi-agent coordination.

    Prevents accidental overwrites when multiple agents write to the same file.
    Advisory only — does not block writes, but warns about conflicts.

    Uses threading.Lock internally to prevent race conditions when multiple
    async tasks (running on the same event loop thread or via thread pool)
    access lock state concurrently.
    """

    _locks: dict[str, str] = {}  # filepath -> agent_name
    _lock_times: dict[str, float] = {}  # filepath -> timestamp
    _mutex = threading.Lock()  # Protect concurrent access to shared state
    LOCK_TIMEOUT = 600.0  # 10 minutes - accounts for LLM call latency

    @classmethod
    def acquire_lock(cls, filepath: str, agent_name: str) -> bool:
        """Acquire advisory lock on a file.

        Args:
            filepath: Relative file path.
            agent_name: Name of the agent requesting the lock.

        Returns:
            True if lock acquired, False if held by another agent.
        """
        with cls._mutex:
            cls._force_release_expired_unlocked()

            current_holder = cls._locks.get(filepath)
            if current_holder is None or current_holder == agent_name:
                cls._locks[filepath] = agent_name
                cls._lock_times[filepath] = time.time()
                logger.info("file_lock_acquired", filepath=filepath, agent=agent_name)
                return True

            # Locked by another agent
            logger.debug(
                "file_lock_denied",
                filepath=filepath,
                requester=agent_name,
                holder=current_holder,
            )
            return False

    @classmethod
    def release_lock(cls, filepath: str, agent_name: str) -> None:
        """Release lock if held by the specified agent.

        Args:
            filepath: Relative file path.
            agent_name: Name of the agent releasing the lock.
        """
        with cls._mutex:
            current_holder = cls._locks.get(filepath)
            if current_holder == agent_name:
                del cls._locks[filepath]
                cls._lock_times.pop(filepath, None)
                logger.debug("file_lock_released", filepath=filepath, agent=agent_name)

    @classmethod
    def refresh_lock(cls, filepath: str, agent_name: str) -> bool:
        """Refresh lock timestamp if agent still holds it.

        Call this during long-running operations (e.g. awaiting LLM response)
        to prevent the lock from expiring while the agent is still active.

        Args:
            filepath: Relative file path.
            agent_name: Name of the agent refreshing the lock.

        Returns:
            True if lock was refreshed, False if agent doesn't hold the lock.
        """
        with cls._mutex:
            if cls._locks.get(filepath) == agent_name:
                cls._lock_times[filepath] = time.time()
                logger.debug("file_lock_refreshed", filepath=filepath, agent=agent_name)
                return True
            return False

    @classmethod
    def check_lock(cls, filepath: str) -> Optional[str]:
        """Check who holds the lock on a file.

        Args:
            filepath: Relative file path.

        Returns:
            Agent name if locked, None if free.
        """
        with cls._mutex:
            cls._force_release_expired_unlocked()
            return cls._locks.get(filepath)

    @classmethod
    def force_release_expired(cls) -> None:
        """Release all locks older than LOCK_TIMEOUT."""
        with cls._mutex:
            cls._force_release_expired_unlocked()

    @classmethod
    def _force_release_expired_unlocked(cls) -> None:
        """Release expired locks. Must be called with _mutex held."""
        now = time.time()
        expired = [
            fp for fp, ts in cls._lock_times.items()
            if (now - ts) > cls.LOCK_TIMEOUT
        ]
        for fp in expired:
            agent = cls._locks.pop(fp, None)
            cls._lock_times.pop(fp, None)
            if agent:
                logger.debug("file_lock_expired", filepath=fp, agent=agent)


@dataclass
class FileInfo:
    """Information about a file in workspace."""

    path: str  # Relative to workspace
    name: str  # Filename only
    size_bytes: int
    total_lines: Optional[int]  # None for binary files
    is_dir: bool
    modified_at: float  # Unix timestamp


@dataclass
class PathValidation:
    """Result of path validation."""

    valid: bool
    resolved_path: Optional[Path] = None
    error: Optional[str] = None


class WorkspaceManager:
    """
    Manages per-task workspace with naming conventions and inventory.

    Usage:
        wm = WorkspaceManager(task_id="abc12345")
        inventory = await wm.get_file_inventory()
        suggestion = wm.suggest_filename("report", "market analysis")
        validation = wm.validate_path("../../etc/passwd")  # → invalid!
    """

    # Naming convention templates
    NAMING_CONVENTIONS: dict[str, str] = {
        "report": "{topic}_{date}.md",
        "analysis": "analysis_{subject}.md",
        "summary": "summary_{topic}.md",
        "code": "{name}.{ext}",
        "data": "data/{name}.{ext}",
        "config": "config/{name}.yaml",
        "notes": "notes/{topic}.md",
        "output": "output/{name}.{ext}",
    }

    def __init__(self, task_id: str, workspace_path: Optional[Path] = None):
        self.task_id = task_id
        if workspace_path:
            self.workspace_path = workspace_path
        else:
            # Default: resolver-aware task dir (nests subtasks).
            from app.core import task_paths
            self.workspace_path = task_paths.task_dir(task_id)
        self.workspace_path.mkdir(parents=True, exist_ok=True)

    def validate_path(self, relative_path: str) -> PathValidation:
        """
        Validate that a path is within workspace boundaries.

        Catches:
        - Path traversal (../)
        - Absolute paths
        - Symlink escapes
        """
        if not relative_path:
            return PathValidation(valid=False, error="Empty path")

        # Reject absolute paths
        if os.path.isabs(relative_path):
            return PathValidation(valid=False, error="Absolute paths not allowed")

        # Resolve the path
        try:
            resolved = (self.workspace_path / relative_path).resolve()
        except (ValueError, OSError) as e:
            return PathValidation(valid=False, error=f"Invalid path: {e}")

        # Check it's within workspace
        try:
            resolved.relative_to(self.workspace_path.resolve())
        except ValueError:
            return PathValidation(
                valid=False, error="Path traversal detected: outside workspace"
            )

        # Also check against WORKSPACE_ROOT (global safety net)
        try:
            resolved.relative_to(WORKSPACE_ROOT.resolve())
        except ValueError:
            return PathValidation(valid=False, error="Path outside workspace root")

        return PathValidation(valid=True, resolved_path=resolved)

    def suggest_filename(self, content_type: str, topic: str, ext: str = "md") -> str:
        """
        Suggest a proper filename based on content type and topic.

        Args:
            content_type: Type of content (report, analysis, code, data, etc.)
            topic: Brief topic description
            ext: File extension (default: md)

        Returns:
            Suggested filename string
        """
        # Clean topic for filename use
        clean_topic = self._sanitize_for_filename(topic)
        date_str = datetime.now().strftime("%Y%m%d")

        template = self.NAMING_CONVENTIONS.get(content_type, "{name}.{ext}")

        try:
            return template.format(
                topic=clean_topic,
                subject=clean_topic,
                name=clean_topic,
                date=date_str,
                ext=ext,
            )
        except (KeyError, IndexError):
            return f"{clean_topic}.{ext}"

    async def get_file_inventory(self) -> list[FileInfo]:
        """
        Scan workspace and return all files with metadata.
        Uses rglob for recursive scanning with exclusions.
        """

        def _scan() -> list[FileInfo]:
            results: list[FileInfo] = []
            if not self.workspace_path.exists():
                return results

            for item in sorted(self.workspace_path.rglob("*")):
                # Skip hidden files and __pycache__
                if any(
                    part.startswith(".") or part == "__pycache__"
                    for part in item.parts
                ):
                    continue

                try:
                    stat = item.stat()
                    rel_path = str(item.relative_to(self.workspace_path))

                    total_lines = None
                    if item.is_file():
                        try:
                            content = item.read_text(
                                encoding="utf-8", errors="replace"
                            )
                            total_lines = content.count("\n") + (
                                1 if content and not content.endswith("\n") else 0
                            )
                        except (UnicodeDecodeError, PermissionError):
                            total_lines = None  # Binary or unreadable

                    results.append(
                        FileInfo(
                            path=rel_path,
                            name=item.name,
                            size_bytes=stat.st_size if item.is_file() else 0,
                            total_lines=total_lines,
                            is_dir=item.is_dir(),
                            modified_at=stat.st_mtime,
                        )
                    )
                except (OSError, PermissionError):
                    continue

            return results

        return await asyncio.to_thread(_scan)

    async def get_inventory_text(self) -> str:
        """Generate human-readable inventory for environment details."""
        files = await self.get_file_inventory()
        if not files:
            return "(Workspace is empty)"

        lines: list[str] = []
        for f in files:
            icon = "📁" if f.is_dir else "📄"
            parts = [f"  {icon} {f.path}"]
            if not f.is_dir:
                meta: list[str] = []
                if f.total_lines is not None:
                    meta.append(f"{f.total_lines} lines")
                if f.size_bytes < 1024:
                    meta.append(f"{f.size_bytes}b")
                else:
                    meta.append(f"{f.size_bytes // 1024}kb")
                if meta:
                    parts.append(f" ({', '.join(meta)})")
            lines.append("".join(parts))

        return "\n".join(lines)

    async def get_all_task_files(self, task_id: str) -> list[str]:
        """
        Return danh sách tất cả files thuộc task (relative paths).

        Scans the task workspace directory recursively, excluding
        hidden files, __pycache__, node_modules, .git, .venv.

        Args:
            task_id: The task ID (uses first 8 chars for directory lookup).

        Returns:
            List of relative file paths within the task workspace.
        """
        from app.core import task_paths
        task_path = task_paths.find_task_dir(task_id) or task_paths.task_dir(task_id)
        if not task_path.exists():
            return []

        def _scan_files() -> list[str]:
            results: list[str] = []
            _exclude_dirs = {"__pycache__", "node_modules", ".git", ".venv"}
            for item in sorted(task_path.rglob("*")):
                if item.is_dir():
                    continue
                # Skip hidden files and excluded directories
                if any(
                    part.startswith(".") or part in _exclude_dirs
                    for part in item.relative_to(task_path).parts
                ):
                    continue
                try:
                    rel_path = str(item.relative_to(task_path))
                    results.append(rel_path)
                except (ValueError, OSError):
                    continue
            return results

        return await asyncio.to_thread(_scan_files)

    async def ensure_consistency(self) -> dict[str, list[str]]:
        """
        Verify workspace state matches expected state.

        Checks:
        - All files are readable (not corrupted)
        - No empty files that should have content (size > 0 for non-__init__)
        - No broken symlinks
        - Logs inconsistencies

        Returns:
            Dict with "healthy" (list of ok files) and "issues" (list of problem descriptions).
        """
        healthy: list[str] = []
        issues: list[str] = []

        if not self.workspace_path.exists():
            issues.append(f"Workspace directory does not exist: {self.workspace_path}")
            return {"healthy": healthy, "issues": issues}

        def _check() -> tuple[list[str], list[str]]:
            _healthy: list[str] = []
            _issues: list[str] = []
            _exclude_dirs = {"__pycache__", "node_modules", ".git", ".venv"}

            for item in sorted(self.workspace_path.rglob("*")):
                if item.is_dir():
                    continue
                # Skip excluded directories
                try:
                    rel_parts = item.relative_to(self.workspace_path).parts
                except ValueError:
                    continue
                if any(part.startswith(".") or part in _exclude_dirs for part in rel_parts):
                    continue

                rel_path = str(item.relative_to(self.workspace_path))

                # Check broken symlinks
                if item.is_symlink() and not item.exists():
                    _issues.append(f"Broken symlink: {rel_path}")
                    continue

                # Check readability
                try:
                    stat = item.stat()
                except OSError as e:
                    _issues.append(f"Unreadable file: {rel_path} ({e})")
                    continue

                # Check empty files (skip __init__.py which are often empty)
                if stat.st_size == 0 and item.name != "__init__.py":
                    _issues.append(f"Empty file: {rel_path}")
                    continue

                _healthy.append(rel_path)

            return _healthy, _issues

        healthy, issues = await asyncio.to_thread(_check)

        if issues:
            logger.warning(
                "workspace_consistency_issues",
                task_id=self.task_id,
                issue_count=len(issues),
                issues=issues[:10],  # Log first 10
            )
        else:
            logger.info(
                "workspace_consistency_ok",
                task_id=self.task_id,
                file_count=len(healthy),
            )

        return {"healthy": healthy, "issues": issues}

    @staticmethod
    def _sanitize_for_filename(text: str, max_len: int = 50) -> str:
        """Convert text to a safe filename component."""
        # Replace common separators with underscore
        safe = re.sub(r"[^\w\s-]", "", text.lower())
        safe = re.sub(r"[\s-]+", "_", safe).strip("_")
        return safe[:max_len] if safe else "untitled"
