"""PDFs studied like a lecture: a script, a paper or an exercise sheet becomes a "document
lecture" of its subject (``LectureSource.document``), with notes, glossary, cards and questions
of its own, and it feeds everything built from a subject's lectures (course overview, topics,
cheat sheet, search, chat, review, readiness).

The trick that keeps the rest of the app unchanged: each page gets one minute on the lecture's
timeline (page p = ``(p-1)*60`` up to ``p*60`` seconds) and its paragraphs get the seconds. The
page text becomes the "transcript", the rendered pages become its slides. Refs, verification,
dedupe, search chunks and topic time ranges all work as for a recording; refs of a document
lecture also carry ``page`` and the interface shows "p. 3" instead of "02:00".
"""

from __future__ import annotations

import logging
import re
import shutil
from pathlib import Path
from typing import Any

from sqlmodel import Session, delete

from backend.core import jobs
from backend.core.db import session_scope
from backend.core.events import bus
from backend.core.jobs import JobContext, JobError
from backend.core.library import abs_path, create_lecture, rel_path
from backend.core.models import (
    Lecture,
    LectureSource,
    LectureStatus,
    Material,
    Slide,
    Subject,
    TranscriptSegment,
)

log = logging.getLogger(__name__)

PAGE_S = 60  # seconds of "timeline" per page
STUDY_KIND = "study"  # Material.kind of a PDF studied like a lecture
_MAX_PARTS = PAGE_S - 1  # one second per paragraph, inside the page's minute
_MAX_PART_CHARS = 1200


def page_of(t_s: float) -> int:
    return int(max(t_s, 0) // PAGE_S) + 1


def page_refs(refs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Add the page to each ref of a document lecture (the interface shows "p. 3")."""
    return [{**r, "page": page_of(r["start_s"])} for r in refs]


def is_document(lecture: Lecture | None) -> bool:
    return lecture is not None and lecture.source == LectureSource.document


def paragraphs(text: str) -> list[str]:
    """A page's text in parts of a readable size, at most one per second of the page."""
    lines = [ln.strip() for ln in text.splitlines()]
    parts: list[str] = []
    cur = ""
    for ln in lines:
        if not ln:
            if cur:
                parts.append(cur)
                cur = ""
            continue
        # PDF text breaks lines mid-sentence: join, start a new part at a long enough one.
        cur = f"{cur} {ln}".strip() if cur else ln
        if len(cur) >= _MAX_PART_CHARS or (len(cur) > 300 and re.search(r"[.!?:]$", cur)):
            parts.append(cur)
            cur = ""
    if cur:
        parts.append(cur)
    if len(parts) > _MAX_PARTS:  # very dense page: merge the tail into the last second
        parts = [*parts[: _MAX_PARTS - 1], " ".join(parts[_MAX_PARTS - 1 :])]
    return parts


def title_of(filename: str) -> str:
    stem = Path(filename).stem.replace("_", " ").strip()
    return " ".join(stem.split())[:200] or "Document"


def create(s: Session, subject: Subject, filename: str, data: bytes) -> tuple[Lecture, Material]:
    """A new document lecture for an uploaded PDF; its processing job is queued."""
    from backend.pipeline.documents import docs_dir, queue_document, safe_name, unique_path

    lecture = create_lecture(
        s,
        subject,
        source=LectureSource.document,
        status=LectureStatus.processing,
        title=title_of(filename),
        title_is_default=False,
    )
    path = unique_path(docs_dir(lecture), safe_name(filename))
    path.write_bytes(data)
    material = Material(
        subject_id=subject.id,  # type: ignore[arg-type]
        lecture_id=lecture.id,
        kind=STUDY_KIND,
        title=title_of(filename),
        file_path=rel_path(path),
    )
    s.add(material)
    lecture.stages = {"capture": "running"}
    s.add(lecture)
    s.commit()
    s.refresh(material)
    queue_document(s, material.id, lecture.id)  # type: ignore[arg-type]
    return lecture, material


def convert(s: Session, material: Material) -> Lecture:
    """Study a PDF that was added to the subject as background material."""
    from backend.pipeline.documents import delete_document, docs_dir, queue_document

    subject = s.get(Subject, material.subject_id)
    if subject is None:
        raise ValueError("Subject not found")
    src = abs_path(material.file_path)
    if not src.exists():
        raise ValueError("The PDF file is missing")
    lecture = create_lecture(
        s,
        subject,
        source=LectureSource.document,
        status=LectureStatus.processing,
        title=material.title or title_of(src.name),
        title_is_default=False,
    )
    dest = docs_dir(lecture) / src.name
    shutil.copy2(src, dest)
    study = Material(
        subject_id=material.subject_id,
        lecture_id=lecture.id,
        kind=STUDY_KIND,
        title=material.title,
        file_path=rel_path(dest),
    )
    s.add(study)
    delete_document(s, material)  # the subject-level copy: now part of the lecture
    s.commit()
    s.refresh(study)
    queue_document(s, study.id, lecture.id)  # type: ignore[arg-type]
    return lecture


def build(ctx: JobContext, material_id: int, lecture_id: int, texts: list[str]) -> None:
    """Pages -> slides and "transcript" of the document lecture, then its study material."""
    from backend.pipeline.documents import pages_dir_for, render_pages

    if not any(t.strip() for t in texts):
        raise JobError("No text found in this PDF (even with OCR)")
    with session_scope() as s:
        material = s.get(Material, material_id)
        lecture = s.get(Lecture, lecture_id)
        if material is None or lecture is None:
            return
        pdf_path = abs_path(material.file_path)
    files = render_pages(pdf_path, pages_dir_for(pdf_path), list(range(1, len(texts) + 1)), ctx)

    from backend.ai.providers import is_configured as llm_configured
    from backend.pipeline.stages import GENERATE_JOB, lecture_ready

    with session_scope() as s:
        lecture = s.get(Lecture, lecture_id)
        if lecture is None:
            return
        s.exec(delete(Slide).where(Slide.material_id == material_id))  # type: ignore[call-overload]
        s.exec(delete(TranscriptSegment).where(TranscriptSegment.lecture_id == lecture_id))  # type: ignore[call-overload]
        idx = 0
        for page, text in enumerate(texts, start=1):
            start = (page - 1) * PAGE_S
            s.add(
                Slide(
                    lecture_id=lecture_id,
                    t_s=float(start),
                    image_path=rel_path(files[page]),
                    ocr_text=text or " ",
                    source="pdf",
                    material_id=material_id,
                    page=page,
                    score=1.0,
                )
            )
            for k, part in enumerate(paragraphs(text)):
                s.add(
                    TranscriptSegment(
                        lecture_id=lecture_id,
                        idx=idx,
                        start=float(start + k),
                        end=float(start + k + 1),
                        text=part,
                    )
                )
                idx += 1
        lecture.duration_s = float(len(texts) * PAGE_S)
        lecture.meta = {**lecture.meta, "document": {"pages": len(texts)}}
        generate = llm_configured()
        lecture.stages = {
            "capture": "done",
            "transcribe": "done",
            **({"generate": "pending"} if generate else {}),
        }
        lecture.status = LectureStatus.processing if generate else LectureStatus.ready
        lecture.error = None
        s.add(lecture)
        s.commit()
        subject_id, title = lecture.subject_id, lecture.title
        if generate:  # no cleanup: there is no speech recognition to correct
            jobs.enqueue(s, GENERATE_JOB, lecture_id=lecture_id)
        else:
            from backend.search.index import queue_index

            queue_index(s, lecture_id)
    log.info("Document lecture %s: %d pages", lecture_id, len(texts))
    bus.publish("lecture", {"id": lecture_id, "subject_id": subject_id})
    if not generate:
        lecture_ready(lecture_id, title)
