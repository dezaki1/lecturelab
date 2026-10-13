"""Timetable: calendars (link or file), course -> subject mapping, now/next, settings."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlmodel import Session, col, select

from backend.core.config import get_settings
from backend.core.db import get_session
from backend.core.models import TimetableSource
from backend.core.state import update_state
from backend.timetable import service as tt

router = APIRouter(prefix="/api/timetable", tags=["timetable"])

EDITABLE = ("remind_min", "auto_record")


class SourceRequest(BaseModel):
    url: str = Field(min_length=8)
    name: str = ""


class CourseUpdate(BaseModel):
    subject_id: int | None = None  # 0 = unmapped
    ignored: bool | None = None


class TimetableSettings(BaseModel):
    remind_min: int | None = Field(default=None, ge=0, le=60)
    auto_record: bool | None = None


def apply_saved_settings(settings) -> None:  # type: ignore[no-untyped-def]
    from backend.core.state import load_state

    saved = load_state().get("timetable") or {}
    for k in EDITABLE:
        if k in saved:
            setattr(settings.timetable, k, saved[k])


def _settings() -> dict[str, Any]:
    cfg = get_settings().timetable
    return {
        "remind_min": cfg.remind_min,
        "auto_record": cfg.auto_record,
        "sync_hours": cfg.sync_hours,
    }


@router.get("")
def overview(session: Session = Depends(get_session)) -> dict[str, Any]:
    sources = session.exec(select(TimetableSource).order_by(col(TimetableSource.id))).all()
    return {
        "sources": [
            {
                "id": s.id,
                "name": s.name,
                "url": s.url,
                "is_file": bool(s.file_path),
                "last_sync_at": s.last_sync_at,
                "error": s.error,
            }
            for s in sources
        ],
        "courses": tt.courses(session),
        "settings": _settings(),
    }


@router.post("/sources", status_code=201)
def add_link(req: SourceRequest) -> dict[str, Any]:
    try:
        src = tt.add_source(req.name, url=req.url)
    except tt.TimetableError as e:
        raise HTTPException(422, str(e)) from e
    return {"id": src.id}


@router.post("/upload", status_code=201)
async def upload(filename: str, request: Request) -> dict[str, Any]:
    data = await request.body()
    if not data or len(data) > 10_000_000:
        raise HTTPException(413, "Calendar files must be under 10 MB")
    try:
        src = tt.add_source(filename.rsplit(".", 1)[0], data=data)
    except (tt.TimetableError, Exception) as e:
        raise HTTPException(422, str(e)) from e
    return {"id": src.id}


@router.post("/sources/{source_id}/sync")
def sync(source_id: int) -> dict[str, int]:
    try:
        return {"events": tt.sync_source(source_id)}
    except tt.TimetableError as e:
        raise HTTPException(422, str(e)) from e


@router.delete("/sources/{source_id}", status_code=204)
def remove(source_id: int) -> None:
    try:
        tt.remove_source(source_id)
    except tt.TimetableError as e:
        raise HTTPException(404, str(e)) from e


@router.patch("/courses/{course_id}")
def update_course(course_id: int, req: CourseUpdate) -> dict[str, bool]:
    try:
        tt.update_course(course_id, req.subject_id, req.ignored)
    except tt.TimetableError as e:
        raise HTTPException(404, str(e)) from e
    return {"ok": True}


@router.get("/now")
def now(session: Session = Depends(get_session)) -> dict[str, Any]:
    return tt.now_and_next(session)


@router.get("/week")
def week(offset: int = 0, session: Session = Depends(get_session)) -> list[dict[str, Any]]:
    """Events of the week (Monday-Sunday, local time) ``offset`` weeks from this one."""
    local = datetime.now(UTC).astimezone()
    monday = (local - timedelta(days=local.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    start = monday + timedelta(weeks=offset)
    return tt.events_between(
        session, start.astimezone(UTC), (start + timedelta(days=7)).astimezone(UTC)
    )


@router.put("/settings")
def put_settings(req: TimetableSettings) -> dict[str, Any]:
    cfg = get_settings().timetable
    for k, v in req.model_dump(exclude_unset=True).items():
        if v is not None:
            setattr(cfg, k, v)
    update_state(timetable={k: getattr(cfg, k) for k in EDITABLE})
    return _settings()
