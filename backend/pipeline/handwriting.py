"""Handwritten notes: PDFs or photos from the tablet, read by a vision model, page by page.

Runs inside the ``document`` job for materials of kind ``handwritten``. Every page becomes a
JPEG (PDF pages rendered with pypdfium2, photos straightened by their EXIF orientation and
resized) under ``<file>_pages/``. Each page goes to the main model with the lecture's title,
the subject vocabulary and the note headings as hints for decoding terms, and comes back as
Markdown with LaTeX (``prompts/handwriting.md``). Local text-only models can't read images:
then the pages go through RapidOCR instead, which reads print well and handwriting poorly.

The result lives in ``Material.analysis["pages"]`` ([{page, image, text, unclear, summary,
edited}]) and in ``extracted_text``; the student can correct a page's text. The pages are
indexed for search and added to the lecture's generation context.
"""

from __future__ import annotations

import io
import logging
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps
from sqlmodel import Session, col, select

from backend.ai import schemas as sc
from backend.ai.prompts import load_prompt
from backend.ai.providers import get_provider, is_configured
from backend.ai.types import LLMError, LLMNoVision, LLMRequest
from backend.core.db import session_scope
from backend.core.events import bus
from backend.core.jobs import JobContext, JobError
from backend.core.library import abs_path, rel_path
from backend.core.models import Lecture, Material, Note, Subject, utcnow
from backend.pipeline.generate import record_usage

log = logging.getLogger(__name__)

KIND = "handwritten"
IMAGE_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}
PAGE_PX = 1568  # Claude's recommended maximum long side: more pixels only cost more tokens
RENDER_WIDTH = 1600
CONTEXT_CHARS = 12_000  # of handwritten notes in a lecture's generation context
SYSTEM = (
    "You transcribe a university student's handwritten lecture notes exactly as written. You "
    "never add content that is not on the page."
)


def is_image(name: str) -> bool:
    return Path(name).suffix.lower() in IMAGE_TYPES


def looks_like_image(data: bytes) -> bool:
    return (
        data.startswith(b"\xff\xd8")
        or data.startswith(b"\x89PNG")
        or (data[:4] == b"RIFF" and data[8:12] == b"WEBP")
    )


def _page_files(src: Path, out: Path, ctx: JobContext) -> list[Path]:
    from backend.pipeline.documents import render_pages

    out.mkdir(parents=True, exist_ok=True)
    if src.suffix.lower() == ".pdf":
        import pypdfium2 as pdfium

        pdf = pdfium.PdfDocument(src)
        try:
            n = len(pdf)
        finally:
            pdf.close()
        if n == 0:
            raise JobError("The PDF has no pages")
        files = render_pages(src, out, list(range(1, n + 1)), ctx, width=RENDER_WIDTH)
        return [files[p] for p in sorted(files)]
    try:
        img = ImageOps.exif_transpose(Image.open(src))
    except Exception as e:
        raise JobError(f"Could not open the image: {e}") from e
    path = out / "p0001.jpg"
    img.convert("RGB").save(path, "JPEG", quality=88, optimize=True)
    return [path]


def _for_model(path: Path) -> bytes:
    img = Image.open(path)
    img.thumbnail((PAGE_PX, PAGE_PX))
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "JPEG", quality=85)
    return buf.getvalue()


def _hints(s: Session, material: Material) -> str:
    subject = s.get(Subject, material.subject_id)
    lecture = s.get(Lecture, material.lecture_id) if material.lecture_id else None
    lines = [f"Course: {subject.name if subject else ''}"]
    if lecture:
        lines.append(f'Lecture: "{lecture.title}"')
        notes = s.exec(
            select(Note).where(
                Note.lecture_id == lecture.id, Note.kind == "notes", col(Note.is_current)
            )
        ).first()
        if notes:
            heads = [
                ln[3:].split(" [")[0]
                for ln in notes.content_md.splitlines()
                if ln.startswith("## ")
            ]
            if heads:
                lines.append("Topics of the lecture: " + "; ".join(heads[:25]))
    if subject and subject.vocabulary:
        lines.append("Course vocabulary: " + ", ".join(subject.vocabulary[:80]))
    return "\n".join(lines)


def process(ctx: JobContext, material_id: int) -> None:
    with session_scope() as s:
        material = s.get(Material, material_id)
        if material is None:
            return
        src = abs_path(material.file_path)
        hints = _hints(s, material)
        lecture_id, subject_id = material.lecture_id, material.subject_id
    if not src.exists():
        raise JobError("The uploaded file is missing")
    if not is_configured():
        raise JobError("Set up an AI provider first (Settings → AI provider)")
    from backend.pipeline.documents import pages_dir_for

    ctx.progress(0.05, "Preparing the pages")
    files = _page_files(src, pages_dir_for(src), ctx)
    provider = get_provider()
    pages: list[dict[str, Any]] = []
    method, cost = "vision", 0.0
    prompt = load_prompt("handwriting")
    for i, f in enumerate(files, start=1):
        ctx.progress(0.1 + 0.85 * (i - 1) / len(files), f"Reading page {i} of {len(files)}")
        try:
            result = provider.complete(
                LLMRequest(
                    task=KIND,
                    system=SYSTEM,
                    context=hints,
                    instruction=prompt.render(page=i, pages=len(files)),
                    output=sc.HandwritingOut,
                    images=[("image/jpeg", _for_model(f))],
                    max_tokens=6000,
                )
            )
        except LLMNoVision:
            method = "ocr"
            break
        except LLMError as e:
            raise JobError(f"Reading page {i} failed: {e}") from e
        cost += record_usage(lecture_id, subject_id, KIND, result)
        out: sc.HandwritingOut = result.parsed
        pages.append(
            {
                "page": i,
                "image": rel_path(f),
                "text": out.markdown.strip(),
                "unclear": out.unclear,
                "summary": out.summary.strip(),
                "edited": False,
            }
        )
    if method == "ocr":
        from backend.pipeline.slides import run_ocr

        ctx.progress(0.2, "Reading the pages with local OCR")
        texts = run_ocr(files, ctx)
        pages = [
            {
                "page": i,
                "image": rel_path(f),
                "text": t.strip(),
                "unclear": [],
                "summary": "",
                "edited": False,
            }
            for i, (f, t) in enumerate(zip(files, texts, strict=True), start=1)
        ]
    _store(material_id, pages, method, cost)


def _store(material_id: int, pages: list[dict[str, Any]], method: str, cost: float) -> None:
    with session_scope() as s:
        material = s.get(Material, material_id)
        if material is None:
            return
        material.analysis = {
            "pages": pages,
            "method": method,
            "cost_usd": round(cost, 4),
            "read_at": utcnow().isoformat(),
        }
        material.extracted_text = joined(pages)
        s.add(material)
        s.commit()
        _reindex(s, material)


def joined(pages: list[dict[str, Any]]) -> str:
    return "\n\n".join(f"[page {p['page']}]\n{p['text']}" for p in pages)


def _reindex(s: Session, material: Material) -> None:
    if material.lecture_id is None:
        return
    lec = s.get(Lecture, material.lecture_id)
    if lec is not None and lec.stages.get("index") == "done":
        from backend.search.index import queue_index

        queue_index(s, lec.id)  # type: ignore[arg-type]
    bus.publish("lecture", {"id": material.lecture_id, "subject_id": material.subject_id})


def edit_page(s: Session, material: Material, page: int, text: str) -> dict[str, Any]:
    pages = [dict(p) for p in (material.analysis or {}).get("pages", [])]
    hit = next((p for p in pages if p["page"] == page), None)
    if hit is None:
        raise KeyError(page)
    hit.update(text=text.strip(), edited=True)
    material.analysis = {**material.analysis, "pages": pages}  # reassign: JSON column
    material.extracted_text = joined(pages)
    s.add(material)
    s.commit()
    _reindex(s, material)
    return hit


def lecture_notes(s: Session, lecture_id: int) -> list[Material]:
    return list(
        s.exec(
            select(Material)
            .where(Material.lecture_id == lecture_id, Material.kind == KIND)
            .order_by(col(Material.id))
        ).all()
    )


def context_block(s: Session, lecture_id: int) -> str:
    """The student's handwritten notes for the generation context ('' if none)."""
    parts = []
    for m in lecture_notes(s, lecture_id):
        for p in (m.analysis or {}).get("pages", []):
            if p["text"].strip():
                parts.append(f"[{m.title}, page {p['page']}]\n{p['text'].strip()}")
    text = "\n\n".join(parts)
    return text[:CONTEXT_CHARS]
