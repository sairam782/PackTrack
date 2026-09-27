"""A small in-process registry so video processing can report progress.

Deliberately in-memory: this is state that is worthless once the server
restarts, and the prototype has no reason to be more than one process.
"""

from __future__ import annotations

import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

JOB_TTL_S = 30 * 60
MAX_JOBS = 100


@dataclass
class Job:
    id: str
    kind: str
    label: str
    status: str = "running"  # running | done | error
    percent: float = 0.0
    detail: str = "starting"
    result: dict | None = None
    error: str | None = None
    created_at: float = field(default_factory=time.monotonic)

    def snapshot(self) -> dict[str, Any]:
        payload = {
            "job_id": self.id,
            "kind": self.kind,
            "label": self.label,
            "status": self.status,
            "percent": round(self.percent, 1),
            "detail": self.detail,
        }
        if self.result is not None:
            payload["result"] = self.result
        if self.error:
            payload["error"] = self.error
        return payload


class JobRegistry:
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()

    def _evict(self) -> None:
        now = time.monotonic()
        stale = [
            job_id
            for job_id, job in self._jobs.items()
            if job.status != "running" and now - job.created_at > JOB_TTL_S
        ]
        for job_id in stale:
            self._jobs.pop(job_id, None)
        if len(self._jobs) > MAX_JOBS:
            done = sorted(
                (j for j in self._jobs.values() if j.status != "running"),
                key=lambda j: j.created_at,
            )
            for job in done[: len(self._jobs) - MAX_JOBS]:
                self._jobs.pop(job.id, None)

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def list(self) -> list[dict]:
        with self._lock:
            jobs = sorted(self._jobs.values(), key=lambda j: -j.created_at)
        return [j.snapshot() for j in jobs]

    def start(self, kind: str, label: str, work: Callable[[Job], dict]) -> Job:
        with self._lock:
            self._evict()
            job = Job(id=uuid.uuid4().hex[:12], kind=kind, label=label)
            self._jobs[job.id] = job

        def runner() -> None:
            try:
                job.result = work(job)
            except Exception as exc:  # surfaced to the user, so keep the text
                job.status = "error"
                job.error = str(exc) or exc.__class__.__name__
                job.detail = "failed"
            else:
                job.status = "done"
                job.percent = 100.0
                job.detail = "finished"

        threading.Thread(target=runner, name=f"job-{job.id}", daemon=True).start()
        return job


registry = JobRegistry()
