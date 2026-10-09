"""Exam simulator endpoints: options, create, sit (start / autosave / submit), results, history."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from sqlmodel import Session, col, func, select

from backend.ai.providers import is_configured
from backend.core.db import get_session
from backend.core.models import Exam, ExamItem, ExamStatus, Question, Subject, utcnow
from backend.pipeline.past_exams import analysed_exams, points_per_minute
from backend.pipeline.subject_hub import current_hub
from backend.study import exam as svc

router = APIRouter(prefix="/api/exams", tags=["exams"])


class CreateExam(BaseModel):
    subject_id: int
    duration_min: int = Field(ge=5, le=300)
    topics: list[str] = Field(default_factory=list)
    types: list[str] = Field(default_factory=list)
    difficulty: str = "exam"
    source: str = "generate"
    title: str = ""


class SaveAnswer(BaseModel):
    answer_text: str | None = Field(default=None, max_length=50_000)
    selected: list[int] | None = None
    flagged: bool | None = None


def _exam(s: Session, exam_id: int) -> Exam:
    exam = s.get(Exam, exam_id)
    if exam is None:
        raise HTTPException(404, "Exam not found")
    return exam


def _error(e: svc.ExamError) -> HTTPException:
    return HTTPException(404 if "not found" in str(e) else 409, str(e))


def _summary(s: Session, exam: Exam) -> dict[str, Any]:
    subject = s.get(Subject, exam.subject_id)
    items = s.exec(select(func.count()).where(ExamItem.exam_id == exam.id)).one()
    return {
        **exam.model_dump(exclude={"config"}),
        "topics": exam.config.get("topics", []),
        "types": exam.config.get("types", []),
        "difficulty": exam.config.get("difficulty", "exam"),
        "source": exam.config.get("source", "generate"),
        "subject_name": subject.name if subject else "",
        "subject_color": subject.color if subject else "#888",
        "items": items,
        "remaining_s": svc.time_left(exam),
    }


def _question(q: dict[str, Any], reveal: bool, numbers: dict[int, int]) -> dict[str, Any]:
    out = {
        k: q.get(k)
        for k in ("type", "title", "bloom", "difficulty", "points", "prompt", "topic", "emphasized")
    }
    if not reveal:  # while sitting the exam: no answers, no rubric
        out["options"] = [{"text": o.get("text", "")} for o in q.get("options") or []]
        return out
    out["options"] = q.get("options") or []
    for k in ("answer", "explanation", "rubric", "solution_steps", "expected_output"):
        out[k] = q.get(k)
    out["sources"] = [
        {"label": svc.ref_label(numbers, r), "lecture_id": r["lecture_id"], "start_s": r["start_s"]}
        for r in q.get("source_refs") or []
        if svc.ref_label(numbers, r)
    ]
    return out


def _detail(s: Session, exam: Exam) -> dict[str, Any]:
    reveal = exam.submitted_at is not None
    numbers = svc.lecture_numbers(s, exam.subject_id)
    items = []
    for i in svc.items_of(s, exam.id):  # type: ignore[arg-type]
        row = {
            "id": i.id,
            "idx": i.idx,
            "question": _question(i.question, reveal, numbers),
            "answer_text": i.answer_text,
            "selected": i.selected,
            "flagged": i.flagged,
            "max_points": i.max_points,
        }
        if reveal:
            row |= {
                "score": i.score,
                "rubric_awarded": i.rubric_awarded,
                "feedback": i.feedback,
                "graded_by": i.graded_by,
            }
        items.append(row)
    return {**_summary(s, exam), "items": items, "item_count": len(items)}


@router.get("/options")
def exam_options(subject_id: int, session: Session = Depends(get_session)) -> dict[str, Any]:
    """What the "New exam" form offers for a subject."""
    if session.get(Subject, subject_id) is None:
        raise HTTPException(404, "Subject not found")
    hub = current_hub(session, subject_id)
    topics: list[dict[str, Any]] = []
    if hub is not None:
        from backend.study.topics import topics_with_mastery

        seen: set[str] = set()
        for t in topics_with_mastery(session, subject_id, hub):
            if t["name"].casefold() in seen:  # picked by name, so show each name once
                continue
            seen.add(t["name"].casefold())
            topics.append(
                {
                    "name": t["name"],
                    "exam_relevant": t["exam_relevant"],
                    "mastery": t["mastery"],
                    "questions": len(t["question_ids"]),
                }
            )
    past = analysed_exams(session, subject_id)
    durations = sorted(e.duration_min for e in past if e.duration_min)
    bank = dict(
        session.exec(
            select(Question.type, func.count())
            .where(
                Question.subject_id == subject_id,
                Question.kind == "exam",
                col(Question.is_current),
            )
            .group_by(Question.type)
        ).all()
    )
    return {
        "topics": topics,
        "past_exams": [
            {"title": e.title, "duration_min": e.duration_min, "total_points": e.total_points}
            for e in past
        ],
        "suggested_duration": durations[len(durations) // 2] if durations else 90,
        "points_per_min": points_per_minute(past) or svc.DEFAULT_POINTS_PER_MIN,
        "bank": bank,
        "llm_configured": is_configured(),
    }


@router.get("")
def list_exams(
    subject_id: int | None = None, limit: int = 50, session: Session = Depends(get_session)
) -> list[dict[str, Any]]:
    q = select(Exam).order_by(col(Exam.created_at).desc(), col(Exam.id).desc())
    q = q.limit(min(limit, 200))
    if subject_id:
        q = q.where(Exam.subject_id == subject_id)
    out = []
    for exam in session.exec(q).all():
        svc.check_deadline(session, exam)
        out.append(_summary(session, exam))
    return out


@router.post("", status_code=201)
def create_exam(req: CreateExam, session: Session = Depends(get_session)) -> dict[str, Any]:
    if req.source == "generate" and not is_configured():
        raise HTTPException(409, "Add an Anthropic API key on the Settings page first")
    try:
        exam = svc.create_exam(session, **req.model_dump())
    except svc.ExamError as e:
        raise HTTPException(404 if "not found" in str(e) else 422, str(e)) from e
    return _detail(session, exam)


@router.get("/{exam_id}")
def get_exam(exam_id: int, session: Session = Depends(get_session)) -> dict[str, Any]:
    exam = svc.check_deadline(session, _exam(session, exam_id))
    return _detail(session, exam)


@router.post("/{exam_id}/start")
def start_exam(exam_id: int, session: Session = Depends(get_session)) -> dict[str, Any]:
    try:
        exam = svc.start(session, _exam(session, exam_id))
    except svc.ExamError as e:
        raise _error(e) from e
    return _detail(session, exam)


@router.patch("/{exam_id}/items/{item_id}")
def save_answer(
    exam_id: int, item_id: int, req: SaveAnswer, session: Session = Depends(get_session)
) -> dict[str, Any]:
    exam = _exam(session, exam_id)
    item = session.get(ExamItem, item_id)
    if item is None or item.exam_id != exam_id:
        raise HTTPException(404, "Question not found in this exam")
    try:
        item = svc.save_answer(session, exam, item, **req.model_dump())
    except svc.ExamError as e:
        raise _error(e) from e
    return {"id": item.id, "saved_at": utcnow(), "remaining_s": svc.time_left(exam)}


@router.post("/{exam_id}/submit")
def submit_exam(exam_id: int, session: Session = Depends(get_session)) -> dict[str, Any]:
    exam = _exam(session, exam_id)
    try:
        svc.submit(session, exam)
    except svc.ExamError as e:
        raise _error(e) from e
    return _detail(session, exam)


@router.post("/{exam_id}/retry", status_code=202)
def retry_exam(exam_id: int, session: Session = Depends(get_session)) -> dict[str, Any]:
    exam = svc.check_deadline(session, _exam(session, exam_id))
    if not is_configured():
        raise HTTPException(409, "Add an Anthropic API key on the Settings page first")
    try:
        svc.retry(session, exam)
    except svc.ExamError as e:
        raise _error(e) from e
    return _summary(session, exam)


@router.delete("/{exam_id}", status_code=204)
def delete_exam(exam_id: int, session: Session = Depends(get_session)) -> Response:
    exam = svc.check_deadline(session, _exam(session, exam_id))
    if exam.status in (ExamStatus.building, ExamStatus.grading):
        raise HTTPException(409, "Wait until the exam is built or graded")
    svc.delete_exam(session, exam)
    return Response(status_code=204)
