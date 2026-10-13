"""Weak spots per subject and overall, and targeted practice sets."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlmodel import Session

from backend.core.db import get_session
from backend.core.models import PracticeSet, Subject
from backend.study import weak

router = APIRouter(prefix="/api", tags=["weak"])


class PracticeSetRequest(BaseModel):
    topics: list[str] = Field(default_factory=list)  # empty = the weakest topics
    n: int = Field(default=6, ge=2, le=15)


@router.get("/subjects/{subject_id}/weak-spots")
def subject_weak_spots(subject_id: int, session: Session = Depends(get_session)) -> dict[str, Any]:
    if session.get(Subject, subject_id) is None:
        raise HTTPException(404, "Subject not found")
    return {
        "topics": weak.topic_stats(session, subject_id),
        "window_days": weak.WINDOW_DAYS,
        "weak_below": weak.WEAK_BELOW,
    }


@router.get("/weak-spots")
def weak_spots(limit: int = 8, session: Session = Depends(get_session)) -> list[dict[str, Any]]:
    return weak.weak_overview(session, limit=min(limit, 30))


@router.post("/subjects/{subject_id}/practice-sets", status_code=201)
def create_set(
    subject_id: int, req: PracticeSetRequest, session: Session = Depends(get_session)
) -> dict[str, Any]:
    if session.get(Subject, subject_id) is None:
        raise HTTPException(404, "Subject not found")
    try:
        ps = weak.create_practice_set(session, subject_id, req.topics, req.n)
    except weak.WeakError as e:
        raise HTTPException(409, str(e)) from e
    return ps.model_dump()


@router.get("/practice-sets/{set_id}")
def get_set(set_id: int, session: Session = Depends(get_session)) -> dict[str, Any]:
    ps = session.get(PracticeSet, set_id)
    if ps is None:
        raise HTTPException(404, "Practice set not found")
    return ps.model_dump()
