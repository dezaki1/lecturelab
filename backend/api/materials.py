"""Generated study material: read, regenerate, edit notes, delete items."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from sqlmodel import Session, col, select

from backend.ai.providers import active_provider_name, is_configured
from backend.core import jobs
from backend.core.db import get_session
from backend.core.events import bus
from backend.core.models import (
    Flashcard,
    Job,
    JobStatus,
    Lecture,
    LectureStatus,
    Note,
    Question,
    utcnow,
)
from backend.pipeline.generate import ALL_KINDS, estimate
from backend.pipeline.stages import GENERATE_JOB
from backend.search.index import INDEX_JOB, queue_index

router = APIRouter(prefix="/api", tags=["materials"])


class NoteVersion(BaseModel):
    id: int
    version: int
    created_at: datetime
    edited_by_me: bool
    model: str
    instructions: str


class NoteRead(BaseModel):
    id: int
    kind: str
    version: int
    is_current: bool
    content_md: str
    data: dict[str, Any]
    source_refs: list[dict[str, Any]]
    edited_by_me: bool
    model: str
    prompt_version: str
    created_at: datetime
    updated_at: datetime
    versions: list[NoteVersion] = Field(default_factory=list)


class CardRead(BaseModel):
    id: int
    type: str
    front: str
    back: str
    tags: list[str]
    difficulty: str
    source_refs: list[dict[str, Any]]
    verified: str
    verify_note: str
    edited_by_me: bool


class QuestionRead(BaseModel):
    id: int
    kind: str
    type: str
    title: str
    bloom: str
    difficulty: str
    points: float
    prompt: str
    options: list[dict[str, Any]]
    answer: str
    explanation: str
    rubric: list[dict[str, Any]]
    hints: list[str]
    solution_steps: list[str]
    expected_output: str
    emphasized: bool
    source_refs: list[dict[str, Any]]
    verified: str
    verify_note: str


class Materials(BaseModel):
    notes: dict[str, NoteRead]
    flashcards: list[CardRead]
    exercises: list[QuestionRead]
    questions: list[QuestionRead]


class GenerateRequest(BaseModel):
    kinds: list[str] | None = None  # None = everything
    instructions: str = ""


class NoteUpdate(BaseModel):
    content_md: str


def _lecture(session: Session, lecture_id: int) -> Lecture:
    lecture = session.get(Lecture, lecture_id)
    if lecture is None:
        raise HTTPException(404, "Lecture not found")
    return lecture


def _note_read(session: Session, note: Note) -> dict[str, Any]:
    versions = session.exec(
        select(Note)
        .where(Note.lecture_id == note.lecture_id, Note.kind == note.kind)
        .order_by(col(Note.version).desc())
    ).all()
    return {**note.model_dump(), "versions": [v.model_dump() for v in versions]}


@router.get("/lectures/{lecture_id}/materials", response_model=Materials)
def get_materials(lecture_id: int, session: Session = Depends(get_session)) -> dict[str, Any]:
    _lecture(session, lecture_id)
    notes = session.exec(
        select(Note).where(Note.lecture_id == lecture_id, col(Note.is_current))
    ).all()
    cards = session.exec(
        select(Flashcard)
        .where(Flashcard.lecture_id == lecture_id, col(Flashcard.is_current))
        .order_by(col(Flashcard.id))
    ).all()
    questions = session.exec(
        select(Question)
        .where(Question.lecture_id == lecture_id, col(Question.is_current))
        .order_by(col(Question.id))
    ).all()
    return {
        "notes": {n.kind: _note_read(session, n) for n in notes},
        "flashcards": cards,
        "exercises": [q for q in questions if q.kind == "exercise"],
        "questions": [q for q in questions if q.kind == "exam"],
    }


@router.get("/lectures/{lecture_id}/generate/estimate")
def get_estimate(lecture_id: int, session: Session = Depends(get_session)) -> dict[str, Any]:
    lecture = _lecture(session, lecture_id)
    return {**estimate(session, lecture), "configured": is_configured()}


@router.post("/lectures/{lecture_id}/generate", status_code=202)
def post_generate(
    lecture_id: int, req: GenerateRequest, session: Session = Depends(get_session)
) -> dict[str, Any]:
    lecture = _lecture(session, lecture_id)
    if lecture.stages.get("transcribe") != "done":
        raise HTTPException(409, "Transcribe the lecture first")
    if not is_configured():
        raise HTTPException(409, "Add an Anthropic API key on the Settings page first")
    kinds = req.kinds or list(ALL_KINDS)
    unknown = set(kinds) - set(ALL_KINDS)
    if unknown:
        raise HTTPException(422, f"Unknown section(s): {sorted(unknown)}")
    busy = session.exec(
        select(Job).where(
            Job.lecture_id == lecture_id,
            col(Job.status).in_([JobStatus.queued, JobStatus.running]),
            Job.kind != INDEX_JOB,
        )
    ).first()
    if busy:
        raise HTTPException(409, "This lecture is already being processed")
    lecture.stages = {**lecture.stages, "generate": "pending"}
    lecture.status = LectureStatus.processing
    session.add(lecture)
    session.commit()
    job = jobs.enqueue(
        session,
        GENERATE_JOB,
        lecture_id=lecture_id,
        payload={"kinds": kinds, "instructions": req.instructions},
    )
    bus.publish("lecture", {"id": lecture_id, "subject_id": lecture.subject_id})
    return {"job_id": job.id, "provider": active_provider_name()}


@router.get("/notes/{note_id}", response_model=NoteRead)
def get_note(note_id: int, session: Session = Depends(get_session)) -> dict[str, Any]:
    note = session.get(Note, note_id)
    if note is None:
        raise HTTPException(404, "Note not found")
    return _note_read(session, note)


@router.patch("/notes/{note_id}", response_model=NoteRead)
def edit_note(
    note_id: int, data: NoteUpdate, session: Session = Depends(get_session)
) -> dict[str, Any]:
    """Save a manual edit. The first edit of a generated version creates a new version, so the
    generated text stays available; later edits update that edited version in place."""
    note = session.get(Note, note_id)
    if note is None:
        raise HTTPException(404, "Note not found")
    if not note.is_current:
        raise HTTPException(409, "Only the current version can be edited; restore it first")
    if note.edited_by_me:
        note.content_md = data.content_md
        note.updated_at = utcnow()
        session.add(note)
    else:
        note.is_current = False
        session.add(note)
        latest = session.exec(
            select(Note.version)
            .where(Note.lecture_id == note.lecture_id, Note.kind == note.kind)
            .order_by(col(Note.version).desc())
        ).first()
        note = Note(
            subject_id=note.subject_id,
            lecture_id=note.lecture_id,
            kind=note.kind,
            version=(latest or 0) + 1,
            content_md=data.content_md,
            data=note.data,
            source_refs=note.source_refs,
            edited_by_me=True,
            model=note.model,
            prompt_version=note.prompt_version,
        )
        session.add(note)
    session.commit()
    session.refresh(note)
    if note.lecture_id:
        queue_index(session, note.lecture_id)
    return _note_read(session, note)


@router.post("/notes/{note_id}/restore", response_model=NoteRead)
def restore_note(note_id: int, session: Session = Depends(get_session)) -> dict[str, Any]:
    note = session.get(Note, note_id)
    if note is None:
        raise HTTPException(404, "Note not found")
    for other in session.exec(
        select(Note).where(Note.lecture_id == note.lecture_id, Note.kind == note.kind)
    ).all():
        other.is_current = other.id == note.id
        session.add(other)
    session.commit()
    session.refresh(note)
    return _note_read(session, note)


@router.delete("/flashcards/{card_id}", status_code=204)
def delete_card(card_id: int, session: Session = Depends(get_session)) -> Response:
    card = session.get(Flashcard, card_id)
    if card is None:
        raise HTTPException(404, "Card not found")
    session.delete(card)
    session.commit()
    return Response(status_code=204)


@router.delete("/questions/{question_id}", status_code=204)
def delete_question(question_id: int, session: Session = Depends(get_session)) -> Response:
    q = session.get(Question, question_id)
    if q is None:
        raise HTTPException(404, "Question not found")
    session.delete(q)
    session.commit()
    return Response(status_code=204)
