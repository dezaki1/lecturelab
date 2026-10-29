"""PDFs attached to a lecture (slide decks from Moodle): upload, list, open, delete."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlmodel import Session, col, func, select

from backend.core.config import get_settings
from backend.core.db import get_session
from backend.core.events import bus
from backend.core.library import abs_path, rel_path
from backend.core.models import Lecture, Material, Slide, Subject
from backend.pipeline import handwriting as hw
from backend.pipeline import study_docs
from backend.pipeline.documents import (
    DOCS_DIR,
    delete_document,
    docs_dir,
    is_processing,
    queue_document,
    safe_name,
    unique_path,
)

router = APIRouter(prefix="/api", tags=["documents"])

KINDS = ("slides", "notes", "past_exam", "other", "handwritten", "study")
MAX_BYTES = 200 * 1024 * 1024


class DocumentRead(BaseModel):
    id: int
    lecture_id: int | None
    kind: str
    title: str
    created_at: datetime
    pages: int  # 0 until the PDF has been read
    placed: int  # slide pages placed on the lecture timeline
    processing: bool


def _read(s: Session, m: Material) -> dict[str, Any]:
    placed = s.exec(select(func.count()).where(Slide.material_id == m.id)).one()
    return {
        **m.model_dump(),
        "pages": m.extracted_text.count("[page "),
        "placed": placed,
        "processing": is_processing(s, m.id),  # type: ignore[arg-type]
    }


@router.get("/lectures/{lecture_id}/documents", response_model=list[DocumentRead])
def list_documents(lecture_id: int, session: Session = Depends(get_session)) -> list[dict]:
    if session.get(Lecture, lecture_id) is None:
        raise HTTPException(404, "Lecture not found")
    docs = session.exec(
        select(Material).where(Material.lecture_id == lecture_id).order_by(col(Material.id))
    ).all()
    return [_read(session, m) for m in docs]


@router.post("/lectures/{lecture_id}/documents", response_model=DocumentRead, status_code=201)
async def upload_document(
    lecture_id: int,
    filename: str,
    request: Request,
    kind: str = "slides",
    session: Session = Depends(get_session),
) -> dict:
    lecture = session.get(Lecture, lecture_id)
    if lecture is None:
        raise HTTPException(404, "Lecture not found")
    if kind not in KINDS:
        raise HTTPException(422, f"kind must be one of {KINDS}")
    data = await _pdf_body(filename, request, images=kind == hw.KIND)
    path = unique_path(docs_dir(lecture), safe_name(filename))
    path.write_bytes(data)
    material = Material(
        subject_id=lecture.subject_id,
        lecture_id=lecture_id,
        kind=kind,
        title=filename.rsplit(".", 1)[0][:200],
        file_path=rel_path(path),
    )
    session.add(material)
    session.commit()
    session.refresh(material)
    queue_document(session, material.id, lecture_id)  # type: ignore[arg-type]
    bus.publish("lecture", {"id": lecture_id, "subject_id": lecture.subject_id})
    return _read(session, material)


@router.post("/subjects/{subject_id}/documents", response_model=DocumentRead, status_code=201)
async def upload_subject_document(
    subject_id: int,
    filename: str,
    request: Request,
    kind: str = "other",
    study: bool = False,
    session: Session = Depends(get_session),
) -> dict:
    """A PDF for the whole subject (script, exercise sheet...). ``study``: studied like a
    lecture (its own notes, cards and questions, see pipeline.study_docs); otherwise its text
    is only extracted (search, chat)."""
    subject = session.get(Subject, subject_id)
    if subject is None:
        raise HTTPException(404, "Subject not found")
    if kind not in KINDS:
        raise HTTPException(422, f"kind must be one of {KINDS}")
    data = await _pdf_body(filename, request)
    if study:
        _, material = study_docs.create(session, subject, filename, data)
        bus.publish("lecture", {"id": material.lecture_id, "subject_id": subject_id})
        return _read(session, material)
    folder = get_settings().subjects_dir / subject.slug / DOCS_DIR
    folder.mkdir(parents=True, exist_ok=True)
    path = unique_path(folder, safe_name(filename))
    path.write_bytes(data)
    material = Material(
        subject_id=subject_id,
        kind=kind,
        title=filename.rsplit(".", 1)[0][:200],
        file_path=rel_path(path),
    )
    session.add(material)
    session.commit()
    session.refresh(material)
    queue_document(session, material.id, None)  # type: ignore[arg-type]
    return _read(session, material)


async def _pdf_body(filename: str, request: Request, images: bool = False) -> bytes:
    """The uploaded PDF (or, for handwritten notes, also a JPEG/PNG/WebP photo)."""
    image = images and hw.is_image(filename)
    if not filename.lower().endswith(".pdf") and not image:
        raise HTTPException(
            415,
            "Only PDFs or photos (JPEG, PNG, WebP) can be added here"
            if images
            else "Only PDF files can be added here",
        )
    data = await request.body()
    if len(data) > MAX_BYTES:
        raise HTTPException(413, "The file is larger than 200 MB")
    if image and not hw.looks_like_image(data):
        raise HTTPException(415, "This file is not a JPEG, PNG or WebP image")
    if not image and not data.startswith(b"%PDF"):
        raise HTTPException(415, "This file is not a PDF")
    return data


@router.post("/documents/{document_id}/study", response_model=DocumentRead, status_code=202)
def study_document(document_id: int, session: Session = Depends(get_session)) -> dict:
    """Study a subject PDF that was added as background: it becomes a document lecture."""
    material = session.get(Material, document_id)
    if material is None:
        raise HTTPException(404, "Document not found")
    if material.lecture_id is not None or material.kind not in ("other", "notes"):
        raise HTTPException(409, "Only a course PDF that isn't part of a lecture can be studied")
    try:
        lecture = study_docs.convert(session, material)
    except ValueError as e:
        raise HTTPException(409, str(e)) from e
    bus.publish("lecture", {"id": lecture.id, "subject_id": lecture.subject_id})
    study = session.exec(select(Material).where(Material.lecture_id == lecture.id)).one()
    return _read(session, study)


@router.get("/documents/{document_id}/file")
def document_file(document_id: int, session: Session = Depends(get_session)) -> FileResponse:
    m = session.get(Material, document_id)
    if m is None or not abs_path(m.file_path).exists():
        raise HTTPException(404, "Document not found")
    # inline: opens in the browser's PDF viewer (or as an image)
    path = abs_path(m.file_path)
    return FileResponse(
        path,
        media_type=hw.IMAGE_TYPES.get(path.suffix.lower(), "application/pdf"),
        content_disposition_type="inline",
        filename=abs_path(m.file_path).name,
    )


@router.post("/documents/{document_id}/place", response_model=DocumentRead, status_code=202)
def replace_document(document_id: int, session: Session = Depends(get_session)) -> dict:
    """Match the pages to the lecture again (e.g. after re-transcribing)."""
    m = session.get(Material, document_id)
    if m is None:
        raise HTTPException(404, "Document not found")
    if not is_processing(session, document_id):
        queue_document(session, document_id, m.lecture_id)
    return _read(session, m)


@router.delete("/documents/{document_id}", status_code=204)
def remove_document(document_id: int, session: Session = Depends(get_session)) -> Response:
    m = session.get(Material, document_id)
    if m is None:
        raise HTTPException(404, "Document not found")
    if is_processing(session, document_id):
        raise HTTPException(409, "This PDF is still being processed")
    lecture = session.get(Lecture, m.lecture_id) if m.lecture_id else None
    delete_document(session, m)
    if lecture is not None:
        from backend.search.index import queue_index

        if lecture.stages.get("index") == "done":
            queue_index(session, lecture.id)  # type: ignore[arg-type]
        bus.publish("lecture", {"id": lecture.id, "subject_id": lecture.subject_id})
    return Response(status_code=204)


# Handwritten notes --------------------------------------------------------------------------


class PageEdit(BaseModel):
    text: str


def _handwritten(s: Session, m: Material) -> dict[str, Any]:
    a = m.analysis or {}
    return {
        "id": m.id,
        "title": m.title,
        "created_at": m.created_at,
        "processing": is_processing(s, m.id),  # type: ignore[arg-type]
        "method": a.get("method"),
        "cost_usd": a.get("cost_usd", 0),
        "is_pdf": m.file_path.lower().endswith(".pdf"),
        "pages": [
            {k: p.get(k) for k in ("page", "text", "unclear", "summary", "edited")}
            for p in a.get("pages", [])
        ],
    }


@router.get("/lectures/{lecture_id}/handwritten")
def list_handwritten(lecture_id: int, session: Session = Depends(get_session)) -> list[dict]:
    if session.get(Lecture, lecture_id) is None:
        raise HTTPException(404, "Lecture not found")
    return [_handwritten(session, m) for m in hw.lecture_notes(session, lecture_id)]


def _page(m: Material | None, page: int) -> dict[str, Any]:
    pages = (m.analysis or {}).get("pages", []) if m else []
    hit = next((p for p in pages if p["page"] == page), None)
    if m is None or m.kind != hw.KIND or hit is None:
        raise HTTPException(404, "Page not found")
    return hit


@router.get("/documents/{document_id}/pages/{page}/image")
def page_image(
    document_id: int, page: int, session: Session = Depends(get_session)
) -> FileResponse:
    p = _page(session.get(Material, document_id), page)
    path = abs_path(p["image"])
    if not path.is_file():
        raise HTTPException(404, "Page image missing")
    return FileResponse(path, media_type="image/jpeg")


@router.patch("/documents/{document_id}/pages/{page}")
def edit_page(
    document_id: int, page: int, req: PageEdit, session: Session = Depends(get_session)
) -> dict[str, Any]:
    m = session.get(Material, document_id)
    _page(m, page)
    return hw.edit_page(session, m, page, req.text)  # type: ignore[arg-type]
