"""Moving a lecture to another subject, or removing it, without leaving traces behind.

A lecture filed under the wrong subject spreads into that subject: its notes, flashcards and
exam questions, exam hints, search chunks, documents, deadlines, eval questions, recap and
usage records all carry the subject, and the subject-level material (course overview with
topics and concept map, cheat sheet, week recaps, study plan) is built from its lectures.

``move`` re-homes every row made from the lecture (cards keep their review history), rewrites
the stored file paths after the folder moved, and refreshes the subject-level material of BOTH
subjects. ``refresh_subjects`` is also called after a lecture is deleted. The lecture's own
study material was written in the old subject's context (its instructions, vocabulary and
earlier lectures), so ``lecture.meta["moved_from"]`` makes the lecture page offer to
regenerate it; regenerating clears the mark.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any

from sqlalchemy import update
from sqlmodel import Session, col, select

from backend.ai.providers import active_provider_name, is_configured
from backend.core.events import bus
from backend.core.library import move_lecture as move_folder
from backend.core.models import (
    ChatThread,
    Chunk,
    Deadline,
    EvalCase,
    ExamHint,
    Flashcard,
    Lecture,
    Material,
    Note,
    Question,
    Recap,
    Slide,
    Subject,
    UsageRecord,
    utcnow,
)

log = logging.getLogger(__name__)

# Rows that belong to a lecture and carry its subject.
LECTURE_ROWS = (
    Note,
    Flashcard,
    Question,
    ExamHint,
    Chunk,
    Material,
    UsageRecord,
    Deadline,
    EvalCase,
    Recap,
    ChatThread,
)


def rebase(path: str | None, old: str, new: str) -> str | None:
    """``old/x/y`` -> ``new/x/y`` for paths stored relative to the data folder."""
    if not path:
        return path
    p = path.replace("\\", "/")
    o = old.replace("\\", "/").rstrip("/")
    return new.replace("\\", "/").rstrip("/") + p[len(o) :] if p.startswith(o + "/") else path


def move(s: Session, lecture: Lecture, subject: Subject) -> dict[str, Any]:
    """Move ``lecture`` (folder, files, every derived row) to ``subject``; caller has checked
    that nothing is recording or processing it."""
    old_id, old_folder = lecture.subject_id, lecture.folder
    old = s.get(Subject, old_id)
    move_folder(lecture, subject)  # folder, media path, subject_id
    new_folder, lid = lecture.folder, lecture.id
    moved: dict[str, int] = {}
    for model in LECTURE_ROWS:
        n = s.exec(  # type: ignore[call-overload]
            update(model)
            .where(model.lecture_id == lid)  # type: ignore[attr-defined]
            .values(subject_id=subject.id)
        ).rowcount
        if n:
            moved[model.__tablename__] = n  # type: ignore[attr-defined]
    for sl in s.exec(select(Slide).where(Slide.lecture_id == lid)).all():
        sl.image_path = rebase(sl.image_path, old_folder, new_folder)  # type: ignore[assignment]
        s.add(sl)
    for m in s.exec(select(Material).where(Material.lecture_id == lid)).all():
        m.file_path = rebase(m.file_path, old_folder, new_folder)  # type: ignore[assignment]
        pages = (m.analysis or {}).get("pages")
        if pages:
            m.analysis = {
                **m.analysis,
                "pages": [
                    {**p, "image": rebase(p.get("image"), old_folder, new_folder)} for p in pages
                ],
            }
        s.add(m)
    for r in s.exec(select(Recap).where(Recap.lecture_id == lid)).all():
        r.audio_path = rebase(r.audio_path, old_folder, new_folder) or ""
        s.add(r)
    lecture.meta = {
        **lecture.meta,
        "moved_from": {
            "subject_id": old_id,
            "name": old.name if old else "",
            "at": utcnow().isoformat(),
        },
    }
    s.add(lecture)
    s.commit()
    log.info("Moved lecture %s from subject %s to %s: %s", lid, old_id, subject.id, moved)
    refresh_subjects(s, [old_id, subject.id], lecture.started_at.astimezone().date())  # type: ignore[list-item]
    bus.publish("lecture", {"id": lid, "subject_id": subject.id})
    return moved


def refresh_subjects(s: Session, subject_ids: list[int], lecture_day: date) -> None:
    """Rebuild what these subjects derive from their lectures, after one moved or was deleted."""
    from backend.pipeline.cheatsheet import CHEATSHEET_KIND, is_building, queue_cheatsheet
    from backend.pipeline.subject_hub import (
        HUB_KIND,
        current_subject_note,
        hub_enabled,
        lectures_with_notes,
        queue_subject_update,
    )
    from backend.study import planner

    monday = lecture_day - timedelta(days=lecture_day.weekday())
    llm = is_configured()
    for sid in dict.fromkeys(subject_ids):
        if s.get(Subject, sid) is None:
            continue
        with_notes = [
            lec
            for _, lec in lectures_with_notes(s, sid)
            if s.exec(
                select(Note.id).where(
                    Note.lecture_id == lec.id, Note.kind == "notes", col(Note.is_current)
                )
            ).first()
        ]
        if not with_notes:
            # Nothing left to build from: the overview and cheat sheet would only show the
            # lecture that left.
            for kind in (HUB_KIND, CHEATSHEET_KIND):
                for n in s.exec(
                    select(Note).where(
                        Note.subject_id == sid,
                        col(Note.lecture_id).is_(None),
                        Note.kind == kind,
                        col(Note.is_current),
                    )
                ).all():
                    n.is_current = False
                    s.add(n)
            s.commit()
        else:
            if llm and hub_enabled(active_provider_name()):
                queue_subject_update(s, sid, force=True)
            sheet = current_subject_note(s, sid, CHEATSHEET_KIND)
            if sheet is not None and llm and not is_building(s, sid):
                queue_cheatsheet(
                    s, sid, int((sheet.data or {}).get("pages") or 2), sheet.instructions
                )
        for r in s.exec(
            select(Recap).where(
                Recap.scope == "week", Recap.subject_id == sid, Recap.week_start == monday
            )
        ).all():
            r.error = "Outdated: a lecture of this week moved to another subject or was deleted"
            s.add(r)
        s.commit()
        bus.publish("subject_hub", {"subject_id": sid})
        bus.publish("subjects", {"id": sid})
    try:
        planner.build(s)
    except Exception:  # the plan rebuilds itself on the next read anyway
        log.exception("Study plan rebuild failed")
