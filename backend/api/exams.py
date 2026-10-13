"""Exam simulator: past-exam profile, create/take/submit exams, results and history."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from sqlmodel import Session, col, select

from backend.ai.providers import is_configured
from backend.core.db import get_session
from backend.core.events import bus
from backend.core.models import Exam, ExamAnswer, Question, Subject, utcnow
from backend.study import exams as ex

router = APIRouter(prefix="/api", tags=["exams"])


class ExamConfig(BaseModel):
    title: str = ""
    topics: list[str] = Field(default_factory=list)
    types: list[str] = Field(default_factory=list)
    difficulty: Literal["mixed", "easy", "medium", "hard"] = "mixed"
    duration_min: int | None = Field(default=None, ge=5, le=300)
    target_points: float | None = Field(default=None, gt=0, le=500)
    allow_new: bool = True


class AnswerUpdate(BaseModel):
    selected: list[int] | None = None
    answer_text: str | None = None


def _exam(s: Session, exam_id: int) -> Exam:
    exam = s.get(Exam, exam_id)
    if exam is None:
        raise HTTPException(404, "Exam not found")
    return exam


def _summary(e: Exam) -> dict[str, Any]:
    return {
        "id": e.id,
        "subject_id": e.subject_id,
        "title": e.title,
        "status": e.status,
        "error": e.error,
        "config": e.config,
        "time_limit_s": e.time_limit_s,
        "total_points": e.total_points,
        "score": e.score,
        "created_at": e.created_at,
        "started_at": e.started_at,
        "submitted_at": e.submitted_at,
        "graded_at": e.graded_at,
    }


def _detail(s: Session, e: Exam) -> dict[str, Any]:
    graded = e.status == "graded"
    answers = s.exec(
        select(ExamAnswer).where(ExamAnswer.exam_id == e.id).order_by(col(ExamAnswer.position))
    ).all()
    items = []
    for a in answers:
        q = s.get(Question, a.question_id)
        if q is None:
            continue
        question: dict[str, Any] = {
            "id": q.id,
            "type": q.type,
            "title": q.title,
            "points": q.points,
            "prompt": q.prompt,
            # Before grading only the option texts: which are correct stays hidden.
            "options": q.options if graded else [{"text": o.get("text", "")} for o in q.options],
        }
        if graded:
            question |= {
                "answer": q.answer,
                "explanation": q.explanation,
                "rubric": q.rubric,
                "source_refs": q.source_refs,
                "lecture_id": q.lecture_id,
            }
        items.append(
            {
                "id": a.id,
                "position": a.position,
                "selected": a.selected,
                "answer_text": a.answer_text,
                "score": a.score if graded else None,
                "max_points": a.max_points,
                "feedback": a.feedback if graded else "",
                "rubric_scores": a.rubric_scores if graded else [],
                "graded_by": a.graded_by if graded else "",
                "question": question,
            }
        )
    end = ex.deadline(e)
    return {
        **_summary(e),
        "summary_md": e.summary_md if graded else "",
        "revise": e.revise if graded else [],
        "deadline": end,
        "server_now": utcnow(),
        "answers": items,
    }


@router.get("/subjects/{subject_id}/exam-profile")
def get_profile(subject_id: int, session: Session = Depends(get_session)) -> dict[str, Any]:
    if session.get(Subject, subject_id) is None:
        raise HTTPException(404, "Subject not found")
    docs = ex.past_exams(session, subject_id)
    bank = session.exec(
        select(Question.type).where(
            Question.subject_id == subject_id, Question.kind == "exam", col(Question.is_current)
        )
    ).all()
    counts: dict[str, int] = {}
    for t in bank:
        counts[t] = counts.get(t, 0) + 1
    return {
        "profile": ex.exam_profile(session, subject_id),
        "past_exams": [
            {
                "id": m.id,
                "title": m.analysis.get("title") or m.title,
                "questions": len(m.analysis.get("questions") or []),
                "analysed": bool(m.analysis.get("questions")),
                "has_text": bool(m.extracted_text.strip()),
            }
            for m in docs
        ],
        "bank": counts,
        "can_generate": is_configured(),
    }


@router.post("/subjects/{subject_id}/exams", status_code=201)
def create(subject_id: int, cfg: ExamConfig, session: Session = Depends(get_session)) -> dict:
    try:
        exam = ex.create_exam(session, subject_id, cfg.model_dump())
    except ex.ExamError as e:
        raise HTTPException(404, str(e)) from e
    bus.publish("exam", {"id": exam.id, "status": exam.status})
    return _summary(exam)


@router.get("/exams")
def history(subject_id: int | None = None, session: Session = Depends(get_session)) -> list[dict]:
    q = select(Exam).order_by(col(Exam.id).desc())
    if subject_id:
        q = q.where(Exam.subject_id == subject_id)
    return [_summary(e) for e in session.exec(q).all()]


@router.get("/exams/{exam_id}")
def get_exam(exam_id: int, session: Session = Depends(get_session)) -> dict[str, Any]:
    return _detail(session, _exam(session, exam_id))


@router.post("/exams/{exam_id}/start")
def start(exam_id: int, session: Session = Depends(get_session)) -> dict[str, Any]:
    try:
        exam = ex.start(session, _exam(session, exam_id))
    except ex.ExamError as e:
        raise HTTPException(409, str(e)) from e
    return _detail(session, exam)


@router.put("/exams/{exam_id}/answers/{answer_id}")
def save_answer(
    exam_id: int, answer_id: int, req: AnswerUpdate, session: Session = Depends(get_session)
) -> dict[str, Any]:
    try:
        a = ex.save_answer(
            session, _exam(session, exam_id), answer_id, req.selected, req.answer_text
        )
    except ex.ExamError as e:
        raise HTTPException(409, str(e)) from e
    return {"id": a.id, "updated_at": a.updated_at}


@router.post("/exams/{exam_id}/submit")
def submit(exam_id: int, session: Session = Depends(get_session)) -> dict[str, Any]:
    try:
        exam = ex.submit(session, _exam(session, exam_id))
    except ex.ExamError as e:
        raise HTTPException(409, str(e)) from e
    bus.publish("exam", {"id": exam.id, "status": exam.status})
    return _detail(session, exam)


@router.post("/exams/{exam_id}/grade", status_code=202)
def regrade(exam_id: int, session: Session = Depends(get_session)) -> dict[str, Any]:
    """Retry grading after a failure."""
    exam = _exam(session, exam_id)
    if exam.status != "grading_failed":
        raise HTTPException(409, "Only a failed grading can be retried")
    exam.status = "grading"
    session.add(exam)
    session.commit()
    from backend.core import jobs

    jobs.enqueue(
        session, ex.EXAM_GRADE_JOB, payload={"exam_id": exam.id, "subject_id": exam.subject_id}
    )
    return _summary(exam)


@router.delete("/exams/{exam_id}", status_code=204)
def delete(exam_id: int, session: Session = Depends(get_session)) -> Response:
    exam = _exam(session, exam_id)
    if exam.status in ("building", "grading"):
        raise HTTPException(409, "Wait until the exam is ready or graded")
    session.delete(exam)  # answers cascade; attempts keep their score (exam_id set to NULL)
    session.commit()
    return Response(status_code=204)
