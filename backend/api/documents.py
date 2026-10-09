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
from backend.pipeline.documents import (
    DOCS_DIR,
    delete_document,
    docs_dir,
    is_processing,
    queue_document,
    safe_name,
    unique_path,
)
from backend.pipeline.past_exams import PAST_EXAM_KIND, has_text, is_analysing, queue_analysis
from backend.pipeline.past_exams import summary as past_exam_summary

router = APIRouter(prefix="/api", tags=["documents"])

KINDS = ("slides", "notes", "past_exam", "other")
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
    analysing: bool = False  # past exams: format/style analysis queued or running
    past_exam: dict[str, Any] | None = None  # past exams: the analysis summary


def _read(s: Session, m: Material) -> dict[str, Any]:
    placed = s.exec(select(func.count()).where(Slide.material_id == m.id)).one()
    return {
        **m.model_dump(),
        "pages": m.extracted_text.count("[page "),
        "placed": placed,
        "processing": is_processing(s, m.id),  # type: ignore[arg-type]
        "analysing": m.kind == PAST_EXAM_KIND and is_analysing(s, m.id),  # type: ignore[arg-type]
        "past_exam": past_exam_summary(m) if m.kind == PAST_EXAM_KIND else None,
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
    data = await _pdf_body(filename, request)
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
    session: Session = Depends(get_session),
) -> dict:
    """A PDF for the whole subject (script, exercise sheet...): text extracted, not placed."""
    subject = session.get(Subject, subject_id)
    if subject is None:
        raise HTTPException(404, "Subject not found")
    if kind not in KINDS:
        raise HTTPException(422, f"kind must be one of {KINDS}")
    data = await _pdf_body(filename, request)
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


async def _pdf_body(filename: str, request: Request) -> bytes:
    if not filename.lower().endswith(".pdf"):
        raise HTTPException(415, "Only PDF files can be added here")
    data = await request.body()
    if len(data) > MAX_BYTES:
        raise HTTPException(413, "PDF is larger than 200 MB")
    if not data.startswith(b"%PDF"):
        raise HTTPException(415, "This file is not a PDF")
    return data


@router.get("/documents/{document_id}/file")
def document_file(document_id: int, session: Session = Depends(get_session)) -> FileResponse:
    m = session.get(Material, document_id)
    if m is None or not abs_path(m.file_path).exists():
        raise HTTPException(404, "Document not found")
    # inline: opens in the browser's PDF viewer
    return FileResponse(
        abs_path(m.file_path),
        media_type="application/pdf",
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


@router.post("/documents/{document_id}/analyse", response_model=DocumentRead, status_code=202)
def analyse_document(document_id: int, session: Session = Depends(get_session)) -> dict:
    """Past exams: analyse the format and style again (e.g. after a failure or a better model)."""
    m = session.get(Material, document_id)
    if m is None:
        raise HTTPException(404, "Document not found")
    if m.kind != PAST_EXAM_KIND:
        raise HTTPException(422, "Only past exams are analysed")
    if is_processing(session, document_id) or is_analysing(session, document_id):
        raise HTTPException(409, "This PDF is still being processed")
    if not has_text(m.extracted_text):
        raise HTTPException(409, "No text could be read from this PDF")
    if not queue_analysis(session, document_id):
        raise HTTPException(409, "Add an Anthropic API key on the Settings page first")
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
