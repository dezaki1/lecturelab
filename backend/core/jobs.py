"""Persistent job queue: a SQLite table plus one worker thread.

Jobs survive restarts: anything left ``running`` when the app died is re-queued on startup.
Handlers run on the worker thread one at a time (so only one GPU job ever runs). Handlers that
need lots of CPU/GPU must do the heavy part in a child process (see M4) so the API stays
responsive and VRAM is released when the process exits.
"""

from __future__ import annotations

import contextlib
import logging
import subprocess
import threading
import traceback
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from sqlmodel import Session, col, select

from backend.core.db import session_scope
from backend.core.events import bus
from backend.core.models import Job, JobStatus, utcnow

log = logging.getLogger(__name__)


class JobError(Exception):
    """Raise from a handler for an expected failure; the message is shown to the user."""


@dataclass
class JobContext:
    job_id: int
    kind: str
    lecture_id: int | None
    payload: dict[str, Any]

    def progress(self, fraction: float, message: str = "") -> None:
        fraction = max(0.0, min(1.0, fraction))
        with session_scope() as s:
            job = s.get(Job, self.job_id)
            if job is None:
                return
            job.progress = fraction
            job.message = message
            s.add(job)
            s.commit()
        bus.publish("job", _job_event(self.job_id, "running", fraction, message, self.lecture_id))


Handler = Callable[[JobContext], None]
HANDLERS: dict[str, Handler] = {}

# Human-readable job names (UI + notifications).
LABELS = {
    "encode_recording": "Saving recording",
    "import_media": "Importing",
    "download_media": "Downloading",
    "save_media": "Saving video",
    "transcribe": "Transcription",
    "generate": "Study material",
    "index": "Search indexing",
    "subject_hub": "Course overview",
    "cleanup": "Transcript check",
    "slides": "Reading slides",
    "document": "Reading PDF",
    "past_exam": "Analysing past exam",
    "exam_build": "Preparing exam",
    "exam_grade": "Grading exam",
    "weak_practice": "Practice for weak topics",
    "cheatsheet": "Cheat sheet",
}
# Kinds that announce their own failures (generation reports which sections failed).
SELF_NOTIFYING = {"generate"}


# Lanes. Reading slide text (OCR) only uses the CPU, so it runs on a second lane: while the GPU
# transcribes one lecture, the CPU reads the next one's slides. Two jobs never work on the same
# lecture at once (they would overwrite each other's stage updates), and jobs that use a
# lecture's slide text wait until its OCR is done.
SIDE_LANE = {"slides"}
BEFORE_SLIDE_TEXT = {"download_media", "import_media", "encode_recording", "transcribe"}
_claim_lock = threading.Lock()


def handler(kind: str) -> Callable[[Handler], Handler]:
    def register(fn: Handler) -> Handler:
        HANDLERS[kind] = fn
        return fn

    return register


def _job_event(
    job_id: int, status: str, progress: float, message: str, lecture_id: int | None
) -> dict[str, Any]:
    return {
        "id": job_id,
        "status": status,
        "progress": progress,
        "message": message,
        "lecture_id": lecture_id,
    }


def enqueue(
    session: Session,
    kind: str,
    lecture_id: int | None = None,
    payload: dict[str, Any] | None = None,
) -> Job:
    job = Job(kind=kind, lecture_id=lecture_id, payload=payload or {})
    session.add(job)
    session.commit()
    session.refresh(job)
    bus.publish("job", _job_event(job.id, job.status, 0.0, "", lecture_id))  # type: ignore[arg-type]
    wake_all()
    return job


_children: set[subprocess.Popen] = set()  # type: ignore[type-arg]
_children_lock = threading.Lock()


def track_child(proc: subprocess.Popen) -> None:  # type: ignore[type-arg]
    """Register a job's child process (Whisper, OCR) so shutdown can end it."""
    with _children_lock:
        _children.add(proc)


def untrack_child(proc: subprocess.Popen) -> None:  # type: ignore[type-arg]
    with _children_lock:
        _children.discard(proc)


def _kill_children() -> None:
    with _children_lock:
        procs = list(_children)
    for p in procs:
        with contextlib.suppress(OSError):
            p.kill()


class Worker:
    """Runs queued jobs one at a time. ``lane``: None takes every job in order (tests); "main"
    everything but SIDE_LANE jobs, "side" only those (see SIDE_LANE)."""

    def __init__(self, poll_s: float = 2.0, lane: str | None = None) -> None:
        self._poll_s = poll_s
        self.lane = lane
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self, requeue: bool = True) -> None:
        if requeue:
            self.requeue_interrupted()
        name = f"job-worker-{self.lane}" if self.lane else "job-worker"
        self._thread = threading.Thread(target=self._loop, name=name, daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        self._wake.set()
        _kill_children()  # the interrupted job is re-queued and resumes on the next start
        if self._thread:
            self._thread.join(timeout)

    def wake(self) -> None:
        self._wake.set()

    @staticmethod
    def requeue_interrupted() -> int:
        with session_scope() as s:
            jobs = s.exec(select(Job).where(Job.status == JobStatus.running)).all()
            for job in jobs:
                job.status = JobStatus.queued
                job.message = "Resumed after restart"
                s.add(job)
            s.commit()
            return len(jobs)

    def run_pending(self) -> int:
        """Run queued jobs until none are left. Returns how many ran (used by tests)."""
        n = 0
        while not self._stop.is_set():
            with _claim_lock, session_scope() as s:
                job = self._next(s)
                if job is None:
                    return n
                job.status = JobStatus.running
                job.started_at = utcnow()
                job.attempts += 1
                job.error = None
                s.add(job)
                s.commit()
                ctx = JobContext(job.id, job.kind, job.lecture_id, dict(job.payload))  # type: ignore[arg-type]
            self._execute(ctx)
            n += 1
        return n

    def _next(self, s: Session) -> Job | None:
        queued = s.exec(
            select(Job).where(Job.status == JobStatus.queued).order_by(col(Job.id))
        ).all()
        if self.lane is None:
            return queued[0] if queued else None
        running = s.exec(select(Job).where(Job.status == JobStatus.running)).all()
        busy = {j.lecture_id for j in running if j.lecture_id is not None}
        reading = {j.lecture_id for j in [*queued, *running] if j.kind in SIDE_LANE}
        for job in queued:
            side = job.kind in SIDE_LANE
            if side != (self.lane == "side"):
                continue
            if job.lecture_id is not None and job.lecture_id in busy:
                continue
            if not side and job.lecture_id in reading and job.kind not in BEFORE_SLIDE_TEXT:
                continue
            return job
        return None

    def _execute(self, ctx: JobContext) -> None:
        bus.publish("job", _job_event(ctx.job_id, "running", 0.0, "", ctx.lecture_id))
        fn = HANDLERS.get(ctx.kind)
        status, error = JobStatus.done, None
        try:
            if fn is None:
                raise JobError(f"No handler for job kind {ctx.kind!r}")
            fn(ctx)
        except JobError as e:
            status, error = JobStatus.failed, str(e)
            log.warning("Job %s (%s) failed: %s", ctx.job_id, ctx.kind, e)
        except Exception as e:
            status, error = JobStatus.failed, f"{type(e).__name__}: {e}"
            log.error("Job %s (%s) crashed:\n%s", ctx.job_id, ctx.kind, traceback.format_exc())
        if status == JobStatus.failed and self._stop.is_set():
            # Stopped by shutdown (its child process was ended): run it again next start.
            log.info("Job %s (%s) interrupted by shutdown; re-queued", ctx.job_id, ctx.kind)
            with session_scope() as s:
                job = s.get(Job, ctx.job_id)
                if job is not None:
                    job.status, job.message = JobStatus.queued, "Resumes after restart"
                    s.add(job)
                    s.commit()
            return
        with session_scope() as s:
            job = s.get(Job, ctx.job_id)
            if job is None:
                return
            job.status = status
            job.error = error
            job.finished_at = utcnow()
            if status == JobStatus.done:
                job.progress = 1.0
            s.add(job)
            s.commit()
            bus.publish(
                "job", _job_event(ctx.job_id, status, job.progress, error or "", ctx.lecture_id)
            )
        if status == JobStatus.failed and ctx.kind not in SELF_NOTIFYING:
            bus.publish(
                "notify",
                {
                    "title": f"{LABELS.get(ctx.kind, ctx.kind)} failed",
                    "message": error or "",
                    "level": "error",
                    "lecture_id": ctx.lecture_id,
                },
            )

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.run_pending()
            except Exception:
                log.exception("Job worker loop error")
            self._wake.wait(self._poll_s)
            self._wake.clear()


worker: Worker | None = None
side_worker: Worker | None = None


def wake_all() -> None:
    for w in (worker, side_worker):
        if w is not None:
            w.wake()


def start_worker() -> Worker:
    global worker, side_worker
    worker = Worker(lane="main")
    side_worker = Worker(lane="side")
    worker.start()  # re-queues jobs a shutdown interrupted, before either lane looks
    side_worker.start(requeue=False)
    return worker


def stop_worker() -> None:
    global worker, side_worker
    for w in (side_worker, worker):
        if w is not None:
            w.stop()
    worker = side_worker = None
