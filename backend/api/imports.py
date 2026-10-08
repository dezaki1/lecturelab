"""Upload endpoint for importing recordings.

The browser sends the file as the raw request body (not multipart) so multi-GB videos stream
straight to disk without buffering or an extra dependency.
"""

from __future__ import annotations

import shutil
import uuid
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from backend.api.lectures import KINDS, LectureRead, lecture_read
from backend.core import jobs
from backend.core.config import get_settings
from backend.core.db import session_scope
from backend.core.events import bus
from backend.core.library import create_lecture, lecture_dir
from backend.core.media import probe
from backend.core.models import LectureSource, LectureStatus, Subject
from backend.pipeline.ingest import (
    IMPORT_EXTENSIONS,
    IMPORT_JOB,
    guess_started_at,
    move_upload,
    title_from_filename,
)

router = APIRouter(prefix="/api/imports", tags=["imports"])


@router.post("", response_model=LectureRead, status_code=201)
async def import_file(
    request: Request,
    subject_id: int,
    filename: str,
    title: str | None = None,
    kind: str = "",
    last_modified_ms: int | None = None,
) -> dict:
    settings = get_settings()
    ext = Path(filename).suffix.lower()
    if ext not in IMPORT_EXTENSIONS:
        allowed = ", ".join(sorted(e.lstrip(".") for e in IMPORT_EXTENSIONS))
        raise HTTPException(415, f"Unsupported file type {ext or '(none)'}. Supported: {allowed}")
    if kind not in KINDS:
        raise HTTPException(422, f"kind must be one of {sorted(KINDS)}")
    with session_scope() as s:
        if s.get(Subject, subject_id) is None:
            raise HTTPException(404, "Subject not found")

    size = int(request.headers.get("content-length") or 0)
    free = shutil.disk_usage(settings.data_dir).free
    if size + settings.recorder.min_free_gb * 1e9 > free:
        raise HTTPException(
            507, f"Not enough disk space: the file is {size / 1e9:.1f} GB, {free / 1e9:.1f} GB free"
        )

    tmp = settings.tmp_dir / f"upload-{uuid.uuid4().hex}{ext}"
    try:
        written = 0
        with open(tmp, "wb") as f:
            async for chunk in request.stream():
                f.write(chunk)
                written += len(chunk)
        if written == 0:
            raise HTTPException(400, "The file is empty")
        try:
            info = await run_in_threadpool(probe, tmp)
        except jobs.JobError as e:
            raise HTTPException(422, str(e)) from e
        if not info.has_audio:
            raise HTTPException(422, "This file has no audio track")

        last_modified = (
            datetime.fromtimestamp(last_modified_ms / 1000, UTC) if last_modified_ms else None
        )
        with session_scope() as s:
            subject = s.get(Subject, subject_id)
            assert subject is not None
            lecture = create_lecture(
                s,
                subject,
                source=LectureSource.import_,
                status=LectureStatus.processing,
                title=(title or "").strip() or title_from_filename(filename),
                title_is_default=not (title or "").strip()
                or (title or "").strip() == title_from_filename(filename),
                kind=kind,
                started_at=guess_started_at(info, last_modified),
            )
            lecture.duration_s = info.duration
            s.add(lecture)
            s.commit()
            source = move_upload(tmp, lecture_dir(lecture), ext)
            jobs.enqueue(s, IMPORT_JOB, lecture_id=lecture.id, payload={"source": source})
            result = lecture_read(s, lecture)
        bus.publish("lecture", {"id": result["id"], "subject_id": subject_id})
        return result
    finally:
        tmp.unlink(missing_ok=True)
