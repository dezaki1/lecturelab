from __future__ import annotations

import shutil
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw, ImageFont
from sqlmodel import select

from backend.ai.providers.mock_provider import MockProvider
from backend.app import create_app
from backend.core.config import Settings
from backend.core.db import session_scope
from backend.core.jobs import Worker
from backend.core.models import Attempt, Exam, Question
from tests.fakes import fake_factory
from tests.pdfgen import make_pdf

FIXTURES = Path(__file__).parent / "fixtures"
pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not on PATH")

PAST_EXAM = [
    ["Machine Learning - Final exam (90 min)"],
    ["1) Derive the PCA objective (6 P)", "2) Compute the eigenvalues of a 2x2 matrix (4 P)"],
    ["3) Explain why centering matters (3 P)"],
]


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    settings.generation.auto = True
    with TestClient(create_app(settings, start_worker=False, source_factory=fake_factory())) as c:
        yield c


def _subject_with_lecture(c: TestClient) -> int:
    sid = c.post("/api/subjects", json={"name": "ML"}).json()["id"]
    c.post(
        "/api/imports",
        params={"subject_id": sid, "filename": "lecture.m4a"},
        content=(FIXTURES / "lecture.m4a").read_bytes(),
    )
    Worker().run_pending()  # import, transcribe, generate (2 exam questions), index
    return sid


def _upload_past_exam(c: TestClient, sid: int, pdf: Path) -> None:
    r = c.post(
        f"/api/subjects/{sid}/documents",
        params={"filename": pdf.name, "kind": "past_exam"},
        content=pdf.read_bytes(),
    )
    assert r.status_code == 201, r.text
    Worker().run_pending()  # document (text), past_exam analysis


def test_past_exam_profile_and_question_style(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sid = _subject_with_lecture(client)
    _upload_past_exam(client, sid, make_pdf(tmp_path / "exam2024.pdf", PAST_EXAM))
    p = client.get(f"/api/subjects/{sid}/exam-profile").json()
    [doc] = p["past_exams"]
    assert doc["analysed"] and doc["questions"] == 3
    prof = p["profile"]
    assert prof["duration_min"] == 90 and prof["total_points"] == 13
    assert prof["types"]["short"] > prof["types"]["calculation"] > 0
    assert prof["style_notes"] and len(prof["examples"]) == 2  # one per type
    assert p["bank"] == {"mcq": 1, "short": 1}

    # Regenerated exam questions get the past exams' style after the cache breakpoint.
    seen: dict[str, str] = {}
    original = MockProvider.complete

    def spy(self, req):  # type: ignore[no-untyped-def]
        seen[req.task] = req.instruction
        return original(self, req)

    monkeypatch.setattr(MockProvider, "complete", spy)
    lid = client.get(f"/api/lectures?subject_id={sid}").json()[0]["id"]
    client.post(f"/api/lectures/{lid}/generate", json={"kinds": ["questions"]})
    Worker().run_pending()
    assert "Past exams of this course (1 analysed)" in seen["questions"]
    assert "Derivations by hand" in seen["questions"]


def test_scanned_past_exam_is_read_with_ocr(client: TestClient, tmp_path: Path) -> None:
    sid = client.post("/api/subjects", json={"name": "ML"}).json()["id"]
    img = Image.new("RGB", (1240, 1754), "white")  # A4 at 150 dpi, no text layer
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("arial.ttf", 40)
    except OSError:
        font = ImageFont.load_default(40)
    draw.text((100, 150), "1) Derive the PCA objective (6 P)", fill="black", font=font)
    draw.text((100, 260), "2) Explain the kernel trick (4 P)", fill="black", font=font)
    pdf = tmp_path / "scan.pdf"
    img.save(pdf, "PDF", resolution=150)
    _upload_past_exam(client, sid, pdf)
    [doc] = client.get(f"/api/subjects/{sid}/exam-profile").json()["past_exams"]
    assert doc["has_text"] and doc["analysed"] and doc["questions"] == 2


def test_exam_build_take_grade(client: TestClient) -> None:
    sid = _subject_with_lecture(client)
    r = client.post(
        f"/api/subjects/{sid}/exams",
        json={"duration_min": 30, "target_points": 11, "types": ["mcq", "short"]},
    )
    assert r.status_code == 201
    exam_id = r.json()["id"]
    assert r.json()["status"] == "building"
    Worker().run_pending()  # build: bank (2 + 1 points) + new questions (4 points each)

    e = client.get(f"/api/exams/{exam_id}").json()
    assert e["status"] == "ready" and e["time_limit_s"] == 1800
    answers = e["answers"]
    types = [a["question"]["type"] for a in answers]
    assert types[0] == "mcq" and types.count("short") == 3  # 1 bank + 2 new
    assert e["total_points"] == 11
    assert all("correct" not in o for a in answers for o in a["question"]["options"])
    assert "answer" not in answers[0]["question"]  # solutions hidden before grading
    with session_scope() as s:
        new = s.exec(select(Question).where(Question.generation == 0)).all()
        assert len(new) == 2 and all(q.source_refs for q in new)
        mcq = s.get(Question, answers[0]["question"]["id"])
        right = [i for i, o in enumerate(mcq.options) if o["correct"]]  # type: ignore[union-attr]

    assert (
        client.put(f"/api/exams/{exam_id}/answers/{answers[0]['id']}", json={}).status_code == 409
    )
    started = client.post(f"/api/exams/{exam_id}/start").json()
    assert started["status"] == "in_progress" and started["deadline"]
    client.put(f"/api/exams/{exam_id}/answers/{answers[0]['id']}", json={"selected": right})
    client.put(
        f"/api/exams/{exam_id}/answers/{answers[1]['id']}",
        json={"answer_text": "The correct idea."},
    )
    client.put(f"/api/exams/{exam_id}/answers/{answers[2]['id']}", json={"answer_text": "Unsure."})
    bad = client.put(f"/api/exams/{exam_id}/answers/{answers[0]['id']}", json={"selected": [9]})
    assert bad.status_code == 409

    assert client.post(f"/api/exams/{exam_id}/submit").json()["status"] == "grading"
    assert client.post(f"/api/exams/{exam_id}/submit").status_code == 409
    Worker().run_pending()

    g = client.get(f"/api/exams/{exam_id}").json()
    assert g["status"] == "graded" and g["summary_md"] == "Mock summary."
    scores = [a["score"] for a in g["answers"]]
    full = answers[1]["max_points"]
    # mcq right: full; "correct" answer: rubric clamped to full; "Unsure.": half; empty: 0
    assert scores[0] == answers[0]["max_points"] and scores[1] == full
    assert scores[2] == pytest.approx(answers[2]["max_points"] / 2)
    assert scores[3] == 0 and g["answers"][3]["feedback"] == "No answer given."
    assert g["score"] == pytest.approx(sum(scores))
    assert g["answers"][0]["question"]["options"][right[0]]["correct"]
    assert g["answers"][1]["rubric_scores"] and g["answers"][1]["question"]["source_refs"]
    assert g["config"]["late"] is False
    with session_scope() as s:
        attempts = s.exec(select(Attempt).where(Attempt.exam_id == exam_id)).all()
        assert len(attempts) == 4

    hist = client.get(f"/api/exams?subject_id={sid}").json()
    assert [h["id"] for h in hist] == [exam_id] and hist[0]["score"] == g["score"]
    assert client.delete(f"/api/exams/{exam_id}").status_code == 204
    with session_scope() as s:
        assert len(s.exec(select(Attempt)).all()) == 4  # practice history is kept


def test_late_submission_is_flagged(client: TestClient) -> None:
    sid = _subject_with_lecture(client)
    exam_id = client.post(
        f"/api/subjects/{sid}/exams", json={"duration_min": 10, "allow_new": False}
    ).json()["id"]
    Worker().run_pending()
    client.post(f"/api/exams/{exam_id}/start")
    with session_scope() as s:
        exam = s.get(Exam, exam_id)
        exam.started_at = exam.started_at - timedelta(minutes=13)  # type: ignore[union-attr, operator]
        s.add(exam)
        s.commit()
    client.post(f"/api/exams/{exam_id}/submit")
    Worker().run_pending()
    assert client.get(f"/api/exams/{exam_id}").json()["config"]["late"] is True


def test_no_questions_fails_with_a_reason(client: TestClient) -> None:
    sid = client.post("/api/subjects", json={"name": "Empty"}).json()["id"]
    exam_id = client.post(f"/api/subjects/{sid}/exams", json={"allow_new": False}).json()["id"]
    Worker().run_pending()
    e = client.get(f"/api/exams/{exam_id}").json()
    assert e["status"] == "failed" and "No exam questions" in e["error"]
