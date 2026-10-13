"""Deadlines (review list, manual items), the study plan and the .ics calendar."""

from __future__ import annotations

from datetime import date
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from sqlmodel import Session

from backend.core.db import get_session
from backend.exports.calendar import build_ics
from backend.study import deadlines as dl
from backend.study import planner

router = APIRouter(prefix="/api", tags=["planner"])

Kind = Literal["deadline", "exam", "assignment", "admin", "other"]


class DeadlineIn(BaseModel):
    title: str = Field(min_length=1, max_length=500)
    kind: Kind = "deadline"
    subject_id: int | None = None
    due_date: date | None = None
    due_time: str = ""


class DeadlinePatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=500)
    kind: Kind | None = None
    subject_id: int | None = None
    due_date: date | None = None
    due_time: str | None = None
    status: Literal["suggested", "open", "done", "dismissed"] | None = None


class ItemPatch(BaseModel):
    done: bool


class PlannerSettings(BaseModel):
    minutes_per_day: int | None = Field(default=None, ge=15, le=600)
    weekdays: list[int] | None = None
    start_time: str | None = None
    block_min: int | None = Field(default=None, ge=15, le=120)


@router.get("/deadlines")
def list_deadlines(session: Session = Depends(get_session)) -> dict[str, Any]:
    return dl.listing(session)


@router.post("/deadlines", status_code=201)
def add_deadline(req: DeadlineIn, session: Session = Depends(get_session)) -> dict[str, Any]:
    try:
        d = dl.create(session, req.model_dump())
    except dl.DeadlineError as e:
        raise HTTPException(422, str(e)) from e
    return {"id": d.id}


@router.patch("/deadlines/{deadline_id}")
def patch_deadline(
    deadline_id: int, req: DeadlinePatch, session: Session = Depends(get_session)
) -> dict[str, Any]:
    try:
        d = dl.update(session, deadline_id, req.model_dump(exclude_unset=True))
    except dl.DeadlineError as e:
        raise HTTPException(404 if "not found" in str(e) else 422, str(e)) from e
    return {"id": d.id, "status": d.status}


@router.delete("/deadlines/{deadline_id}", status_code=204)
def delete_deadline(deadline_id: int, session: Session = Depends(get_session)) -> None:
    try:
        dl.delete(session, deadline_id)
    except dl.DeadlineError as e:
        raise HTTPException(404, str(e)) from e


@router.get("/planner")
def get_plan(days: int = 21, session: Session = Depends(get_session)) -> dict[str, Any]:
    return planner.plan(session, days=max(1, min(days, 120)))


@router.post("/planner/rebuild")
def rebuild(session: Session = Depends(get_session)) -> dict[str, int]:
    return {"items": planner.build(session)}


@router.patch("/planner/items/{item_id}")
def patch_item(
    item_id: int, req: ItemPatch, session: Session = Depends(get_session)
) -> dict[str, Any]:
    try:
        return planner.set_done(session, item_id, req.done)
    except planner.PlannerError as e:
        raise HTTPException(404, str(e)) from e


@router.put("/planner/settings")
def put_settings(req: PlannerSettings, session: Session = Depends(get_session)) -> dict[str, Any]:
    try:
        return planner.update_settings(session, req.model_dump(exclude_none=True))
    except planner.PlannerError as e:
        raise HTTPException(422, str(e)) from e


@router.get("/calendar.ics")
def calendar(
    plan: bool = True,
    deadlines: bool = True,
    exams: bool = True,
    download: bool = False,
    session: Session = Depends(get_session),
) -> Response:
    headers = {"Content-Disposition": 'attachment; filename="lecturelab.ics"'} if download else {}
    return Response(
        build_ics(session, plan=plan, deadlines=deadlines, exams=exams),
        media_type="text/calendar; charset=utf-8",
        headers=headers,
    )
