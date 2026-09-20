"""Whole-market discovery jobs that must not occupy the API event loop.

One job runs at a time. The runner is awaited from a worker thread so
`GET /api/health` stays responsive even when Stage 1 is in a long provider wait.
"""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import uuid4

from app.utils.time import utc_now


def new_job_id() -> str:
    return uuid4().hex[:12]


@dataclass
class ResearchJob:
    job_id: str
    status: str  # QUEUED / RUNNING / COMPLETE / FAILED
    stage: str
    processed: int = 0
    total: int = 0
    started_at: datetime | None = None
    finished_at: datetime | None = None
    message: str = ""
    error: str | None = None
    result: dict[str, Any] | None = None
    cache_hits: int = 0
    cache_misses: int = 0
    symbols: tuple[str, ...] | None = None

    def elapsed_seconds(self, *, now: datetime | None = None) -> float:
        start = self.started_at
        if start is None:
            return 0.0
        end = self.finished_at or now or utc_now()
        return max(0.0, (end - start).total_seconds())

    def as_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "status": self.status,
            "stage": self.stage,
            "processed": self.processed,
            "total": self.total,
            "elapsed_seconds": round(self.elapsed_seconds(), 1),
            "message": self.message,
            "error": self.error,
            "cache_hits": self.cache_hits,
            "cache_misses": self.cache_misses,
            "symbols": list(self.symbols) if self.symbols is not None else None,
            "scan_kind": "EXPLICIT" if self.symbols is not None else "F&O_UNIVERSE",
            "result": self.result,
        }


class ResearchJobRegistry:
    """Process-local. A restart clears jobs — that is documented, not hidden."""

    def __init__(self) -> None:
        self._jobs: dict[str, ResearchJob] = {}
        self._latest_id: str | None = None
        self._lock = asyncio.Lock()
        self._running_task: asyncio.Task[None] | None = None
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="tire-discover")

    def get(self, job_id: str) -> ResearchJob | None:
        return self._jobs.get(job_id)

    def latest(self) -> ResearchJob | None:
        if self._latest_id is None:
            return None
        return self._jobs.get(self._latest_id)

    def active(self) -> ResearchJob | None:
        job = self.latest()
        if job is not None and job.status in ("QUEUED", "RUNNING"):
            return job
        return None

    async def start(self, *, symbols: tuple[str, ...] | None, runner: Any) -> ResearchJob:
        async with self._lock:
            existing = self.active()
            if existing is not None:
                return existing
            job = ResearchJob(
                job_id=new_job_id(),
                status="QUEUED",
                stage="queued",
                message="Scan queued.",
                symbols=symbols,
            )
            self._jobs[job.job_id] = job
            self._latest_id = job.job_id
            self._running_task = asyncio.create_task(self._run(job, runner), name=f"tire-discover-{job.job_id}")
            return job

    async def _run(self, job: ResearchJob, runner: Any) -> None:
        job.status = "RUNNING"
        job.stage = "starting"
        job.started_at = utc_now()
        job.message = "Market scan running."
        try:
            view = await runner(job)
            job.result = view
            job.status = "COMPLETE"
            job.stage = "complete"
            job.message = "Scan complete."
        except Exception as exc:  # noqa: BLE001 -- job isolation; never crash the API
            job.status = "FAILED"
            job.stage = "failed"
            job.error = str(exc)
            job.message = "Scan could not finish."
        finally:
            job.finished_at = utc_now()
