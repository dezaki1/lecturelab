from __future__ import annotations

import io
import shutil
import zipfile
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.core.config import Settings
from backend.core.jobs import Worker
from backend.exports import pdf
from backend.exports.vault import lecture_body, link_times, safe
from tests.fakes import fake_factory

FIXTURES = Path(__file__).parent / "fixtures"
pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not on PATH")


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    settings.generation.auto = True
    settings.generation.subject_hub = True
    with TestClient(create_app(settings, start_worker=False, source_factory=fake_factory())) as c:
        yield c


def _subject(c: TestClient) -> tuple[int, int]:
    sid = c.post("/api/subjects", json={"name": "Machine Learning: Supervised"}).json()["id"]
    lid = c.post(
        "/api/imports",
        params={"subject_id": sid, "filename": "PCA basics.m4a"},
        content=(FIXTURES / "lecture.m4a").read_bytes(),
    ).json()["id"]
    Worker().run_pending()  # import, transcribe, generate, index, course overview
    return sid, lid


def test_link_times_and_headings() -> None:
    assert link_times("see [12:34] and `[00:01]`", 5) == (
        "see [12:34](http://127.0.0.1:8765/lectures/5?t=754) and `[00:01]`"
    )
    body, heads = lecture_body("## ★ PCA [01:05]\ntext [1:00:00]\n```\n## not a heading\n```", 7)
    assert heads == ["★ PCA"]
    assert body.startswith("### ★ PCA\n[▶ 01:05](http://127.0.0.1:8765/lectures/7?t=65)")
    assert "lectures/7?t=3600" in body and "## not a heading" in body
    assert safe('A/B: "C"?') == "A B C"


def test_cheatsheet_and_vault(client: TestClient) -> None:
    sid, lid = _subject(client)
    assert client.get(f"/api/subjects/{sid}/cheatsheet").json() == {"note": None, "building": False}
    assert client.post(f"/api/subjects/{sid}/cheatsheet", json={"pages": 1}).status_code == 202
    assert client.post(f"/api/subjects/{sid}/cheatsheet", json={}).status_code == 409  # queued
    Worker().run_pending()
    sheet = client.get(f"/api/subjects/{sid}/cheatsheet").json()["note"]
    assert (
        sheet["title"] == "Mock cheat sheet" and sheet["pages"] == 1 and "★" in sheet["content_md"]
    )
    edited = client.patch(
        f"/api/notes/{sheet['id']}", json={"content_md": "## Mine\n- edited"}
    ).json()
    assert edited["version"] == 2 and edited["edited_by_me"]

    r = client.get(f"/api/export/vault?subject_id={sid}")
    assert (
        r.status_code == 200
        and "Machine Learning Supervised vault" in r.headers["content-disposition"]
    )
    z = zipfile.ZipFile(io.BytesIO(r.content))
    names = set(z.namelist())
    folder = "LectureLab/Machine Learning Supervised"
    assert {
        "LectureLab/README.md",
        f"{folder}/_Course overview.md",
        f"{folder}/Glossary.md",
        f"{folder}/Cheat sheet.md",
        f"{folder}/L01 Mock lecture topic.md",
    } <= names

    note = z.read(f"{folder}/L01 Mock lecture topic.md").decode()
    assert note.startswith('---\nsubject: "Machine Learning: Supervised"\nlecture: 1\n')
    assert f"lecturelab: http://127.0.0.1:8765/lectures/{lid}" in note
    assert "[[_Course overview|Course overview]]" in note
    assert "## Summary" in note and "## Notes" in note and "### ★ Topic 1" in note
    assert f"(http://127.0.0.1:8765/lectures/{lid}?t=" in note  # timestamps link back
    assert "#flashcards/machine-learning-supervised" in note and "\n?\n" in note
    assert "==" in note  # cloze card in Spaced Repetition syntax
    assert "> [!question]" in note and "> > [!success]- Answer" in note

    overview = z.read(f"{folder}/_Course overview.md").decode()
    assert "[[L01 Mock lecture topic#★ Topic 1|L1: Topic 1]]" in overview
    assert "```mermaid\nflowchart TD" in overview
    assert "- [[L01 Mock lecture topic]]" in overview
    glossary = z.read(f"{folder}/Glossary.md").decode()
    assert "## Principal component\n*Hauptkomponente*" in glossary
    assert "Lectures: [[L01 Mock lecture topic]]" in glossary
    assert "## Mine" in z.read(f"{folder}/Cheat sheet.md").decode()  # the edited version

    everything = zipfile.ZipFile(io.BytesIO(client.get("/api/export/vault").content))
    assert f"{folder}/_Course overview.md" in everything.namelist()


def test_pdf_endpoints(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    sid, lid = _subject(client)
    real_print = pdf.print_pdf
    urls: list[str] = []

    def fake_print(url: str) -> bytes:
        urls.append(url)
        return b"%PDF-1.7 fake"

    monkeypatch.setattr(pdf, "print_pdf", fake_print)
    r = client.get(f"/api/lectures/{lid}/export.pdf")
    assert r.status_code == 200 and r.content.startswith(b"%PDF")
    assert 'filename="Mock lecture topic.pdf"' in r.headers["content-disposition"]
    assert urls == [f"http://127.0.0.1:8765/print/lectures/{lid}"]
    assert client.get(f"/api/subjects/{sid}/cheatsheet.pdf").status_code == 404  # none yet
    # What the PDF button fetches: plain bytes, so download managers (IDM) don't grab them.
    r = client.get(f"/api/lectures/{lid}/pdf-bytes")
    assert r.content.startswith(b"%PDF") and "content-disposition" not in r.headers
    assert r.headers["content-type"] == "application/octet-stream"
    assert r.headers["x-filename"] == "Mock%20lecture%20topic.pdf"
    assert client.get(f"/api/subjects/{sid}/cheatsheet-pdf-bytes").status_code == 404

    # No Edge/Chrome: the real printer reports it and the UI falls back to "Save as PDF".
    monkeypatch.setattr(pdf, "print_pdf", real_print)
    monkeypatch.setattr(pdf, "find_browser", lambda: None)
    assert client.get("/api/export/pdf-available").json() == {"available": False}
    r = client.get(f"/api/lectures/{lid}/export.pdf")
    assert r.status_code == 501 and "No Edge or Chrome" in r.json()["detail"]
