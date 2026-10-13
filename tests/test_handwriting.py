from __future__ import annotations

import io
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from backend.ai.types import LLMNoVision
from backend.app import create_app
from backend.core.config import Settings
from backend.core.db import session_scope
from backend.core.jobs import Worker
from backend.core.models import Lecture, TranscriptSegment
from backend.pipeline import slides as slide_pipeline
from backend.pipeline.generate import build_context
from tests.pdfgen import make_pdf
from tests.test_recaps import _lecture


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(settings, start_worker=False)) as c:
        yield c


def _photo(rotated: bool = False) -> bytes:
    img = Image.new("RGB", (3000, 2000), "white")
    buf = io.BytesIO()
    exif = Image.Exif()
    if rotated:
        exif[0x0112] = 6  # "rotate 90° clockwise to display"
    img.save(buf, "JPEG", exif=exif)
    return buf.getvalue()


def _indexed_lecture() -> int:
    lid = _lecture("Machine Learning", "ml", datetime(2026, 10, 5, 10, tzinfo=UTC), "PCA")
    with session_scope() as s:
        lec = s.get(Lecture, lid)
        lec.stages = {**lec.stages, "index": "done"}  # type: ignore[union-attr]
        s.add(lec)
        s.add(TranscriptSegment(lecture_id=lid, idx=0, start=0, end=5, text="Today: PCA."))
        s.commit()
    return lid


def _upload(c: TestClient, lid: int, name: str, data: bytes):  # type: ignore[no-untyped-def]
    return c.post(
        f"/api/lectures/{lid}/documents",
        params={"filename": name, "kind": "handwritten"},
        content=data,
    )


def test_photo_is_read_indexed_and_editable(client: TestClient) -> None:
    lid = _indexed_lecture()
    r = _upload(client, lid, "IMG_2041.jpg", _photo(rotated=True))
    assert r.status_code == 201, r.text
    doc = r.json()["id"]
    assert _upload(client, lid, "notes.jpg", b"not an image").status_code == 415
    assert _upload(client, lid, "notes.gif", b"GIF89a").status_code == 415
    r = client.post(
        f"/api/lectures/{lid}/documents",
        params={"filename": "x.jpg", "kind": "slides"},
        content=_photo(),
    )
    assert r.status_code == 415  # photos only for handwritten notes

    Worker().run_pending()  # document job, then the index job
    notes = client.get(f"/api/lectures/{lid}/handwritten").json()
    assert len(notes) == 1 and notes[0]["method"] == "vision" and not notes[0]["processing"]
    page = notes[0]["pages"][0]
    assert page["text"].startswith("## Eigenvectors (page 1)") and page["unclear"]
    assert page["summary"] == "Mock page 1" and not page["edited"]

    img = client.get(f"/api/documents/{doc}/pages/1/image")
    assert img.status_code == 200 and img.headers["content-type"] == "image/jpeg"
    shown = Image.open(io.BytesIO(img.content))
    assert shown.size == (2000, 3000)  # EXIF orientation applied: portrait page
    assert client.get(f"/api/documents/{doc}/pages/2/image").status_code == 404
    assert client.get(f"/api/documents/{doc}/file").headers["content-type"] == "image/jpeg"

    hits = client.get("/api/search", params={"q": "eigenvectors covariance"}).json()
    mine = [h for h in hits if h["source"] == "handwritten"]
    assert mine and mine[0]["heading"] == "My notes, p. 1: Mock page 1"

    r = client.patch(
        f"/api/documents/{doc}/pages/1", json={"text": "## Eigenvectors\nThe Rayleigh quotient."}
    )
    assert r.status_code == 200 and r.json()["edited"]
    Worker().run_pending()
    hits = client.get("/api/search", params={"q": "Rayleigh quotient"}).json()
    assert any(h["source"] == "handwritten" for h in hits)

    with session_scope() as s:
        ctx = build_context(s, s.get(Lecture, lid))  # type: ignore[arg-type]
    assert "## The student's own handwritten notes" in ctx.context
    assert "[IMG_2041, page 1]\n## Eigenvectors\nThe Rayleigh quotient." in ctx.context

    assert client.delete(f"/api/documents/{doc}").status_code == 204
    assert client.get(f"/api/lectures/{lid}/handwritten").json() == []


def test_pdf_pages_and_local_fallback(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lid = _indexed_lecture()
    pdf = make_pdf(tmp_path / "n.pdf", [["Page one"], ["Page two"], ["Page three"]]).read_bytes()
    assert _upload(client, lid, "Samsung Notes.pdf", pdf).status_code == 201
    Worker().run_pending()
    pages = client.get(f"/api/lectures/{lid}/handwritten").json()[0]["pages"]
    assert [p["summary"] for p in pages] == ["Mock page 1", "Mock page 2", "Mock page 3"]

    # A local model without vision: the pages go through local OCR instead.
    from backend.ai.providers import mock_provider

    def no_vision(self, req):  # type: ignore[no-untyped-def]
        raise LLMNoVision("text only")

    monkeypatch.setattr(mock_provider.MockProvider, "complete", no_vision)
    monkeypatch.setattr(slide_pipeline, "run_ocr", lambda files, ctx: ["ocr text"] * len(files))
    assert _upload(client, lid, "photo.png", _png()).status_code == 201
    Worker().run_pending()
    notes = client.get(f"/api/lectures/{lid}/handwritten").json()
    assert notes[1]["method"] == "ocr" and notes[1]["pages"][0]["text"] == "ocr text"


def _png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (800, 600), "white").save(buf, "PNG")
    return buf.getvalue()


def test_needs_a_provider(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.pipeline import handwriting

    lid = _indexed_lecture()
    monkeypatch.setattr(handwriting, "is_configured", lambda: False)
    _upload(client, lid, "a.png", _png())
    Worker().run_pending()
    jobs = client.get("/api/jobs").json()
    rows = jobs if isinstance(jobs, list) else jobs.get("jobs", [])
    failed = [j for j in rows if j["kind"] == "document"]
    assert failed and failed[0]["status"] == "failed" and "AI provider" in failed[0]["error"]
