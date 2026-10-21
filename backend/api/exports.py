"""Exports: Obsidian vault (zip), PDFs of lectures and cheat sheets, the cheat sheet itself."""

from __future__ import annotations

import re
from datetime import date
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field
from sqlmodel import Session

from backend.core.db import get_session
from backend.core.jobs import JobError
from backend.core.models import Lecture, Subject
from backend.exports import pdf
from backend.exports.vault import base_url, export_vault
from backend.pipeline.cheatsheet import CHEATSHEET_KIND, is_building, queue_cheatsheet
from backend.pipeline.subject_hub import current_subject_note

router = APIRouter(prefix="/api", tags=["exports"])


class CheatSheetRequest(BaseModel):
    pages: int = Field(default=2, ge=1, le=4)
    instructions: str = ""


def _filename(name: str, ext: str) -> str:
    stem = re.sub(r"[^\w\- ]+", "", name).strip() or "lecturelab"
    return f"{stem[:80]}.{ext}"


def _download(body: bytes, media: str, filename: str) -> Response:
    return Response(
        body,
        media_type=media,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/export/vault")
def vault(
    subject_id: list[int] | None = Query(None), session: Session = Depends(get_session)
) -> Response:
    data = export_vault(session, subject_id)
    name = f"LectureLab vault {date.today():%Y-%m-%d}"
    if subject_id and len(subject_id) == 1:
        subject = session.get(Subject, subject_id[0])
        if subject is None:
            raise HTTPException(404, "Subject not found")
        name = f"{subject.name} vault {date.today():%Y-%m-%d}"
    return _download(data, "application/zip", _filename(name, "zip"))


@router.get("/export/pdf-available")
def pdf_available() -> dict[str, bool]:
    return {"available": pdf.find_browser() is not None}


def _pdf(path: str, filename: str, raw: bool = False) -> Response:
    try:
        data = pdf.print_pdf(f"{base_url()}{path}")
    except pdf.PdfError as e:
        raise HTTPException(501, str(e)) from e
    if raw:
        # For the app's own PDF button: plain bytes, no "attachment", no .pdf in the URL.
        # Download managers (IDM) take over anything that looks like a file download, and
        # the page was left with an empty file.
        return Response(
            data,
            media_type="application/octet-stream",
            headers={"X-Filename": quote(filename), "Cache-Control": "no-store"},
        )
    return _download(data, "application/pdf", filename)


@router.get("/lectures/{lecture_id}/export.pdf")
def lecture_pdf(lecture_id: int, session: Session = Depends(get_session)) -> Response:
    return _lecture_pdf(session, lecture_id, raw=False)


@router.get("/lectures/{lecture_id}/pdf-bytes")
def lecture_pdf_bytes(lecture_id: int, session: Session = Depends(get_session)) -> Response:
    return _lecture_pdf(session, lecture_id, raw=True)


def _lecture_pdf(session: Session, lecture_id: int, raw: bool) -> Response:
    lecture = session.get(Lecture, lecture_id)
    if lecture is None:
        raise HTTPException(404, "Lecture not found")
    return _pdf(f"/print/lectures/{lecture_id}", _filename(lecture.title, "pdf"), raw)


@router.get("/subjects/{subject_id}/cheatsheet")
def get_cheatsheet(subject_id: int, session: Session = Depends(get_session)) -> dict[str, Any]:
    if session.get(Subject, subject_id) is None:
        raise HTTPException(404, "Subject not found")
    note = current_subject_note(session, subject_id, CHEATSHEET_KIND)
    return {
        "note": None
        if note is None
        else {
            "id": note.id,
            "title": note.data.get("title", ""),
            "content_md": note.content_md,
            "version": note.version,
            "edited_by_me": note.edited_by_me,
            "pages": note.data.get("pages", 2),
            "model": note.model,
            "created_at": note.created_at,
            "updated_at": note.updated_at,
        },
        "building": is_building(session, subject_id),
    }


@router.post("/subjects/{subject_id}/cheatsheet", status_code=202)
def build_cheatsheet(
    subject_id: int, req: CheatSheetRequest, session: Session = Depends(get_session)
) -> dict[str, bool]:
    if session.get(Subject, subject_id) is None:
        raise HTTPException(404, "Subject not found")
    if is_building(session, subject_id):
        raise HTTPException(409, "The cheat sheet is already being written")
    try:
        queue_cheatsheet(session, subject_id, req.pages, req.instructions)
    except JobError as e:
        raise HTTPException(409, str(e)) from e
    return {"queued": True}


@router.get("/subjects/{subject_id}/cheatsheet.pdf")
def cheatsheet_pdf(subject_id: int, session: Session = Depends(get_session)) -> Response:
    return _cheatsheet_pdf(session, subject_id, raw=False)


@router.get("/subjects/{subject_id}/cheatsheet-pdf-bytes")
def cheatsheet_pdf_bytes(subject_id: int, session: Session = Depends(get_session)) -> Response:
    return _cheatsheet_pdf(session, subject_id, raw=True)


def _cheatsheet_pdf(session: Session, subject_id: int, raw: bool) -> Response:
    subject = session.get(Subject, subject_id)
    if subject is None:
        raise HTTPException(404, "Subject not found")
    if current_subject_note(session, subject_id, CHEATSHEET_KIND) is None:
        raise HTTPException(404, "No cheat sheet yet")
    name = _filename(f"{subject.name} cheat sheet", "pdf")
    return _pdf(f"/print/subjects/{subject_id}/cheatsheet", name, raw)
