from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlmodel import Session, col, func, select

from backend.capture.recorder import get_recorder
from backend.core import jobs as jobs_mod
from backend.core.db import get_session
from backend.core.events import bus
from backend.core.library import abs_path, move_lecture, trash_lecture_folder
from backend.core.models import (
    Bookmark,
    BookmarkKind,
    Job,
    JobStatus,
    Lecture,
    LectureSource,
    LectureStatus,
    Subject,
    TranscriptSegment,
)
from backend.pipeline.stages import TRANSCRIBE_JOB
from backend.search.index import INDEX_JOB, remove_lecture_index

router = APIRouter(prefix="/api/lectures", tags=["lectures"])

KINDS = {"", "VL", "VO", "UE", "other"}


class BookmarkRead(BaseModel):
    id: int
    t_s: float
    kind: BookmarkKind
    note: str


class LectureRead(BaseModel):
    id: int
    subject_id: int
    number: int  # 1-based position within the subject, by start time ("L5")
    title: str
    kind: str
    started_at: datetime
    duration_s: float | None
    source: LectureSource
    status: LectureStatus
    stages: dict[str, str]
    error: str | None
    has_audio: bool
    language: str | None = None
    transcription: dict | None = None  # model, device, speed_x, ... from the last run
    cost_usd: float = 0.0
    bookmark_count: int = 0
    job_progress: float | None = None
    job_message: str = ""


class LectureDetail(LectureRead):
    bookmarks: list[BookmarkRead] = Field(default_factory=list)


class LectureUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    kind: str | None = None
    subject_id: int | None = None


class BookmarkCreate(BaseModel):
    t_s: float = Field(ge=0)
    kind: BookmarkKind = BookmarkKind.important
    note: str = ""


def _numbers(session: Session, subject_id: int) -> dict[int, int]:
    ids = session.exec(
        select(Lecture.id)
        .where(Lecture.subject_id == subject_id)
        .order_by(col(Lecture.started_at), col(Lecture.id))
    ).all()
    return {lid: i + 1 for i, lid in enumerate(ids)}  # type: ignore[misc]


def _active_jobs(session: Session, lecture_ids: list[int]) -> dict[int, Job]:
    if not lecture_ids:
        return {}
    jobs = session.exec(
        select(Job).where(
            col(Job.lecture_id).in_(lecture_ids),
            col(Job.status).in_([JobStatus.queued, JobStatus.running]),
            Job.kind != INDEX_JOB,  # background indexing doesn't block or show as processing
        )
    ).all()
    return {j.lecture_id: j for j in jobs}  # type: ignore[misc]


def _to_read(
    lecture: Lecture, number: int, bookmark_count: int, job: Job | None
) -> dict[str, object]:
    return {
        **lecture.model_dump(),
        "number": number,
        "has_audio": bool(lecture.media_path) and abs_path(lecture.media_path).exists(),
        "transcription": lecture.meta.get("transcription"),
        "bookmark_count": bookmark_count,
        "job_progress": job.progress if job else None,
        "job_message": job.message if job else "",
    }


@router.get("", response_model=list[LectureRead])
def list_lectures(subject_id: int, session: Session = Depends(get_session)) -> list[dict]:
    lectures = session.exec(
        select(Lecture)
        .where(Lecture.subject_id == subject_id)
        .order_by(col(Lecture.started_at).desc(), col(Lecture.id).desc())
    ).all()
    ids = [lec.id for lec in lectures]
    counts = dict(
        session.exec(
            select(Bookmark.lecture_id, func.count(col(Bookmark.id)))
            .where(col(Bookmark.lecture_id).in_(ids))
            .group_by(Bookmark.lecture_id)
        ).all()
    )
    numbers = _numbers(session, subject_id)
    jobs = _active_jobs(session, ids)  # type: ignore[arg-type]
    return [
        _to_read(lec, numbers[lec.id], counts.get(lec.id, 0), jobs.get(lec.id))  # type: ignore[index]
        for lec in lectures
    ]


def _get(session: Session, lecture_id: int) -> Lecture:
    lecture = session.get(Lecture, lecture_id)
    if lecture is None:
        raise HTTPException(404, "Lecture not found")
    return lecture


def lecture_read(session: Session, lecture: Lecture) -> dict[str, object]:
    job = _active_jobs(session, [lecture.id]).get(lecture.id)  # type: ignore[list-item, arg-type]
    count = len(session.exec(select(Bookmark.id).where(Bookmark.lecture_id == lecture.id)).all())
    number = _numbers(session, lecture.subject_id)[lecture.id]  # type: ignore[index]
    return _to_read(lecture, number, count, job)


def _detail(session: Session, lecture: Lecture) -> dict[str, object]:
    bookmarks = session.exec(
        select(Bookmark).where(Bookmark.lecture_id == lecture.id).order_by(col(Bookmark.t_s))
    ).all()
    job = _active_jobs(session, [lecture.id]).get(lecture.id)  # type: ignore[list-item, arg-type]
    return {
        **_to_read(lecture, _numbers(session, lecture.subject_id)[lecture.id], len(bookmarks), job),  # type: ignore[index]
        "bookmarks": [b.model_dump() for b in bookmarks],
    }


@router.get("/{lecture_id}", response_model=LectureDetail)
def get_lecture(lecture_id: int, session: Session = Depends(get_session)) -> dict:
    return _detail(session, _get(session, lecture_id))


def _is_busy(lecture: Lecture) -> bool:
    rec = get_recorder()
    return lecture.status == LectureStatus.recording or (
        rec.recording and rec.status().get("lecture_id") == lecture.id
    )


@router.patch("/{lecture_id}", response_model=LectureDetail)
def patch_lecture(
    lecture_id: int, data: LectureUpdate, session: Session = Depends(get_session)
) -> dict:
    lecture = _get(session, lecture_id)
    changes = data.model_dump(exclude_unset=True)
    if "kind" in changes and changes["kind"] not in KINDS:
        raise HTTPException(422, f"kind must be one of {sorted(KINDS)}")
    if "subject_id" in changes and changes["subject_id"] != lecture.subject_id:
        if _is_busy(lecture) or lecture.status == LectureStatus.processing:
            raise HTTPException(409, "Wait until recording and processing have finished")
        subject = session.get(Subject, changes["subject_id"])
        if subject is None:
            raise HTTPException(404, "Subject not found")
        move_lecture(lecture, subject)
    if "title" in changes:
        lecture.title = changes["title"].strip()
        lecture.meta = {**lecture.meta, "title_is_default": False}
    if "kind" in changes:
        lecture.kind = changes["kind"]
    session.add(lecture)
    session.commit()
    session.refresh(lecture)
    return _detail(session, lecture)


@router.delete("/{lecture_id}", status_code=204)
def delete_lecture(lecture_id: int, session: Session = Depends(get_session)) -> Response:
    lecture = _get(session, lecture_id)
    if _is_busy(lecture):
        raise HTTPException(409, "Stop the recording first")
    running = session.exec(
        select(Job).where(Job.lecture_id == lecture_id, Job.status == JobStatus.running)
    ).first()
    if running:
        raise HTTPException(409, "A job is still running for this lecture")
    for job in session.exec(
        select(Job).where(Job.lecture_id == lecture_id, Job.status == JobStatus.queued)
    ).all():
        job.status = JobStatus.cancelled
        session.add(job)
    trash_lecture_folder(lecture)
    remove_lecture_index(session, lecture_id)
    for bm in session.exec(select(Bookmark).where(Bookmark.lecture_id == lecture_id)).all():
        session.delete(bm)
    session.delete(lecture)
    session.commit()
    return Response(status_code=204)


@router.get("/{lecture_id}/audio")
def lecture_audio(lecture_id: int, session: Session = Depends(get_session)) -> FileResponse:
    lecture = _get(session, lecture_id)
    if not lecture.media_path or not abs_path(lecture.media_path).exists():
        raise HTTPException(404, "No audio yet")
    # FileResponse handles HTTP Range requests, so the browser can seek.
    return FileResponse(abs_path(lecture.media_path), media_type="audio/mp4")


@router.post("/{lecture_id}/bookmarks", response_model=BookmarkRead, status_code=201)
def add_bookmark(
    lecture_id: int, data: BookmarkCreate, session: Session = Depends(get_session)
) -> Bookmark:
    _get(session, lecture_id)
    bm = Bookmark(lecture_id=lecture_id, **data.model_dump())
    session.add(bm)
    session.commit()
    session.refresh(bm)
    return bm


@router.delete("/{lecture_id}/bookmarks/{bookmark_id}", status_code=204)
def delete_bookmark(
    lecture_id: int, bookmark_id: int, session: Session = Depends(get_session)
) -> Response:
    bm = session.get(Bookmark, bookmark_id)
    if bm is None or bm.lecture_id != lecture_id:
        raise HTTPException(404, "Bookmark not found")
    session.delete(bm)
    session.commit()
    return Response(status_code=204)


class SegmentRead(BaseModel):
    id: int
    start: float
    end: float
    text: str
    confidence: float | None


class TranscriptRead(BaseModel):
    lecture_id: int
    language: str | None
    segments: list[SegmentRead]


class TranscribeRequest(BaseModel):
    language: str | None = (
        None  # force a language for this run ("en", "de"); None = subject default
    )


@router.get("/{lecture_id}/transcript", response_model=TranscriptRead)
def get_transcript(lecture_id: int, session: Session = Depends(get_session)) -> dict:
    lecture = _get(session, lecture_id)
    segments = session.exec(
        select(TranscriptSegment)
        .where(TranscriptSegment.lecture_id == lecture_id)
        .order_by(col(TranscriptSegment.idx))
    ).all()
    return {"lecture_id": lecture_id, "language": lecture.language, "segments": segments}


@router.post("/{lecture_id}/transcribe", response_model=LectureDetail, status_code=202)
def retranscribe(
    lecture_id: int, req: TranscribeRequest, session: Session = Depends(get_session)
) -> dict:
    lecture = _get(session, lecture_id)
    if not lecture.media_path or not abs_path(lecture.media_path).exists():
        raise HTTPException(409, "This lecture has no audio yet")
    if _active_jobs(session, [lecture_id]):
        raise HTTPException(409, "This lecture is already being processed")
    lecture.stages = {**lecture.stages, "transcribe": "pending"}
    lecture.status = LectureStatus.processing
    lecture.error = None
    session.add(lecture)
    session.commit()
    payload = {"language": req.language} if req.language else {}
    jobs_mod.enqueue(session, TRANSCRIBE_JOB, lecture_id=lecture_id, payload=payload)
    bus.publish("lecture", {"id": lecture_id, "subject_id": lecture.subject_id})
    session.refresh(lecture)
    return _detail(session, lecture)
