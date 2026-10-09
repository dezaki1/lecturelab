"""Per-lecture stage bookkeeping shared by pipeline jobs."""

from __future__ import annotations

from pathlib import Path

from backend.core import jobs
from backend.core.config import get_settings
from backend.core.db import session_scope
from backend.core.events import bus
from backend.core.library import rel_path
from backend.core.models import Lecture, LectureStatus

TRANSCRIBE_JOB = "transcribe"
GENERATE_JOB = "generate"


def capture_done(lecture_id: int, audio: Path, duration: float | None, notify_title: str) -> None:
    """The lecture has playable audio; queue transcription unless disabled."""
    auto = get_settings().transcription.auto
    with session_scope() as s:
        lecture = s.get(Lecture, lecture_id)
        if lecture is None:
            return
        lecture.media_path = rel_path(audio)
        if duration is not None:
            lecture.duration_s = duration
        lecture.stages = {**lecture.stages, "capture": "done"}
        lecture.status = LectureStatus.processing if auto else LectureStatus.ready
        lecture.error = None
        if auto:
            lecture.stages = {**lecture.stages, "transcribe": "pending"}
        s.add(lecture)
        s.commit()
        subject_id, title = lecture.subject_id, lecture.title
        # Slides first: OCR is quick and its text helps generation and search later.
        from backend.pipeline.slides import queue_ocr

        queue_ocr(s, lecture_id)
        if auto:
            jobs.enqueue(s, TRANSCRIBE_JOB, lecture_id=lecture_id)
    bus.publish("lecture", {"id": lecture_id, "subject_id": subject_id})
    if notify_title:
        bus.publish("notify", {"title": notify_title, "message": title, "level": "success"})


def lecture_ready(lecture_id: int, title: str) -> None:
    """The one notification per lecture that matters: everything is done (SPEC M8)."""
    bus.publish(
        "notify",
        {"title": "Lecture ready", "message": title, "level": "success", "lecture_id": lecture_id},
    )


def stage_failed(lecture_id: int, stage: str, error: str) -> None:
    with session_scope() as s:
        lecture = s.get(Lecture, lecture_id)
        if lecture is None:
            return
        lecture.stages = {**lecture.stages, stage: "failed"}
        lecture.status = LectureStatus.failed
        lecture.error = error
        s.add(lecture)
        s.commit()
        subject_id = lecture.subject_id
    bus.publish("lecture", {"id": lecture_id, "subject_id": subject_id})
