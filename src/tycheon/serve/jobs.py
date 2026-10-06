"""Asynchronous jobs: submit work, get a job id, poll for the result.

Forecast, calibration, risk and backtest calls can take seconds to minutes, so the API also offers
them as jobs. A job runs the *same governed tool call* as the synchronous endpoint (grant,
policy, audit) in a background task. Jobs are tenant-scoped: a job id from one tenant is simply
not found by another. They live in memory and are bounded; a restart loses them (v0.2.0).
"""

from __future__ import annotations

import asyncio
import secrets
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from tycheon.agents.protocols import ToolResult

JobStatus = Literal["queued", "running", "succeeded", "failed"]


class TooManyJobsError(Exception):
    """The tenant has too many unfinished jobs; try again later."""


@dataclass
class Job:
    id: str
    tenant: str
    kind: str
    status: JobStatus = "queued"
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    finished_at: datetime | None = None
    result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None


class JobManager:
    def __init__(self, *, max_history: int = 200, max_unfinished_per_tenant: int = 8) -> None:
        self._jobs: dict[str, Job] = {}
        self._tasks: set[asyncio.Task[None]] = set()
        self._max_history = max_history
        self._max_unfinished = max_unfinished_per_tenant

    def submit(self, tenant: str, kind: str, work: Callable[[], Awaitable[ToolResult]]) -> Job:
        unfinished = sum(
            1
            for j in self._jobs.values()
            if j.tenant == tenant and j.status in ("queued", "running")
        )
        if unfinished >= self._max_unfinished:
            raise TooManyJobsError
        job = Job(id=secrets.token_urlsafe(16), tenant=tenant, kind=kind)
        self._jobs[job.id] = job
        self._trim()
        task = asyncio.get_running_loop().create_task(self._run(job, work))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return job

    async def _run(self, job: Job, work: Callable[[], Awaitable[ToolResult]]) -> None:
        job.status = "running"
        try:
            result = await work()
        except Exception:  # a job must end in a terminal state; no internals in the error
            job.error = {"code": "internal_error", "message": "The job could not be completed."}
            job.status = "failed"
        else:
            if result.ok and result.output is not None:
                job.result, job.status = result.output, "succeeded"
            else:
                job.error = {
                    "code": result.error_code or result.status.lower(),
                    "message": result.message,
                }
                job.status = "failed"
        job.finished_at = datetime.now(UTC)

    def get(self, tenant: str, job_id: str) -> Job | None:
        job = self._jobs.get(job_id)
        return job if job is not None and job.tenant == tenant else None

    def list(self, tenant: str) -> list[Job]:
        return [j for j in self._jobs.values() if j.tenant == tenant]

    def _trim(self) -> None:
        if len(self._jobs) <= self._max_history:
            return
        for job_id in [j.id for j in self._jobs.values() if j.status in ("succeeded", "failed")]:
            del self._jobs[job_id]
            if len(self._jobs) <= self._max_history:
                break

    async def drain(self) -> None:
        """Wait for running jobs (used on shutdown and in tests)."""
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
