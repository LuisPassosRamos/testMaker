"""Background jobs (extraction, rendering) with progress the UI can poll.

A small thread pool is enough for one-exam-at-a-time usage and keeps deployment
simple (no broker). Job state is persisted in the project so a page reload shows
the same progress, and failures carry a message written for teachers.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

from app.domain.models import JobState, Project, utcnow
from app.importing.storage import ProjectStore

log = logging.getLogger(__name__)

Progress = Callable[[str, float, str], None]


class JobBusy(Exception):
    pass


class JobRunner:
    def __init__(self, store: ProjectStore, workers: int = 2):
        self.store = store
        self.pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="job")
        # AI jobs mostly wait for the network and use little memory: their own small pool, so a teacher
        # creating an exam (1–2 minutes) never holds up another teacher's PDF.
        self.ai_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="ai-job")
        self._running: dict[str, str] = {}  # project id -> job kind
        self._guard = threading.Lock()
        self.results: dict[str, object] = {}  # job id -> result (render results)

    def is_running(self, pid: str) -> bool:
        with self._guard:
            return pid in self._running

    def start(self, pid: str, kind: str, work: Callable[[Progress], object], failure_message: str) -> JobState:
        with self._guard:
            if pid in self._running:
                raise JobBusy(self._running[pid])
            self._running[pid] = kind
        job = JobState(kind=kind, status="running", stage="start", message="Começando…", progress=0.0,  # type: ignore[arg-type]
                       started_at=utcnow())
        self._update(pid, job)
        pool = self.ai_pool if kind in ("generate", "organize") else self.pool
        pool.submit(self._run, pid, job, work, failure_message)
        return job

    def shutdown(self) -> None:
        for pool in (self.pool, self.ai_pool):
            pool.shutdown(wait=False, cancel_futures=True)

    def _update(self, pid: str, job: JobState) -> None:
        def apply(p: Project) -> None:
            p.job = job.model_copy()

        try:
            self.store.mutate(pid, apply, bump=False)
        except Exception:
            log.warning("job %s: could not persist state", job.id)

    def _run(self, pid: str, job: JobState, work: Callable[[Progress], object], failure_message: str) -> None:
        last_write = 0.0
        started = time.perf_counter()

        def progress(stage: str, fraction: float, message: str) -> None:
            nonlocal last_write
            job.stage, job.progress, job.message = stage, round(min(max(fraction, 0.0), 0.99), 3), message
            now = time.perf_counter()
            if now - last_write > 0.25:
                last_write = now
                self._update(pid, job)

        try:
            result = work(progress)
            self.results[job.id] = result
            job.status, job.progress, job.stage, job.message = "done", 1.0, "done", "Pronto!"
            log.info("job %s (%s) finished in %.1fs", job.id, job.kind, time.perf_counter() - started)
        except Exception as exc:
            job.status, job.stage = "failed", "failed"
            job.error = str(exc) if _is_user_facing(exc) else failure_message
            job.message = job.error
            log.warning("job %s (%s) failed: %s", job.id, job.kind, exc.__class__.__name__)
        finally:
            job.finished_at = utcnow()
            self._update(pid, job)
            with self._guard:
                self._running.pop(pid, None)


def _is_user_facing(exc: Exception) -> bool:
    """Only our own errors carry Portuguese messages safe to show; others get a generic text."""
    from app.ai.base import AiError, AiUnavailable
    from app.rendering.converter import ConversionError

    return isinstance(exc, ConversionError | AiError | AiUnavailable)
