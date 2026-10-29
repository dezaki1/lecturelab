"""PDFs studied like a lecture: pages on a minute each, page refs, the rest of the app unchanged."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from sqlmodel import col, select

from backend.core.db import session_scope
from backend.core.jobs import Worker
from backend.core.models import Flashcard, Lecture, LectureSource, Note, Slide, TranscriptSegment
from backend.pipeline import study_docs
from tests.pdfgen import make_pdf

PAGES = [
    ["Gradient descent", "We minimise a loss by stepping against the gradient.", "Step size eta."],
    ["Momentum", "Momentum adds a fraction of the previous step to the current one."],
    ["Adam", "Adam keeps running means of the gradient and of its square."],
]


def _subject(c: TestClient) -> int:
    return c.post("/api/subjects", json={"name": "Optimisation"}).json()["id"]


def test_pdf_studied_like_a_lecture(client: TestClient, tmp_path: Path) -> None:
    sid = _subject(client)
    pdf = make_pdf(tmp_path / "Script chapter 2.pdf", PAGES)
    r = client.post(
        f"/api/subjects/{sid}/documents",
        params={"filename": "Script chapter 2.pdf", "study": "true"},
        content=pdf.read_bytes(),
    )
    assert r.status_code == 201, r.text
    assert r.json()["kind"] == "study"
    Worker().run_pending()

    [lec] = client.get("/api/lectures", params={"subject_id": sid}).json()
    detail = client.get(f"/api/lectures/{lec['id']}").json()
    assert detail["source"] == "document" and detail["pages"] == 3
    assert detail["status"] == "ready" and detail["title"].startswith("Script chapter 2")
    assert detail["duration_s"] == 3 * study_docs.PAGE_S and not detail["has_audio"]
    with session_scope() as s:
        segs = s.exec(
            select(TranscriptSegment)
            .where(TranscriptSegment.lecture_id == lec["id"])
            .order_by(col(TranscriptSegment.idx))
        ).all()
        # page p starts at minute p-1; its paragraphs get the seconds
        assert study_docs.page_of(segs[0].start) == 1
        assert {study_docs.page_of(x.start) for x in segs} == {1, 2, 3}
        assert any("Adam keeps running means" in x.text and x.start == 120 for x in segs)
        slides = s.exec(select(Slide).where(Slide.lecture_id == lec["id"])).all()
        assert sorted((x.page, x.t_s) for x in slides) == [(1, 0.0), (2, 60.0), (3, 120.0)]
        # generated material points at pages
        cards = s.exec(select(Flashcard).where(Flashcard.lecture_id == lec["id"])).all()
        refs = [ref for card in cards for ref in card.source_refs]
        assert refs and all(ref["page"] == study_docs.page_of(ref["start_s"]) for ref in refs)
        assert s.exec(select(Note).where(Note.lecture_id == lec["id"])).first() is not None


def test_background_pdf_can_be_studied_later(client: TestClient, tmp_path: Path) -> None:
    sid = _subject(client)
    pdf = make_pdf(tmp_path / "Paper.pdf", PAGES[:2])
    doc = client.post(
        f"/api/subjects/{sid}/documents", params={"filename": "Paper.pdf"}, content=pdf.read_bytes()
    ).json()
    Worker().run_pending()
    assert client.get("/api/lectures", params={"subject_id": sid}).json() == []  # background
    r = client.post(f"/api/documents/{doc['id']}/study")
    assert r.status_code == 202, r.text
    Worker().run_pending()
    [lec] = client.get("/api/lectures", params={"subject_id": sid}).json()
    assert client.get(f"/api/lectures/{lec['id']}").json()["pages"] == 2
    assert client.post(f"/api/documents/{doc['id']}/study").status_code == 404  # moved into it


def test_dense_page_fits_its_minute() -> None:
    text = "\n\n".join(f"Paragraph {i}." for i in range(200))
    parts = study_docs.paragraphs(text)
    assert len(parts) == study_docs.PAGE_S - 1 and "Paragraph 199." in parts[-1]
    assert study_docs.paragraphs("one line\nnext line of it") == ["one line next line of it"]


def test_search_labels_pages(client: TestClient) -> None:
    from backend.search.retrieve import Hit

    hit = Hit(
        chunk_id=1, lecture_id=1, subject_id=1, subject_name="Optimisation", lecture_number=4,
        lecture_title="Script", started_at=None, source="transcript", note_kind="", heading="",
        text="", snippet="", start_s=125.0, end_s=126.0, score=1.0, keyword_rank=1,
        semantic_rank=None, page=3,
    )  # fmt: skip
    assert hit.label() == "L4 · p. 3"
    with session_scope() as s:
        assert not study_docs.is_document(s.get(Lecture, 999))
    assert LectureSource.document.value == "document"
