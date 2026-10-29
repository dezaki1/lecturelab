"""Job: process an uploaded PDF (slides from Moodle etc.).

Pages are rendered to JPEGs and their text extracted with pypdfium2. For a slide PDF attached
to a transcribed lecture, the pages are placed on the lecture timeline (``align``) and stored
as ``Slide`` rows (source "pdf"), so they show up like captured slides and feed generation,
cleanup and search. A PDF added before transcription is placed once the transcript exists.
pdfium releases the GIL while rendering, so the server stays responsive.
"""

from __future__ import annotations

import logging
import re
import shutil
from pathlib import Path

from sqlmodel import Session, col, delete, select

from backend.core import jobs
from backend.core.db import session_scope
from backend.core.events import bus
from backend.core.jobs import JobContext, JobError, handler
from backend.core.library import abs_path, lecture_dir, rel_path
from backend.core.models import Job, JobStatus, Lecture, Material, Slide, TranscriptSegment
from backend.pipeline.align import place_pages

log = logging.getLogger(__name__)

DOCUMENT_JOB = "document"
DOCS_DIR = "materials"
PAGE_WIDTH = 1600
SAFE = re.compile(r"[^\w\-. ]+")


def docs_dir(lecture: Lecture) -> Path:
    d = lecture_dir(lecture) / DOCS_DIR
    d.mkdir(parents=True, exist_ok=True)
    return d


IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png", ".webp")  # photos of handwritten notes


def safe_name(filename: str) -> str:
    stem = SAFE.sub("_", Path(filename).stem).strip(" ._") or "slides"
    suffix = Path(filename).suffix.lower()
    return stem[:80] + (suffix if suffix in IMAGE_SUFFIXES else ".pdf")


def unique_path(folder: Path, name: str) -> Path:
    path = folder / name
    n = 2
    while path.exists():
        path = folder / f"{Path(name).stem} ({n}){Path(name).suffix}"
        n += 1
    return path


def queue_document(s: Session, material_id: int, lecture_id: int | None) -> None:
    jobs.enqueue(s, DOCUMENT_JOB, lecture_id=lecture_id, payload={"material_id": material_id})


def queue_waiting_documents(s: Session, lecture_id: int) -> None:
    """After transcription: place slide PDFs that were added before the transcript existed."""
    for m in s.exec(
        select(Material).where(Material.lecture_id == lecture_id, Material.kind == "slides")
    ).all():
        placed = s.exec(select(Slide.id).where(Slide.material_id == m.id).limit(1)).first()
        if placed is None:
            queue_document(s, m.id, lecture_id)  # type: ignore[arg-type]


def is_processing(s: Session, material_id: int) -> bool:
    active = s.exec(
        select(Job).where(
            Job.kind == DOCUMENT_JOB, col(Job.status).in_([JobStatus.queued, JobStatus.running])
        )
    ).all()
    return any(j.payload.get("material_id") == material_id for j in active)


def page_texts(pdf_path: Path) -> list[str]:
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(pdf_path)
    try:
        texts = []
        for i in range(len(pdf)):
            page = pdf[i]
            raw = page.get_textpage().get_text_bounded()
            texts.append("\n".join(line.strip() for line in raw.splitlines() if line.strip()))
        return texts
    finally:
        pdf.close()


def render_pages(
    pdf_path: Path, out: Path, pages: list[int], ctx: JobContext, width: int = PAGE_WIDTH
) -> dict[int, Path]:
    """Render the given 1-based pages to JPEG; returns page -> file."""
    import pypdfium2 as pdfium

    out.mkdir(parents=True, exist_ok=True)
    pdf = pdfium.PdfDocument(pdf_path)
    files: dict[int, Path] = {}
    try:
        for n, p in enumerate(pages):
            page = pdf[p - 1]
            img = page.render(scale=width / max(page.get_width(), 1)).to_pil()
            path = out / f"p{p:04d}.jpg"
            img.convert("RGB").save(path, "JPEG", quality=85, optimize=True)
            files[p] = path
            ctx.progress(0.3 + 0.7 * (n + 1) / len(pages), "Rendering slide pages")
    finally:
        pdf.close()
    return files


@handler(DOCUMENT_JOB)
def process_document(ctx: JobContext) -> None:
    material_id = ctx.payload["material_id"]
    with session_scope() as s:
        material = s.get(Material, material_id)
        if material is None:
            return  # deleted while queued
        pdf_path = abs_path(material.file_path)
        lecture = s.get(Lecture, material.lecture_id) if material.lecture_id else None
        kind = material.kind
    if kind == "handwritten":
        from backend.pipeline import handwriting

        return handwriting.process(ctx, material_id)
    if not pdf_path.exists():
        raise JobError("The PDF file is missing")

    ctx.progress(0.05, "Reading the PDF")
    try:
        texts = page_texts(pdf_path)
    except Exception as e:
        raise JobError(f"Could not read the PDF: {e}") from e
    if kind in ("past_exam", "study") and not any(t.strip() for t in texts):
        # Old exams and some scripts are scans: render the pages and read them with OCR.
        from backend.pipeline.slides import run_ocr

        ctx.progress(0.2, "Reading the scanned exam")
        files = render_pages(pdf_path, pages_dir_for(pdf_path), list(range(1, len(texts) + 1)), ctx)
        texts = run_ocr([files[p] for p in sorted(files)], ctx)
    with session_scope() as s:
        material = s.get(Material, material_id)
        if material is None:
            return
        material.extracted_text = "\n\n".join(
            f"[page {i}]\n{t}" for i, t in enumerate(texts, start=1)
        )
        s.add(material)
        s.commit()
        if kind == "past_exam":
            from backend.study.exams import queue_past_exam_analysis

            queue_past_exam_analysis(s, material_id)

    if kind == "study" and lecture is not None:  # a PDF studied like a lecture
        from backend.pipeline import study_docs

        study_docs.build(ctx, material_id, lecture.id, texts)  # type: ignore[arg-type]
        return
    if kind != "slides" or lecture is None:
        return
    with session_scope() as s:
        segments = s.exec(
            select(TranscriptSegment)
            .where(TranscriptSegment.lecture_id == lecture.id)
            .order_by(col(TranscriptSegment.idx))
        ).all()
        screen = s.exec(
            select(Slide)
            .where(Slide.lecture_id == lecture.id, Slide.source != "pdf")
            .order_by(col(Slide.t_s))
        ).all()
        seg_rows = [(x.start, x.end, x.text) for x in segments]
        screen_rows = [(x.t_s, x.ocr_text) for x in screen if x.ocr_text.strip()]
    if not seg_rows:
        log.info("PDF %s waits for the transcript of lecture %s", material_id, lecture.id)
        return
    if not any(t.strip() for t in texts):
        raise JobError("This PDF has no text layer (scanned?), so its pages can't be placed")

    ctx.progress(0.2, "Matching pages to the lecture")
    placed = place_pages(texts, seg_rows, screen_rows)
    files = render_pages(pdf_path, pages_dir_for(pdf_path), [p.page for p in placed], ctx)
    with session_scope() as s:
        s.exec(delete(Slide).where(Slide.material_id == material_id))  # type: ignore[call-overload]
        for p in placed:
            s.add(
                Slide(
                    lecture_id=lecture.id,  # type: ignore[arg-type]
                    t_s=round(p.t_s, 2),
                    image_path=rel_path(files[p.page]),
                    ocr_text=texts[p.page - 1] or " ",
                    source="pdf",
                    material_id=material_id,
                    page=p.page,
                    score=round(p.score, 3),
                )
            )
        s.commit()
        lec = s.get(Lecture, lecture.id)
        if lec is not None and lec.stages.get("index") == "done":
            from backend.search.index import queue_index

            queue_index(s, lecture.id)  # type: ignore[arg-type]
    log.info("Placed %d of %d PDF pages in lecture %s", len(placed), len(texts), lecture.id)
    bus.publish("lecture", {"id": lecture.id, "subject_id": lecture.subject_id})


def pages_dir_for(pdf_path: Path) -> Path:
    return pdf_path.with_name(pdf_path.stem + "_pages")


def delete_document(s: Session, material: Material) -> None:
    pdf = abs_path(material.file_path)
    shutil.rmtree(pages_dir_for(pdf), ignore_errors=True)
    pdf.unlink(missing_ok=True)
    s.exec(delete(Slide).where(Slide.material_id == material.id))  # type: ignore[call-overload]
    s.delete(material)
    s.commit()
