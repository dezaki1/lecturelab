"""Per-lecture stage bookkeeping shared by pipeline jobs."""

from __future__ import annotations

from pathlib import Path

from backend.core.db import session_scope
from backend.core.events import bus
from backend.core.library import rel_path
from backend.core.models import Lecture, LectureStatus


def capture_done(lecture_id: int, audio: Path, duration: float | None, notify_title: str) -> None:
    """The lecture has playable audio. (M4 will queue transcription from here.)"""
    with session_scope() as s:
        lecture = s.get(Lecture, lecture_id)
        if lecture is None:
            return
        lecture.media_path = rel_path(audio)
        if duration is not None:
            lecture.duration_s = duration
        lecture.stages = {**lecture.stages, "capture": "done"}
        lecture.status = LectureStatus.ready
        lecture.error = None
        s.add(lecture)
        s.commit()
        subject_id, title = lecture.subject_id, lecture.title
    bus.publish("lecture", {"id": lecture_id, "subject_id": subject_id})
    bus.publish("notify", {"title": notify_title, "message": title, "level": "success"})


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
