"""Job: a 1-2 page cheat sheet per subject (main model), stored as a versioned subject Note.

Built from the course overview's topics, each lecture's study notes and glossary, the exam
hints and the past exams' profile. Editable like any note (manual edits make a new version).
"""

from __future__ import annotations

import logging

from sqlmodel import Session, col, select

from backend.ai import schemas as sc
from backend.ai.prompts import load_prompt
from backend.ai.providers import get_provider, is_configured
from backend.ai.types import LLMRequest
from backend.core import jobs
from backend.core.db import session_scope
from backend.core.events import bus
from backend.core.jobs import JobContext, JobError, handler
from backend.core.models import ExamHint, Job, JobStatus, Note, Subject
from backend.pipeline.generate import LANGUAGE_NAMES, record_usage
from backend.pipeline.subject_hub import current_hub, lectures_with_notes, save_subject_note

log = logging.getLogger(__name__)

CHEATSHEET_JOB = "cheatsheet"
CHEATSHEET_KIND = "cheatsheet"
NOTES_CHARS_PER_LECTURE = 5000
NOTES_CHARS_TOTAL = 80_000
WORDS_PER_PAGE = 650
SYSTEM = (
    "You condense university course notes into exam cheat sheets for a final-year AI "
    "student. You are accurate, terse and use only the course material provided."
)


def queue_cheatsheet(s: Session, subject_id: int, pages: int, instructions: str) -> Job:
    if not is_configured():
        raise JobError("Add an Anthropic API key on the Settings page first")
    return jobs.enqueue(
        s,
        CHEATSHEET_JOB,
        payload={"subject_id": subject_id, "pages": pages, "instructions": instructions},
    )


def is_building(s: Session, subject_id: int) -> bool:
    active = s.exec(
        select(Job).where(
            Job.kind == CHEATSHEET_JOB, col(Job.status).in_([JobStatus.queued, JobStatus.running])
        )
    ).all()
    return any(j.payload.get("subject_id") == subject_id for j in active)


def material(s: Session, subject: Subject) -> tuple[str, int]:
    parts = [f"# Course: {subject.name}"]
    hub = current_hub(s, subject.id)  # type: ignore[arg-type]
    if hub is not None:
        topics = hub.data.get("topics") or []
        parts.append(
            "Topics in course order: "
            + "; ".join(
                ("★ " if t.get("exam_relevant") else "") + t.get("name", "") for t in topics
            )
        )
    covered, total = 0, 0
    for n, lec in lectures_with_notes(s, subject.id):  # type: ignore[arg-type]
        note = s.exec(
            select(Note).where(
                Note.lecture_id == lec.id, Note.kind == "notes", col(Note.is_current)
            )
        ).first()
        if note is None:
            continue
        body = note.content_md[:NOTES_CHARS_PER_LECTURE]
        total += len(body)
        if total > NOTES_CHARS_TOTAL:
            parts.append("(later lectures omitted for length)")
            break
        covered += 1
        parts.append(f'\n## L{n} "{lec.title}"\n{body}')
        glossary = s.exec(
            select(Note).where(
                Note.lecture_id == lec.id, Note.kind == "glossary", col(Note.is_current)
            )
        ).first()
        if glossary is not None:
            terms = [
                f"{t.get('term')}"
                + (f" ({t['german']})" if t.get("german") else "")
                + f": {t.get('definition', '')}"
                for t in glossary.data.get("terms") or []
            ]
            if terms:
                parts.append("Glossary: " + " | ".join(terms))
        hints = s.exec(select(ExamHint).where(ExamHint.lecture_id == lec.id)).all()
        if hints:
            parts.append("Exam hints: " + "; ".join(h.topic or h.quote for h in hints))
    return "\n".join(parts), covered


@handler(CHEATSHEET_JOB)
def build_cheatsheet(ctx: JobContext) -> None:
    from backend.study.exams import exam_profile, profile_text

    subject_id = ctx.payload["subject_id"]
    pages = max(1, min(int(ctx.payload.get("pages") or 2), 4))
    instructions = (ctx.payload.get("instructions") or "").strip()
    with session_scope() as s:
        subject = s.get(Subject, subject_id)
        if subject is None:
            return
        context, covered = material(s, subject)
        style = profile_text(exam_profile(s, subject_id))
        name = subject.name
        out_lang = LANGUAGE_NAMES.get(subject.output_language, subject.output_language or "English")
    if not covered:
        raise JobError("No lecture of this subject has study notes yet")
    ctx.progress(0.2, "Writing the cheat sheet")
    prompt = load_prompt("cheatsheet")
    result = get_provider().complete(
        LLMRequest(
            task="cheatsheet",
            system=SYSTEM,
            context=context,
            instruction=prompt.render(
                subject=name,
                pages=pages,
                words=pages * WORDS_PER_PAGE,
                output_language=out_lang,
                style=("\nThe exam looks like this; weight the sheet accordingly:" + style)
                if style
                else "",
                extra=f"\nAdditional instructions from the student: {instructions}"
                if instructions
                else "",
            ),
            output=sc.CheatSheetOut,
        )
    )
    record_usage(None, subject_id, "cheatsheet", result)
    out: sc.CheatSheetOut = result.parsed
    save_subject_note(
        subject_id,
        CHEATSHEET_KIND,
        out.markdown.strip(),
        {"title": out.title.strip(), "pages": pages, "lectures_covered": covered},
        result.model,
        prompt.tag,
        instructions,
    )
    bus.publish("cheatsheet", {"subject_id": subject_id})
