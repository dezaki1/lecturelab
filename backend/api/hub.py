"""Subject hub: course overview, topics with mastery, concept map, glossary, all materials."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, col, func, select

from backend.ai.providers import is_configured
from backend.core.db import get_session
from backend.core.models import Lecture, Material, Note, Subject
from backend.pipeline.subject_hub import (
    current_hub,
    is_updating,
    lectures_with_notes,
    queue_subject_update,
)
from backend.study.topics import concept_map, cumulative_glossary, topics_with_mastery

router = APIRouter(prefix="/api/subjects", tags=["hub"])


def _subject(s: Session, subject_id: int) -> Subject:
    subject = s.get(Subject, subject_id)
    if subject is None:
        raise HTTPException(404, "Subject not found")
    return subject


@router.get("/{subject_id}/hub")
def get_hub(subject_id: int, session: Session = Depends(get_session)) -> dict[str, Any]:
    _subject(session, subject_id)
    hub = current_hub(session, subject_id)
    # Lectures with study material; more than the overview covers means it's out of date.
    summarised = session.exec(
        select(func.count(func.distinct(Note.lecture_id))).where(
            Note.subject_id == subject_id,
            col(Note.lecture_id).is_not(None),
            col(Note.kind).in_(["summary", "notes"]),
            col(Note.is_current),
        )
    ).one()
    covered = int(hub.data.get("lectures_covered", 0)) if hub else 0
    return {
        "overview": None
        if hub is None
        else {
            "content_md": hub.content_md,
            "version": hub.version,
            "model": hub.model,
            "created_at": hub.created_at,
            "lectures_covered": covered,
        },
        "topics": topics_with_mastery(session, subject_id, hub) if hub else [],
        "concept_map": concept_map(hub) if hub else {"nodes": [], "edges": []},
        "glossary": cumulative_glossary(session, subject_id),
        "lectures_with_notes": summarised,
        "outdated": hub is not None and summarised > covered,
        "updating": is_updating(session, subject_id),
        "can_update": is_configured() and summarised > 0,
    }


@router.post("/{subject_id}/hub", status_code=202)
def refresh_hub(subject_id: int, session: Session = Depends(get_session)) -> dict[str, bool]:
    _subject(session, subject_id)
    if not is_configured():
        raise HTTPException(409, "Add an Anthropic API key on the Settings page first")
    if not any(True for _ in lectures_with_notes(session, subject_id)):
        raise HTTPException(409, "This subject has no lectures yet")
    return {"queued": queue_subject_update(session, subject_id, force=True)}


@router.get("/{subject_id}/documents")
def subject_documents(subject_id: int, session: Session = Depends(get_session)) -> list[dict]:
    """Every PDF of the subject (attached to a lecture or to the subject itself)."""
    from backend.api.documents import _read

    _subject(session, subject_id)
    numbers = {lec.id: n for n, lec in lectures_with_notes(session, subject_id)}
    lectures = {
        lec.id: lec
        for lec in session.exec(select(Lecture).where(Lecture.subject_id == subject_id)).all()
    }
    docs = session.exec(
        select(Material).where(Material.subject_id == subject_id).order_by(col(Material.id))
    ).all()
    return [
        {
            **_read(session, m),
            "lecture_number": numbers.get(m.lecture_id) if m.lecture_id else None,
            "lecture_title": lectures[m.lecture_id].title if m.lecture_id in lectures else "",
        }
        for m in docs
    ]
