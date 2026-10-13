"""Job: the subject hub's course overview, topics and concept map.

Built from the per-lecture notes (TL;DR, note section headings, glossary terms, exam hints),
not from raw transcripts (SPEC §4), so it stays small even late in the semester. It runs after
each generated lecture (one queued update per subject) and on demand, and is stored as a
versioned subject-level ``Note`` (kind "course", ``lecture_id`` None). The cumulative glossary
and topic mastery are computed at read time (``backend.study.topics``).
"""

from __future__ import annotations

import logging
from typing import Any

from sqlmodel import Session, col, func, select

from backend.ai import schemas as sc
from backend.ai.prompts import load_prompt
from backend.ai.providers import active_provider_name, get_provider
from backend.ai.types import LLMRequest
from backend.core import jobs
from backend.core.config import get_settings
from backend.core.db import session_scope
from backend.core.events import bus
from backend.core.jobs import JobContext, JobError, handler
from backend.core.models import ExamHint, Job, JobStatus, Lecture, Note, Subject
from backend.core.util import format_hms
from backend.pipeline.generate import LANGUAGE_NAMES, record_usage

log = logging.getLogger(__name__)

SUBJECT_JOB = "subject_hub"
HUB_KIND = "course"
SYSTEM = (
    "You maintain a revision overview of a university course for a final-year AI student, "
    "built from the study notes of each lecture. Be accurate and concrete; use only what the "
    "notes contain."
)


def hub_enabled(provider_name: str) -> bool:
    cfg = get_settings().generation
    return cfg.subject_hub and (provider_name not in ("ollama", "openai") or cfg.subject_hub_local)


def queue_subject_update(s: Session, subject_id: int, *, force: bool = False) -> bool:
    """Queue one update per subject (a waiting job already covers later lectures)."""
    if not force and not hub_enabled(active_provider_name()):
        return False
    waiting = s.exec(
        select(Job).where(Job.kind == SUBJECT_JOB, Job.status == JobStatus.queued)
    ).all()
    if any(j.payload.get("subject_id") == subject_id for j in waiting):
        return False
    jobs.enqueue(s, SUBJECT_JOB, payload={"subject_id": subject_id})
    return True


def is_updating(s: Session, subject_id: int) -> bool:
    active = s.exec(
        select(Job).where(
            Job.kind == SUBJECT_JOB, col(Job.status).in_([JobStatus.queued, JobStatus.running])
        )
    ).all()
    return any(j.payload.get("subject_id") == subject_id for j in active)


def lectures_with_notes(s: Session, subject_id: int) -> list[tuple[int, Lecture]]:
    """(number, lecture) for the subject's lectures, numbered like everywhere else."""
    lectures = s.exec(
        select(Lecture)
        .where(Lecture.subject_id == subject_id)
        .order_by(col(Lecture.started_at), col(Lecture.id))
    ).all()
    return [(i + 1, lec) for i, lec in enumerate(lectures)]


def _current(s: Session, lecture_id: int, kind: str) -> Note | None:
    return s.exec(
        select(Note).where(Note.lecture_id == lecture_id, Note.kind == kind, col(Note.is_current))
    ).first()


def build_material(s: Session, subject: Subject) -> tuple[str, int]:
    """Context text and how many lectures it covers."""
    parts = [f"# Course: {subject.name}"]
    if subject.exam_date:
        parts.append(f"Exam date: {subject.exam_date.isoformat()}")
    if subject.instructions.strip():
        parts.append(f"Course notes from the student: {subject.instructions.strip()}")
    covered = 0
    for number, lec in lectures_with_notes(s, subject.id):  # type: ignore[arg-type]
        notes = _current(s, lec.id, "notes")  # type: ignore[arg-type]
        summary = _current(s, lec.id, "summary")  # type: ignore[arg-type]
        if notes is None and summary is None:
            continue
        covered += 1
        parts.append(f'\n## L{number} "{lec.title}" ({lec.started_at.astimezone():%Y-%m-%d})')
        if summary is not None:
            parts += [f"- {b['text']}" for b in (summary.data.get("tldr") or [])]
        if notes is not None:
            heads = [
                ("★ " if sec.get("emphasized") else "") + sec.get("heading", "")
                for sec in notes.data.get("sections") or []
            ]
            parts.append("Note sections: " + " | ".join(h for h in heads if h))
        glossary = _current(s, lec.id, "glossary")  # type: ignore[arg-type]
        if glossary is not None:
            terms = [t.get("term", "") for t in glossary.data.get("terms") or []]
            if terms:
                parts.append("Terms: " + ", ".join(t for t in terms if t))
        hints = s.exec(
            select(ExamHint).where(ExamHint.lecture_id == lec.id).order_by(col(ExamHint.start_s))
        ).all()
        if hints:
            parts.append(
                "Exam hints: "
                + "; ".join(f"[{format_hms(h.start_s)}] {h.topic or h.quote}" for h in hints)
            )
    return "\n".join(parts), covered


def current_subject_note(s: Session, subject_id: int, kind: str) -> Note | None:
    return s.exec(
        select(Note).where(
            Note.subject_id == subject_id,
            col(Note.lecture_id).is_(None),
            Note.kind == kind,
            col(Note.is_current),
        )
    ).first()


def current_hub(s: Session, subject_id: int) -> Note | None:
    return current_subject_note(s, subject_id, HUB_KIND)


def save_subject_note(
    subject_id: int,
    kind: str,
    content: str,
    data: dict[str, Any],
    model: str,
    prompt_version: str,
    instructions: str = "",
) -> Note:
    """New current version of a subject-level note (course overview, cheat sheet)."""
    with session_scope() as s:
        old = s.exec(
            select(Note).where(
                Note.subject_id == subject_id,
                col(Note.lecture_id).is_(None),
                Note.kind == kind,
                col(Note.is_current),
            )
        ).all()
        for o in old:
            o.is_current = False
            s.add(o)
        version = (
            s.exec(
                select(func.max(Note.version)).where(
                    Note.subject_id == subject_id,
                    col(Note.lecture_id).is_(None),
                    Note.kind == kind,
                )
            ).one()
            or 0
        ) + 1
        note = Note(
            subject_id=subject_id,
            lecture_id=None,
            kind=kind,
            version=version,
            content_md=content,
            data=data,
            model=model,
            prompt_version=prompt_version,
            instructions=instructions,
        )
        s.add(note)
        s.commit()
        s.refresh(note)
        s.expunge(note)
    return note


def render_overview(out: sc.SubjectHubOut, numbers: dict[int, Lecture]) -> str:
    lines = ["## The course so far", "", out.overview.strip(), "", "## Lecture by lecture", ""]
    for r in sorted(out.recaps, key=lambda x: x.lecture):
        lec = numbers.get(r.lecture)
        title = f' "{lec.title}"' if lec else ""
        lines += [f"**L{r.lecture}{title}.** {r.recap.strip()}", ""]
    return "\n".join(lines).strip()


@handler(SUBJECT_JOB)
def update_subject_hub(ctx: JobContext) -> None:
    subject_id = ctx.payload["subject_id"]
    with session_scope() as s:
        subject = s.get(Subject, subject_id)
        if subject is None:
            return
        material, covered = build_material(s, subject)
        prev = current_hub(s, subject_id)
        prev_topics = [t["name"] for t in (prev.data.get("topics") or [])] if prev else []
        numbers = {n: lec for n, lec in lectures_with_notes(s, subject_id)}
        out_lang = LANGUAGE_NAMES.get(subject.output_language, subject.output_language or "English")
        name = subject.name
    if not covered:
        raise JobError("No lecture of this subject has study material yet")
    ctx.progress(0.1, "Updating the course overview")
    prompt = load_prompt("subject_hub")
    previous = (
        "Existing topics (keep their names, add new ones, merge only true duplicates): "
        + "; ".join(prev_topics)
        if prev_topics
        else ""
    )
    result = get_provider().complete(
        LLMRequest(
            task="subject_hub",
            system=SYSTEM,
            context=material,
            instruction=prompt.render(
                subject=name, previous_topics=previous, output_language=out_lang
            ),
            output=sc.SubjectHubOut,
        )
    )
    record_usage(None, subject_id, "subject_hub", result)
    out: sc.SubjectHubOut = result.parsed
    data: dict[str, Any] = out.model_dump()
    data["lecture_ids"] = {str(n): lec.id for n, lec in numbers.items()}
    data["lectures_covered"] = covered
    save_subject_note(
        subject_id, HUB_KIND, render_overview(out, numbers), data, result.model, prompt.tag
    )
    bus.publish("subject_hub", {"subject_id": subject_id})
    log.info("Course overview for subject %s updated (%d lectures)", subject_id, covered)
