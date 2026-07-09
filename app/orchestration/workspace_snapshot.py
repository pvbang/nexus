"""
MekongAI Nexus - Workspace Snapshot & Rollback.

FIX GAP-6: Recover from agents breaking each other's work.

Problem: If agent B overwrites/corrupts files that agent A created,
there's no way to recover without manual intervention.

Solution: Lightweight snapshot system using file hashing + copy-on-write.
- Before each turn execution, snapshot all _work/ files (hash + optional copy)
- After turn execution, if evaluation detects regression → rollback
- Only snapshots files under _work/ (agent workspace), not _context/
- Uses SHA256 hashes for change detection, full copies only for small files
- Larger files use hardlinks when filesystem supports it

NOT a full git system — intentionally simpler:
- Max 5 snapshots kept (sliding window)
- Only _work/ directory (not system files)
- Rollback is "restore to snapshot state" (destructive forward)
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

_WORKSPACE_BASE = Path(os.environ.get("WORKSPACE_DIR", "workspace_data"))
_MAX_SNAPSHOTS = 5
_MAX_FILE_COPY_BYTES = 1024 * 1024  # 1MB — files larger than this get hash-only tracking
_MAX_TOTAL_SNAPSHOT_BYTES = 50 * 1024 * 1024  # 50MB total across all snapshots


@dataclass
class FileState:
    """State of a single file at snapshot time."""

    path: str  # Relative to _work/
    hash: str  # SHA256
    size: int
    has_copy: bool = False  # Whether we have a full backup copy


@dataclass
class Snapshot:
    """Complete workspace state at a point in time."""

    turn: int
    timestamp: float
    files: list[FileState] = field(default_factory=list)
    total_bytes: int = 0
    snapshot_dir: str = ""  # Where backup copies live


class WorkspaceSnapshot:
    """Manages workspace snapshots for rollback capability.

    Lightweight approach:
    1. Before turn: hash all _work/ files, copy small ones
    2. After turn: if regression detected, can rollback
    3. Keep max 5 snapshots (sliding window)
    """

    def __init__(self, task_id: str) -> None:
        from app.core import task_paths
        self._task_id = task_id
        self._work_dir = task_paths.work_dir(task_id)
        self._snapshots_dir = task_paths.snapshots_dir(task_id)
        self._snapshots: list[Snapshot] = []
        self._total_snapshot_bytes: int = 0
        # Content-addressed blob store: file content is stored ONCE per unique
        # hash and shared across turns. Unchanged files (the common case) cost
        # zero extra bytes each turn — fixes the repeated re-copy bloat where 5
        # turns of ~25 frozen docs duplicated the same state 5x.
        self._blobs_dir = self._snapshots_dir / "_blobs"

    def _blob_path(self, file_hash: str) -> Path:
        return self._blobs_dir / file_hash

    def take_snapshot(self, turn: int) -> Snapshot | None:
        """Take a snapshot of the current workspace state.

        Returns None if workspace is empty or doesn't exist.
        """
        if not self._work_dir.exists():
            return None

        files = list(self._work_dir.rglob("*"))
        work_files = [f for f in files if f.is_file()]

        if not work_files:
            return None

        # Snapshot dir now only holds metadata; file content lives in the shared
        # content-addressed blob store (deduped across turns).
        snap_dir = self._snapshots_dir / f"turn_{turn}"
        snap_dir.mkdir(parents=True, exist_ok=True)
        self._blobs_dir.mkdir(parents=True, exist_ok=True)

        file_states: list[FileState] = []
        total_bytes = 0

        for filepath in work_files:
            try:
                rel_path = str(filepath.relative_to(self._work_dir))
                file_size = filepath.stat().st_size
                file_hash = self._hash_file(filepath)

                # Store content once per unique hash. Unchanged files across turns
                # reuse the existing blob → zero added bytes.
                has_copy = False
                if file_size <= _MAX_FILE_COPY_BYTES and file_hash:
                    blob_path = self._blob_path(file_hash)
                    if blob_path.exists():
                        # Content already stored by a prior file/turn — just reference it.
                        has_copy = True
                    elif self._total_snapshot_bytes + file_size <= _MAX_TOTAL_SNAPSHOT_BYTES:
                        shutil.copy2(filepath, blob_path)
                        has_copy = True
                        self._total_snapshot_bytes += file_size

                file_states.append(FileState(
                    path=rel_path,
                    hash=file_hash,
                    size=file_size,
                    has_copy=has_copy,
                ))
                total_bytes += file_size
            except Exception as e:
                logger.debug("snapshot_file_failed", path=str(filepath), error=str(e)[:50])

        snapshot = Snapshot(
            turn=turn,
            timestamp=__import__("time").time(),
            files=file_states,
            total_bytes=total_bytes,
            snapshot_dir=str(snap_dir),
        )

        self._snapshots.append(snapshot)

        # Evict old snapshots
        while len(self._snapshots) > _MAX_SNAPSHOTS:
            old = self._snapshots.pop(0)
            self._cleanup_snapshot(old)

        # Persist metadata
        self._save_metadata(snapshot)

        return snapshot

    def detect_regression(self, pre_turn_snapshot: Snapshot) -> dict[str, Any]:
        """Compare current workspace against a snapshot.

        Detects:
        - Deleted files (were in snapshot, now gone)
        - Modified files (hash changed)
        - Size regression (total size decreased significantly)

        Returns dict with regression info (empty if no regression).
        """
        if not pre_turn_snapshot or not pre_turn_snapshot.files:
            return {}

        current_state = self._get_current_state()
        current_paths = {f["path"] for f in current_state}
        snapshot_paths = {f.path for f in pre_turn_snapshot.files}

        # Deleted files
        deleted = snapshot_paths - current_paths
        # Modified files
        current_hashes = {f["path"]: f["hash"] for f in current_state}
        modified = []
        for snap_file in pre_turn_snapshot.files:
            if snap_file.path in current_hashes:
                if current_hashes[snap_file.path] != snap_file.hash:
                    modified.append(snap_file.path)

        # Size regression (>50% shrinkage indicates destructive action)
        current_total = sum(f["size"] for f in current_state)
        size_ratio = current_total / max(pre_turn_snapshot.total_bytes, 1)

        regression = {}
        if deleted:
            regression["deleted_files"] = list(deleted)[:10]
        if modified:
            regression["modified_files"] = modified[:10]
        if size_ratio < 0.5 and pre_turn_snapshot.total_bytes > 1000:
            regression["size_regression"] = {
                "before": pre_turn_snapshot.total_bytes,
                "after": current_total,
                "ratio": round(size_ratio, 2),
            }

        return regression

    def rollback_to(self, snapshot: Snapshot) -> dict[str, Any]:
        """Rollback workspace to a previous snapshot state.

        Only restores files that have backup copies.
        Returns summary of what was restored.
        """
        if not snapshot.snapshot_dir:
            return {"success": False, "error": "No snapshot directory"}

        snap_dir = Path(snapshot.snapshot_dir)
        if not snap_dir.exists():
            return {"success": False, "error": "Snapshot directory missing"}

        restored = []
        failed = []

        for file_state in snapshot.files:
            if not file_state.has_copy:
                continue

            # Content is keyed by hash in the shared blob store.
            backup_path = self._blob_path(file_state.hash)
            target_path = self._work_dir / file_state.path

            if not backup_path.exists():
                failed.append(file_state.path)
                continue

            try:
                target_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(backup_path, target_path)
                restored.append(file_state.path)
            except Exception as e:
                failed.append(file_state.path)
                logger.debug("rollback_file_failed", path=file_state.path, error=str(e)[:50])

        return {
            "success": len(restored) > 0,
            "restored": restored,
            "failed": failed,
            "snapshot_turn": snapshot.turn,
        }

    def get_latest_snapshot(self) -> Snapshot | None:
        """Get the most recent snapshot."""
        return self._snapshots[-1] if self._snapshots else None

    def get_snapshot_for_turn(self, turn: int) -> Snapshot | None:
        """Get snapshot taken before a specific turn."""
        for snap in reversed(self._snapshots):
            if snap.turn <= turn:
                return snap
        return None

    def _get_current_state(self) -> list[dict[str, Any]]:
        """Get current workspace file states (for comparison)."""
        if not self._work_dir.exists():
            return []

        state = []
        for filepath in self._work_dir.rglob("*"):
            if not filepath.is_file():
                continue
            try:
                rel_path = str(filepath.relative_to(self._work_dir))
                state.append({
                    "path": rel_path,
                    "hash": self._hash_file(filepath),
                    "size": filepath.stat().st_size,
                })
            except Exception:
                continue
        return state

    def _hash_file(self, filepath: Path) -> str:
        """SHA256 hash of file content."""
        h = hashlib.sha256()
        try:
            with open(filepath, "rb") as f:
                while chunk := f.read(8192):
                    h.update(chunk)
        except Exception:
            return ""
        return h.hexdigest()[:16]  # Truncated for space efficiency

    def _cleanup_snapshot(self, snapshot: Snapshot) -> None:
        """Remove an evicted snapshot's metadata dir and any now-unreferenced blobs.

        Blobs are shared across snapshots (content-addressed), so a blob is only
        deleted when NO remaining snapshot still references its hash.
        """
        # Remove the (metadata-only) snapshot dir.
        if snapshot.snapshot_dir:
            snap_dir = Path(snapshot.snapshot_dir)
            if snap_dir.exists():
                try:
                    shutil.rmtree(snap_dir)
                except Exception as e:
                    logger.debug("snapshot_cleanup_failed", error=str(e)[:50])

        # Reference-count blobs against the snapshots that remain.
        live_hashes = {
            f.hash for s in self._snapshots for f in s.files if f.has_copy and f.hash
        }
        evicted_hashes = {f.hash for f in snapshot.files if f.has_copy and f.hash}
        for h in evicted_hashes - live_hashes:
            blob = self._blob_path(h)
            if blob.exists():
                try:
                    blob.unlink()
                except Exception as e:
                    logger.debug("blob_cleanup_failed", error=str(e)[:50])

        # Recalculate total bytes from blobs still referenced on disk.
        counted: set[str] = set()
        total = 0
        for s in self._snapshots:
            for f in s.files:
                if f.has_copy and f.hash and f.hash not in counted:
                    if self._blob_path(f.hash).exists():
                        counted.add(f.hash)
                        total += f.size
        self._total_snapshot_bytes = total

    def _save_metadata(self, snapshot: Snapshot) -> None:
        """Save snapshot metadata for recovery."""
        meta_path = self._snapshots_dir / "snapshots.jsonl"
        try:
            meta = {
                "turn": snapshot.turn,
                "timestamp": snapshot.timestamp,
                "file_count": len(snapshot.files),
                "total_bytes": snapshot.total_bytes,
                "snapshot_dir": snapshot.snapshot_dir,
            }
            with open(meta_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(meta, ensure_ascii=False) + "\n")
        except Exception:
            pass
