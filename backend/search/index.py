"""Search index: chunk lectures into ``chunk`` rows mirrored into FTS5 and sqlite-vec.

Transcript chunks are 60-90 s windows cut at segment boundaries; note chunks are one per
"## " section of the generated notes (plus the summary and announcements); slide chunks are
the OCR text of one slide. Indexing a lecture
is idempotent: its old chunks are removed first. If embeddings are unavailable (Ollama down)
the lecture is still keyword-searchable and gets embedded on the next index run.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from sqlalchemy import text
from sqlmodel import Session, col, delete, select

from backend.core import jobs
from backend.core.config import get_settings
from backend.core.db import session_scope
from backend.core.events import bus
from backend.core.jobs import JobContext, JobError, handler
from backend.core.models import Chunk, Job, JobStatus, Lecture, Note, Slide, TranscriptSegment
from backend.search.embeddings import EmbeddingError, embed, pack

log = logging.getLogger(__name__)

INDEX_JOB = "index"
NOTE_KINDS_INDEXED = ("summary", "notes", "announcements", "glossary")
TS = re.compile(r"\[(?:(\d+):)?(\d{1,3}):(\d{2})")
CHIP = re.compile(r"\s*\[(?:\d+:)?\d{1,3}:\d{2}(?:\s*[-–]\s*(?:\d+:)?\d{1,3}:\d{2})?\]")


def first_ts(md: str) -> float | None:
    m = TS.search(md)
    if not m:
        return None
    return int(m.group(1) or 0) * 3600 + int(m.group(2)) * 60 + int(m.group(3))


def transcript_chunks(segments: list[TranscriptSegment], min_s: float, max_s: float) -> list[dict]:
    chunks: list[dict] = []
    cur: list[TranscriptSegment] = []
    for seg in segments:
        if cur and seg.end - cur[0].start > max_s:
            chunks.append(_tchunk(cur))
            cur = []
        cur.append(seg)
        if cur[-1].end - cur[0].start >= min_s:
            chunks.append(_tchunk(cur))
            cur = []
    if cur:
        # A short tail joins the previous chunk instead of standing alone.
        if chunks and cur[-1].end - cur[0].start < min_s / 3:
            prev = chunks.pop()
            chunks.append(
                {
                    **prev,
                    "text": prev["text"] + " " + " ".join(s.text for s in cur),
                    "end_s": cur[-1].end,
                }
            )
        else:
            chunks.append(_tchunk(cur))
    return chunks


def _tchunk(segs: list[TranscriptSegment]) -> dict:
    return {
        "source": "transcript",
        "text": " ".join(s.text for s in segs),
        "start_s": segs[0].start,
        "end_s": segs[-1].end,
    }


def note_chunks(note: Note) -> list[dict]:
    if note.kind != "notes":
        return [
            {
                "source": "note",
                "note_kind": note.kind,
                "heading": note.kind.title(),
                "text": note.content_md.strip(),  # keep [mm:ss] so chat can cite the exact moment
                "start_s": first_ts(note.content_md),
            }
        ]
    out = []
    for part in re.split(r"(?m)^## ", note.content_md):
        if not part.strip():
            continue
        heading, _, body = part.partition("\n")
        out.append(
            {
                "source": "note",
                "note_kind": "notes",
                "heading": CHIP.sub("", heading).replace("★", "").strip(),
                "text": body.strip(),
                "start_s": first_ts(heading) if first_ts(heading) is not None else first_ts(body),
            }
        )
    return [c for c in out if c["text"]]


MIN_SLIDE_TEXT = 15  # shorter OCR text (a title card, a logo) isn't worth indexing


def slide_chunks(slides: list[Slide]) -> list[dict]:
    """One chunk per slide with readable text; a slide lasts until the next one appears."""
    out: list[dict] = []
    for i, sl in enumerate(slides):
        text = sl.ocr_text.strip()
        if len(text) < MIN_SLIDE_TEXT or (out and out[-1]["text"] == text):
            continue
        end = slides[i + 1].t_s if i + 1 < len(slides) else None
        out.append(
            {
                "source": "slide",
                "heading": text.splitlines()[0][:120],
                "text": text,
                "start_s": sl.t_s,
                "end_s": end,
                "page": sl.page,
            }
        )
    return out


def handwritten_chunks(s: Session, lecture_id: int) -> list[dict]:
    """One chunk per page of the student's handwritten notes (no timestamp)."""
    from backend.pipeline.handwriting import lecture_notes

    out = []
    for m in lecture_notes(s, lecture_id):
        for p in (m.analysis or {}).get("pages", []):
            if len(p["text"].strip()) >= MIN_SLIDE_TEXT:
                summary = f": {p['summary']}" if p.get("summary") else ""
                out.append(
                    {
                        "source": "handwritten",
                        "heading": f"My notes, p. {p['page']}{summary}"[:200],
                        "text": p["text"].strip(),
                        "start_s": None,
                        "end_s": None,
                        "material_id": m.id,
                        "page": p["page"],
                    }
                )
    return out


def remove_lecture_index(s: Session, lecture_id: int) -> None:
    ids = s.exec(select(Chunk.id).where(Chunk.lecture_id == lecture_id)).all()
    if ids:
        marks = ",".join(str(int(i)) for i in ids)  # type: ignore[arg-type]
        s.connection().execute(text(f"DELETE FROM chunk_fts WHERE rowid IN ({marks})"))
        s.connection().execute(text(f"DELETE FROM chunk_vec WHERE chunk_id IN ({marks})"))
    s.exec(delete(Chunk).where(Chunk.lecture_id == lecture_id))  # type: ignore[call-overload]


def index_lecture(lecture_id: int) -> dict[str, Any]:
    cfg = get_settings().search
    with session_scope() as s:
        lecture = s.get(Lecture, lecture_id)
        if lecture is None:
            raise JobError("Lecture no longer exists")
        segments = s.exec(
            select(TranscriptSegment)
            .where(TranscriptSegment.lecture_id == lecture_id)
            .order_by(col(TranscriptSegment.idx))
        ).all()
        notes = s.exec(
            select(Note).where(
                Note.lecture_id == lecture_id,
                col(Note.is_current),
                col(Note.kind).in_(NOTE_KINDS_INDEXED),
            )
        ).all()
        slides = s.exec(
            select(Slide).where(Slide.lecture_id == lecture_id).order_by(col(Slide.t_s))
        ).all()
        raw = transcript_chunks(list(segments), cfg.chunk_min_s, cfg.chunk_max_s)
        raw.extend(slide_chunks(list(slides)))
        raw.extend(handwritten_chunks(s, lecture_id))
        for n in notes:
            raw.extend(note_chunks(n))
        remove_lecture_index(s, lecture_id)
        chunks = [Chunk(subject_id=lecture.subject_id, lecture_id=lecture_id, **c) for c in raw]
        for c in chunks:
            s.add(c)
        s.commit()
        for c in chunks:
            s.refresh(c)
            s.connection().execute(
                text("INSERT INTO chunk_fts(rowid, heading, text) VALUES (:id, :h, :t)"),
                {"id": c.id, "h": c.heading, "t": c.text},
            )
        s.commit()
        items = [(c.id, f"{c.heading}\n{c.text}".strip()) for c in chunks]

    warning = None
    try:
        vectors = embed([t for _, t in items]) if items else []
    except EmbeddingError as e:
        warning = str(e)
        log.warning("Lecture %s indexed for keyword search only: %s", lecture_id, e)
        vectors = []
    if vectors:
        with session_scope() as s:
            for (cid, _), vec in zip(items, vectors, strict=True):
                s.connection().execute(
                    text("INSERT INTO chunk_vec(chunk_id, embedding) VALUES (:id, :v)"),
                    {"id": cid, "v": pack(vec)},
                )
                chunk = s.get(Chunk, cid)
                if chunk is not None:
                    chunk.embedded = True
                    s.add(chunk)
            s.commit()
    return {"chunks": len(items), "semantic": bool(vectors), "warning": warning}


def queue_index(s: Session, lecture_id: int) -> None:
    """Queue (re)indexing unless a job for this lecture is already waiting."""
    waiting = s.exec(
        select(Job).where(
            Job.lecture_id == lecture_id, Job.kind == INDEX_JOB, Job.status == JobStatus.queued
        )
    ).first()
    if waiting is None:
        jobs.enqueue(s, INDEX_JOB, lecture_id=lecture_id)


@handler(INDEX_JOB)
def index_job(ctx: JobContext) -> None:
    assert ctx.lecture_id is not None
    ctx.progress(0.1, "Indexing for search")
    result = index_lecture(ctx.lecture_id)
    with session_scope() as s:
        lecture = s.get(Lecture, ctx.lecture_id)
        if lecture is None:
            return
        lecture.stages = {**lecture.stages, "index": "done"}
        lecture.meta = {**lecture.meta, "index": result}
        s.add(lecture)
        s.commit()
        subject_id = lecture.subject_id
    bus.publish("lecture", {"id": ctx.lecture_id, "subject_id": subject_id})


def backfill(s: Session) -> int:
    """Queue indexing for transcribed lectures that were never indexed (e.g. pre-M6)."""
    n = 0
    for lecture in s.exec(select(Lecture)).all():
        if lecture.stages.get("transcribe") == "done" and "index" not in lecture.stages:
            queue_index(s, lecture.id)  # type: ignore[arg-type]
            n += 1
    return n
