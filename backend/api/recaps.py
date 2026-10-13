"""Audio recaps of lectures and weeks."""

from __future__ import annotations

from datetime import date
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlmodel import Session, col, select

from backend.core.config import get_settings
from backend.core.db import get_session
from backend.core.library import abs_path
from backend.core.models import Recap
from backend.core.state import load_state, update_state
from backend.pipeline import recap as rc

router = APIRouter(tags=["recaps"])


class RecapRequest(BaseModel):
    scope: Literal["lecture", "week"]
    lecture_id: int | None = None
    subject_id: int | None = None
    week_start: date | None = None
    minutes: int = Field(default=7, ge=3, le=15)


class RecapSettings(BaseModel):
    auto_lecture: bool | None = None
    minutes: int | None = Field(default=None, ge=3, le=15)


def apply_saved_settings(settings) -> None:  # type: ignore[no-untyped-def]
    saved = load_state().get("recap") or {}
    for k in ("auto_lecture", "minutes"):
        if k in saved:
            setattr(settings.recap, k, saved[k])


@router.get("/api/recaps")
def list_recaps(
    lecture_id: int | None = None,
    subject_id: int | None = None,
    session: Session = Depends(get_session),
) -> list[dict[str, Any]]:
    q = select(Recap).order_by(col(Recap.created_at).desc()).limit(100)
    if lecture_id:
        q = q.where(Recap.lecture_id == lecture_id)
    if subject_id:
        q = q.where(Recap.subject_id == subject_id)
    return [rc.recap_dict(r) for r in session.exec(q).all()]


@router.get("/api/recaps/weeks")
def recap_weeks(session: Session = Depends(get_session)) -> list[dict[str, Any]]:
    return rc.weeks(session)


@router.post("/api/recaps", status_code=201)
def create_recap(req: RecapRequest, session: Session = Depends(get_session)) -> dict[str, Any]:
    try:
        r = rc.create(
            session,
            req.scope,
            req.minutes,
            lecture_id=req.lecture_id,
            subject_id=req.subject_id,
            week_start=req.week_start,
        )
    except rc.RecapError as e:
        raise HTTPException(422, str(e)) from e
    return rc.recap_dict(r)


def _get(session: Session, recap_id: int) -> Recap:
    r = session.get(Recap, recap_id)
    if r is None:
        raise HTTPException(404, "Recap not found")
    return r


@router.get("/api/recaps/{recap_id}")
def get_recap(recap_id: int, session: Session = Depends(get_session)) -> dict[str, Any]:
    return rc.recap_dict(_get(session, recap_id))


@router.get("/api/recaps/{recap_id}/audio")
def recap_audio(
    recap_id: int, download: bool = False, session: Session = Depends(get_session)
) -> FileResponse:
    r = _get(session, recap_id)
    if not r.audio_path or not abs_path(r.audio_path).is_file():
        raise HTTPException(404, "No audio yet")
    name = "".join(c if c.isalnum() or c in " -_" else "_" for c in (r.title or "recap"))[:80]
    return FileResponse(
        abs_path(r.audio_path),
        media_type="audio/mp4",
        filename=f"{name.strip() or 'recap'}.m4a" if download else None,
    )


@router.delete("/api/recaps/{recap_id}", status_code=204)
def delete_recap(recap_id: int, session: Session = Depends(get_session)) -> None:
    rc.delete(session, _get(session, recap_id))


# PC only: the LAN guard blocks writes under /api/settings.
@router.get("/api/settings/recap")
def recap_settings() -> dict[str, Any]:
    cfg = get_settings().recap
    return {"auto_lecture": cfg.auto_lecture, "minutes": cfg.minutes}


@router.put("/api/settings/recap")
def put_recap_settings(req: RecapSettings) -> dict[str, Any]:
    cfg = get_settings().recap
    for k, v in req.model_dump(exclude_none=True).items():
        setattr(cfg, k, v)
    update_state(recap={"auto_lecture": cfg.auto_lecture, "minutes": cfg.minutes})
    return recap_settings()
