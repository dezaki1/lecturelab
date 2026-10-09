from __future__ import annotations

import shutil
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import col, select

from backend.ai.providers.mock_provider import MockProvider
from backend.app import create_app
from backend.core.config import Settings
from backend.core.db import session_scope
from backend.core.jobs import Worker
from backend.core.models import Attempt, Exam, ExamItem, Job, Question, utcnow
from backend.pipeline.past_exams import style_block
from tests.fakes import fake_factory
from tests.pdfgen import make_pdf

FIXTURES = Path(__file__).parent / "fixtures"
pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not on PATH")


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    settings.generation.auto = True
    settings.generation.subject_hub = True
    with TestClient(create_app(settings, start_worker=False, source_factory=fake_factory())) as c:
        yield c


def _lecture(c: TestClient, sid: int, name: str = "PCA.m4a") -> int:
    r = c.post(
        "/api/imports",
        params={"subject_id": sid, "filename": name},
        content=(FIXTURES / "lecture.m4a").read_bytes(),
    )
    lecture_id = r.json()["id"]
    Worker().run_pending()  # import, transcribe, generate, index, course overview
    return lecture_id


def _past_exam(c: TestClient, sid: int, tmp_path: Path) -> dict:
    pdf = make_pdf(
        tmp_path / "exam 2025.pdf",
        [
            ["Final exam", "Working time: 90 minutes", "1. Define PCA in one sentence (10 points)"],
            ["2. Compute the variance of 1, 2, 3 (20 points)"],
        ],
    )
    r = c.post(
        f"/api/subjects/{sid}/documents",
        params={"filename": pdf.name, "kind": "past_exam"},
        content=pdf.read_bytes(),
    )
    assert r.status_code == 201, r.text
    return r.json()


def test_past_exam_is_analysed_and_shapes_questions(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sid = client.post("/api/subjects", json={"name": "ML"}).json()["id"]
    doc = _past_exam(client, sid, tmp_path)
    assert doc["kind"] == "past_exam" and doc["processing"]
    assert Worker().run_pending() == 2  # read the PDF, then analyse it
    [doc] = client.get(f"/api/subjects/{sid}/documents").json()
    pe = doc["past_exam"]
    assert pe["duration_min"] == 90 and pe["total_points"] == 30 and pe["questions"] == 2
    assert {t["name"] for t in pe["topics"]} == {"PCA", "Basics"} and not pe["ocr"]

    with session_scope() as s:
        block = style_block(s, sid)
    assert "Final exam WS 2024/25 (2025-01-30, 90 min, 30 points" in block
    assert "Small integer numbers" in block and "[calculation, 20 pts] 2. Compute" not in block
    assert "[calculation, 20 pts] Compute the variance" in block
    assert "Topic weighting in past exams: Basics 67%; PCA 33%" in block

    # Generated exam questions get the style block (after the cache breakpoint).
    seen: dict[str, str] = {}
    original = MockProvider.complete

    def spy(self, req):  # type: ignore[no-untyped-def]
        seen[req.task] = req.instruction
        return original(self, req)

    monkeypatch.setattr(MockProvider, "complete", spy)
    _lecture(client, sid)
    assert "Past exams of this course" in seen["questions"]
    assert "Past exams of this course" not in seen["flashcards"]

    opts = client.get("/api/exams/options", params={"subject_id": sid}).json()
    assert opts["suggested_duration"] == 90 and opts["points_per_min"] == pytest.approx(30 / 90)
    assert opts["topics"] and opts["bank"] == {"mcq": 1, "short": 1}

    r = client.post(f"/api/documents/{doc['id']}/analyse")
    assert r.status_code == 202 and r.json()["analysing"]
    assert client.post(f"/api/documents/{doc['id']}/analyse").status_code == 409  # queued
    Worker().run_pending()


def test_generated_exam_sit_and_ai_grading(client: TestClient) -> None:
    sid = client.post("/api/subjects", json={"name": "ML"}).json()["id"]
    _lecture(client, sid)
    topics = client.get("/api/exams/options", params={"subject_id": sid}).json()["topics"]
    assert [t["name"] for t in topics] == ["Topic 1", "Topic 2", "Topic 3"]  # one per name

    r = client.post(
        "/api/exams",
        json={"subject_id": sid, "duration_min": 30, "topics": ["Topic 2"]},
    )
    assert r.status_code == 201, r.text
    exam = r.json()
    assert exam["status"] == "building" and exam["title"] == "ML: Topic 2"
    assert client.post(f"/api/exams/{exam['id']}/start").status_code == 409  # not built yet
    assert Worker().run_pending() == 1
    exam = client.get(f"/api/exams/{exam['id']}").json()
    assert exam["status"] == "ready" and exam["title"] == "ML: Mock exam"
    assert exam["max_points"] == 6
    mcq, long = exam["items"]
    # While sitting: no answers, no rubric, no "correct" flags.
    assert mcq["question"]["options"] == [{"text": "The right one"}, {"text": "A wrong one"}]
    assert "answer" not in mcq["question"] and "rubric" not in long["question"]
    assert long["question"]["topic"] == "Topic 2" and "score" not in long

    exam = client.post(f"/api/exams/{exam['id']}/start").json()
    assert exam["status"] == "running" and 1790 < exam["remaining_s"] <= 1800
    base = f"/api/exams/{exam['id']}/items"
    assert client.patch(f"{base}/{mcq['id']}", json={"selected": [5]}).status_code == 409
    assert client.patch(f"{base}/{mcq['id']}", json={"selected": [0]}).status_code == 200
    r = client.patch(f"{base}/{long['id']}", json={"answer_text": "It means...", "flagged": True})
    assert r.status_code == 200 and r.json()["remaining_s"] > 0

    exam = client.post(f"/api/exams/{exam['id']}/submit").json()
    assert exam["status"] == "grading"
    mcq, long = exam["items"]
    assert mcq["score"] == 2 and mcq["graded_by"] == "auto" and long["score"] is None
    assert mcq["question"]["options"][0]["correct"] is True  # revealed after submitting
    assert client.patch(f"{base}/{long['id']}", json={"answer_text": "late"}).status_code == 409

    assert Worker().run_pending() == 1
    exam = client.get(f"/api/exams/{exam['id']}").json()
    assert exam["status"] == "graded" and exam["score"] == 5 and exam["max_points"] == 6
    assert exam["feedback_md"] == "Solid work; revise the details."
    long = exam["items"][1]
    # Clamped to the rubric: full first criterion, half the second, the bogus one ignored.
    assert [a["points"] for a in long["rubric_awarded"]] == [2, 1] and long["score"] == 3
    assert long["graded_by"] == "ai" and long["flagged"] and long["answer_text"] == "It means..."
    [src] = long["question"]["sources"]
    assert src["label"].startswith("L1 · ") and src["lecture_id"]

    with session_scope() as s:
        attempts = s.exec(select(Attempt).order_by(col(Attempt.id))).all()
        assert [(a.score, a.max_points, a.correct) for a in attempts] == [
            (2, 2, True),
            (3, 4, False),
        ]
        assert attempts[1].rubric_checked == [0]
        sim_ids = [q.id for q in s.exec(select(Question).where(Question.kind == "sim")).all()]
        assert len(sim_ids) == 2
    # Simulator questions stay out of practice and the lecture's question list.
    practice = client.get("/api/practice/questions", params={"subject_id": sid}).json()
    assert not {p["id"] for p in practice} & set(sim_ids)

    history = client.get("/api/exams", params={"subject_id": sid}).json()
    assert [(h["id"], h["score"], h["items"]) for h in history] == [(exam["id"], 5, 2)]

    assert client.delete(f"/api/exams/{exam['id']}").status_code == 204
    with session_scope() as s:
        assert not s.exec(select(Question).where(Question.kind == "sim")).all()
        assert not s.exec(select(ExamItem)).all()
        assert s.exec(select(Question).where(Question.kind == "exam")).all()  # bank untouched


def test_bank_exam_auto_submits_after_the_deadline(client: TestClient) -> None:
    sid = client.post("/api/subjects", json={"name": "ML"}).json()["id"]
    _lecture(client, sid)
    r = client.post(
        "/api/exams",
        json={"subject_id": sid, "duration_min": 5, "source": "bank", "types": ["mcq", "short"]},
    )
    exam = r.json()
    # Instant (no model call): both bank questions, choice first.
    assert exam["status"] == "ready" and [i["question"]["type"] for i in exam["items"]] == [
        "mcq",
        "short",
    ]
    exam = client.post(f"/api/exams/{exam['id']}/start").json()
    with session_scope() as s:
        row = s.get(Exam, exam["id"])
        assert row is not None
        row.deadline = utcnow() - timedelta(minutes=1)
        s.add(row)
        s.commit()
    # The browser was closed: the next read submits; nothing open was answered, so no AI call.
    exam = client.get(f"/api/exams/{exam['id']}").json()
    assert exam["status"] == "graded" and exam["score"] == 0 and exam["max_points"] == 3
    assert [i["feedback"] for i in exam["items"]] == ["Not answered.", "Not answered."]
    with session_scope() as s:
        assert not s.exec(select(Job).where(Job.kind == "exam_grade")).all()

    r = client.post(
        "/api/exams",
        json={"subject_id": sid, "duration_min": 30, "source": "bank", "types": ["code"]},
    )
    assert r.status_code == 422 and "No matching questions" in r.json()["detail"]


def test_grading_that_skips_an_answer_fails_and_can_be_retried(client: TestClient) -> None:
    sid = client.post("/api/subjects", json={"name": "ML"}).json()["id"]
    _lecture(client, sid)
    exam = client.post(
        "/api/exams", json={"subject_id": sid, "duration_min": 10, "source": "bank"}
    ).json()
    client.post(f"/api/exams/{exam['id']}/start")
    short = exam["items"][1]
    client.patch(f"/api/exams/{exam['id']}/items/{short['id']}", json={"answer_text": "SKIP"})
    client.post(f"/api/exams/{exam['id']}/submit")
    Worker().run_pending()
    exam = client.get(f"/api/exams/{exam['id']}").json()
    assert exam["status"] == "failed" and "did not grade every answer" in exam["error"]
    assert client.delete(f"/api/exams/{exam['id']}").status_code == 204

    exam = client.post(
        "/api/exams", json={"subject_id": sid, "duration_min": 10, "source": "bank"}
    ).json()
    client.post(f"/api/exams/{exam['id']}/start")
    short = exam["items"][1]
    client.patch(f"/api/exams/{exam['id']}/items/{short['id']}", json={"answer_text": "SKIP"})
    client.post(f"/api/exams/{exam['id']}/submit")
    Worker().run_pending()
    with session_scope() as s:
        item = s.get(ExamItem, short["id"])
        assert item is not None
        item.answer_text = "Center the data."
        s.add(item)
        s.commit()
    r = client.post(f"/api/exams/{exam['id']}/retry")
    assert r.status_code == 202 and r.json()["status"] == "grading"
    Worker().run_pending()
    exam = client.get(f"/api/exams/{exam['id']}").json()
    assert exam["status"] == "graded" and exam["items"][1]["score"] == 1


def test_scanned_past_exam_is_read_with_ocr(client: TestClient, tmp_path: Path) -> None:
    from PIL import Image, ImageDraw, ImageFont

    page = Image.new("RGB", (1240, 1754), "white")  # A4 at 150 dpi, no text layer
    d = ImageDraw.Draw(page)
    font = ImageFont.load_default(44)
    for k, line in enumerate(["Exam 60 minutes", "1. Define PCA (10 points)"]):
        d.text((100, 150 + 120 * k), line, fill="black", font=font)
    pdf = tmp_path / "scan.pdf"
    page.save(pdf, "PDF", resolution=150)

    sid = client.post("/api/subjects", json={"name": "ML"}).json()["id"]
    r = client.post(
        f"/api/subjects/{sid}/documents",
        params={"filename": pdf.name, "kind": "past_exam"},
        content=pdf.read_bytes(),
    )
    assert r.status_code == 201
    assert Worker().run_pending() == 2
    [doc] = client.get(f"/api/subjects/{sid}/documents").json()
    assert doc["past_exam"]["ocr"] and doc["past_exam"]["duration_min"] == 60
    assert doc["past_exam"]["questions"] == 1


def test_cancelled_build_marks_the_exam_failed(client: TestClient) -> None:
    sid = client.post("/api/subjects", json={"name": "ML"}).json()["id"]
    _lecture(client, sid)
    exam = client.post("/api/exams", json={"subject_id": sid, "duration_min": 30}).json()
    assert client.delete(f"/api/exams/{exam['id']}").status_code == 409  # still being written
    [job] = [j for j in client.get("/api/jobs?status=active").json() if j["kind"] == "exam_build"]
    assert job["label"] == "Writing exam"
    client.post(f"/api/jobs/{job['id']}/cancel")
    exam = client.get(f"/api/exams/{exam['id']}").json()
    assert exam["status"] == "failed" and exam["error"] == "The job was cancelled"
    assert client.post(f"/api/exams/{exam['id']}/retry").json()["status"] == "building"
    Worker().run_pending()
    assert client.get(f"/api/exams/{exam['id']}").json()["status"] == "ready"
