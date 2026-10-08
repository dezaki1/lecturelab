"""Flashcard review (FSRS) and question practice endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlmodel import Session, col, func, select

from backend.api.materials import CardRead, QuestionRead
from backend.core.db import get_session
from backend.core.events import bus
from backend.core.models import Attempt, Flashcard, Lecture, Question, ReviewLog, Subject
from backend.search.retrieve import lecture_numbers
from backend.study import practice as practice_svc
from backend.study import review as review_svc

router = APIRouter(prefix="/api", tags=["study"])


class ReviewRequest(BaseModel):
    rating: int = Field(ge=1, le=4)
    duration_ms: int | None = Field(default=None, ge=0)


class ExtraNew(BaseModel):
    count: int = Field(default=10, ge=1, le=200)


class AttemptRequest(BaseModel):
    selected: list[int] | None = None
    answer_text: str = ""
    rubric_checked: list[int] | None = None
    self_score: float | None = None
    duration_ms: int | None = Field(default=None, ge=0)


def _context(s: Session, subject_id: int, lecture_id: int | None) -> dict[str, Any]:
    subject = s.get(Subject, subject_id)
    lecture = s.get(Lecture, lecture_id) if lecture_id else None
    number = lecture_numbers(s, {subject_id}).get(lecture_id) if lecture_id else None  # type: ignore[arg-type]
    return {
        "subject_name": subject.name if subject else "",
        "subject_color": subject.color if subject else "#888",
        "lecture_number": number,
        "lecture_title": lecture.title if lecture else "",
    }


def _card_payload(s: Session, card: Flashcard) -> dict[str, Any]:
    reviews = s.exec(
        select(func.count()).select_from(ReviewLog).where(ReviewLog.card_id == card.id)
    ).one()
    return {
        "card": CardRead.model_validate(card.model_dump()).model_dump(),
        **_context(s, card.subject_id, card.lecture_id),
        "lecture_id": card.lecture_id,
        "is_new": not card.fsrs,
        "reviews": reviews,
        # Reversed cards alternate direction between reviews.
        "reverse": card.type == "reversed" and reviews % 2 == 1,
        "intervals": review_svc.preview_intervals(card),
    }


@router.get("/review/summary")
def review_summary(session: Session = Depends(get_session)) -> dict[str, Any]:
    return review_svc.stats(session)


@router.get("/review/next")
def review_next(
    subject_id: int | None = None, session: Session = Depends(get_session)
) -> dict[str, Any]:
    card = review_svc.next_card(session, subject_id)
    remaining = review_svc.counts(session, subject_id)
    if card is None:
        return {"card": None, "remaining": remaining}
    return {**_card_payload(session, card), "remaining": remaining}


@router.post("/review/{card_id:int}")
def review_card(
    card_id: int, req: ReviewRequest, session: Session = Depends(get_session)
) -> dict[str, Any]:
    try:
        card = review_svc.review(session, card_id, req.rating, req.duration_ms)
    except review_svc.ReviewError as e:
        raise HTTPException(404 if "not found" in str(e) else 422, str(e)) from e
    bus.publish("review", {"card_id": card_id})
    return {"card_id": card.id, "due": card.due, "state": card.fsrs.get("state")}


@router.post("/review/undo")
def review_undo(session: Session = Depends(get_session)) -> dict[str, Any]:
    card = review_svc.undo_last(session)
    if card is None:
        raise HTTPException(409, "Nothing to undo today")
    bus.publish("review", {"card_id": card.id})
    return {"card_id": card.id}


@router.post("/review/extra-new")
def review_extra_new(req: ExtraNew, session: Session = Depends(get_session)) -> dict[str, Any]:
    review_svc.add_extra_new(req.count)
    return review_svc.counts(session)


@router.get("/practice/questions")
def practice_questions(
    subject_id: int | None = None,
    lecture_id: int | None = None,
    kind: str = "exam",
    mode: str = "mixed",
    limit: int = 10,
    session: Session = Depends(get_session),
) -> list[dict[str, Any]]:
    if kind not in ("exam", "exercise") or mode not in ("mixed", "weakest"):
        raise HTTPException(422, "kind must be exam|exercise, mode mixed|weakest")
    picked = practice_svc.pick_questions(
        session,
        subject_id=subject_id,
        lecture_id=lecture_id,
        kind=kind,
        mode=mode,
        limit=min(limit, 50),
    )
    rows = {
        q.id: q
        for q in session.exec(
            select(Question).where(col(Question.id).in_([p["id"] for p in picked]))
        ).all()
    }
    out = []
    for p in picked:
        q = rows[p["id"]]
        out.append(
            {
                **p,
                "question": QuestionRead.model_validate(q.model_dump()).model_dump(),
                "lecture_id": q.lecture_id,
                **_context(session, q.subject_id, q.lecture_id),
            }
        )
    return out


@router.post("/practice/questions/{question_id}/attempts")
def practice_attempt(
    question_id: int, req: AttemptRequest, session: Session = Depends(get_session)
) -> dict[str, Any]:
    try:
        a = practice_svc.submit(session, question_id, **req.model_dump())
    except practice_svc.PracticeError as e:
        raise HTTPException(404 if "not found" in str(e) else 422, str(e)) from e
    q = session.get(Question, question_id)
    assert q is not None
    return {
        "attempt_id": a.id,
        "correct": a.correct,
        "score": a.score,
        "max_points": a.max_points,
        "auto_graded": a.auto_graded,
        "correct_options": [i for i, o in enumerate(q.options) if o.get("correct")],
    }


@router.get("/practice/stats")
def practice_stats(session: Session = Depends(get_session)) -> dict[str, Any]:
    rows = session.exec(
        select(
            Question.subject_id,
            func.count(col(Attempt.id)),
            func.sum(Attempt.score),
            func.sum(Attempt.max_points),
        )
        .join(Question, col(Question.id) == col(Attempt.question_id))
        .group_by(Question.subject_id)
    ).all()
    return {
        "by_subject": [
            {"subject_id": sid, "attempts": n, "score": round(sc or 0, 1), "max": round(mx or 0, 1)}
            for sid, n, sc, mx in rows
        ]
    }
