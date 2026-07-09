"""
MekongAI Nexus - Batch Runner (Hermes-agent inspired).

Process multiple prompts in parallel with checkpointing.
Resume interrupted batches from the last checkpoint.

Inspired by: hermes-agent/batch_runner.py
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import structlog

logger = structlog.get_logger(__name__)


# ------------------------------------------------------------------
# Data models
# ------------------------------------------------------------------

@dataclass
class BatchJob:
    """A single prompt within a batch."""
    job_id: str
    prompt: str
    status: str = "pending"        # pending | running | completed | failed
    result: Optional[str] = None
    error: Optional[str] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "BatchJob":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class BatchResult:
    """Summary of a batch run."""
    batch_id: str
    total: int
    completed: int
    failed: int
    results: list[BatchJob] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "batch_id": self.batch_id,
            "total": self.total,
            "completed": self.completed,
            "failed": self.failed,
            "results": [r.to_dict() for r in self.results],
        }


# ------------------------------------------------------------------
# BatchRunner
# ------------------------------------------------------------------

class BatchRunner:
    """Process multiple prompts concurrently with checkpointing.

    Usage::

        runner = BatchRunner(llm=get_core_llm(), max_concurrent=3)
        result = await runner.run_batch(["prompt1", "prompt2", "prompt3"])
        print(result.completed, result.failed)

    Checkpoint files are stored as JSON in *checkpoint_dir* so that
    interrupted batches can be resumed via :meth:`resume_batch`.
    """

    def __init__(
        self,
        llm: Any,
        max_concurrent: int = 3,
        checkpoint_dir: str = "workspace_data/.batch_checkpoints",
    ) -> None:
        self._llm = llm
        self._max_concurrent = max_concurrent
        self._checkpoint_dir = Path(checkpoint_dir)
        self._checkpoint_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def run_batch(
        self,
        prompts: list[str],
        batch_id: Optional[str] = None,
    ) -> BatchResult:
        """Run a list of prompts concurrently with checkpointing.

        If *batch_id* matches an existing checkpoint, the batch is
        resumed from where it left off.
        """
        bid = batch_id or f"batch_{uuid.uuid4().hex[:12]}"
        checkpoint_path = self._checkpoint_dir / f"{bid}.json"

        # Resume from checkpoint if it exists
        if checkpoint_path.exists():
            logger.info("batch_resuming", batch_id=bid)
            return await self._resume_from_checkpoint(bid, checkpoint_path, prompts)

        # Create fresh batch
        jobs = [
            BatchJob(job_id=f"{bid}_job_{i}", prompt=p)
            for i, p in enumerate(prompts)
        ]
        self._save_checkpoint(bid, jobs)
        return await self._execute_jobs(bid, jobs)

    async def resume_batch(self, batch_id: str) -> BatchResult:
        """Resume an interrupted batch from its checkpoint."""
        checkpoint_path = self._checkpoint_dir / f"{batch_id}.json"
        if not checkpoint_path.exists():
            logger.warning("batch_checkpoint_not_found", batch_id=batch_id)
            return BatchResult(
                batch_id=batch_id, total=0, completed=0, failed=0, results=[],
            )
        return await self._resume_from_checkpoint(batch_id, checkpoint_path)

    def get_batch_status(self, batch_id: str) -> dict[str, Any]:
        """Return current status of a batch from its checkpoint file."""
        checkpoint_path = self._checkpoint_dir / f"{batch_id}.json"
        if not checkpoint_path.exists():
            return {"error": "Batch not found", "batch_id": batch_id}

        data = json.loads(checkpoint_path.read_text(encoding="utf-8"))
        jobs = [BatchJob.from_dict(j) for j in data.get("jobs", [])]
        completed = sum(1 for j in jobs if j.status == "completed")
        failed = sum(1 for j in jobs if j.status == "failed")
        pending = sum(1 for j in jobs if j.status == "pending")
        running = sum(1 for j in jobs if j.status == "running")

        return {
            "batch_id": batch_id,
            "total": len(jobs),
            "completed": completed,
            "failed": failed,
            "pending": pending,
            "running": running,
            "progress_pct": round(((completed + failed) / max(len(jobs), 1)) * 100, 1),
        }

    # ------------------------------------------------------------------
    # Internal execution
    # ------------------------------------------------------------------

    async def _resume_from_checkpoint(
        self,
        batch_id: str,
        checkpoint_path: Path,
        new_prompts: Optional[list[str]] = None,
    ) -> BatchResult:
        """Load checkpoint and re-run only pending/failed jobs."""
        data = json.loads(checkpoint_path.read_text(encoding="utf-8"))
        jobs = [BatchJob.from_dict(j) for j in data.get("jobs", [])]

        # If new prompts provided, append any that don't exist yet
        if new_prompts:
            existing_prompts = {j.prompt for j in jobs}
            for i, p in enumerate(new_prompts):
                if p not in existing_prompts:
                    jobs.append(BatchJob(
                        job_id=f"{batch_id}_job_{len(jobs) + i}",
                        prompt=p,
                    ))

        # Reset "running" jobs back to "pending" (were interrupted)
        for job in jobs:
            if job.status == "running":
                job.status = "pending"

        return await self._execute_jobs(batch_id, jobs)

    async def _execute_jobs(self, batch_id: str, jobs: list[BatchJob]) -> BatchResult:
        """Execute pending jobs concurrently with a semaphore."""
        semaphore = asyncio.Semaphore(self._max_concurrent)
        pending_jobs = [j for j in jobs if j.status == "pending"]

        logger.info("batch_started", batch_id=batch_id,
                     total=len(jobs), pending=len(pending_jobs))

        async def _run_one(job: BatchJob) -> None:
            async with semaphore:
                job.status = "running"
                job.started_at = datetime.utcnow().isoformat()
                try:
                    messages = [
                        {"role": "system", "content": "You are a helpful assistant."},
                        {"role": "user", "content": job.prompt},
                    ]
                    response = await self._llm.chat(messages=messages)
                    job.result = response.get("content", "")
                    job.status = "completed"
                except Exception as exc:
                    job.error = str(exc)[:500]
                    job.status = "failed"
                    logger.error("batch_job_failed",
                                 batch_id=batch_id, job_id=job.job_id, error=str(exc))
                finally:
                    job.completed_at = datetime.utcnow().isoformat()
                    # Checkpoint after each job
                    self._save_checkpoint(batch_id, jobs)

        # Run all pending jobs concurrently
        if pending_jobs:
            await asyncio.gather(
                *[_run_one(j) for j in pending_jobs],
                return_exceptions=True,
            )

        completed = sum(1 for j in jobs if j.status == "completed")
        failed = sum(1 for j in jobs if j.status == "failed")

        logger.info("batch_completed", batch_id=batch_id,
                     total=len(jobs), completed=completed, failed=failed)

        return BatchResult(
            batch_id=batch_id,
            total=len(jobs),
            completed=completed,
            failed=failed,
            results=jobs,
        )

    # ------------------------------------------------------------------
    # Checkpointing
    # ------------------------------------------------------------------

    def _save_checkpoint(self, batch_id: str, jobs: list[BatchJob]) -> None:
        """Persist batch state to a JSON checkpoint file."""
        checkpoint_path = self._checkpoint_dir / f"{batch_id}.json"
        data = {
            "batch_id": batch_id,
            "updated_at": datetime.utcnow().isoformat(),
            "jobs": [j.to_dict() for j in jobs],
        }
        checkpoint_path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
