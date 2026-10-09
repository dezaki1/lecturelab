from __future__ import annotations

import shutil
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import delete, select

from backend.ai.providers.mock_provider import MockProvider
from backend.app import create_app
from backend.core.config import Settings
from backend.core.db import session_scope
from backend.core.jobs import Worker
from backend.core.models import ExamHint, Lecture, TranscriptSegment
from backend.pipeline.hints import backfill, detect, store_rule_hints
from tests.fakes import fake_factory
from tests.test_documents import script_segments

FIXTURES = Path(__file__).parent / "fixtures"
pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not on PATH")


@dataclass
class Seg:
    start: float
    end: float
    text: str


def test_keyword_detector_english_and_german() -> None:
    segs = [Seg(a, b, t) for a, b, t in script_segments()]
    hints = detect(segs)
    assert len(hints) == 1
    [h] = hints
    assert h["kind"] == "exam" and "will be in the exam" in h["quote"]
    assert h["end_s"] - h["start_s"] >= 10  # covers the explanation that follows

    german = [
        Seg(0, 5, "Heute geht es um Kerne."),
        Seg(60, 65, "Das ist sehr wichtig, merken Sie sich das."),
        Seg(70, 75, "Und das kommt in der Prüfung."),  # merged with the hint before
        Seg(200, 205, "Remember this: the kernel trick avoids the explicit mapping."),
        Seg(300, 305, "It is important to note the time."),  # not "this is important"
    ]
    out = detect(german)
    assert [(h["start_s"], h["kind"]) for h in out] == [(60, "exam"), (200, "important")]


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    settings.generation.auto = True
    with TestClient(create_app(settings, start_worker=False, source_factory=fake_factory())) as c:
        yield c


def _lecture(c: TestClient) -> int:
    sid = c.post("/api/subjects", json={"name": "ML"}).json()["id"]
    r = c.post(
        "/api/imports",
        params={"subject_id": sid, "filename": "lecture.m4a"},
        content=(FIXTURES / "lecture.m4a").read_bytes(),
    )
    lid = r.json()["id"]
    Worker().run_pending()  # import, transcribe, generate, index
    with session_scope() as s:  # placeholder transcript -> the real script with its exam hint
        s.exec(delete(TranscriptSegment).where(TranscriptSegment.lecture_id == lid))  # type: ignore[call-overload]
        for i, (a, b, text) in enumerate(script_segments()):
            s.add(TranscriptSegment(lecture_id=lid, idx=i, start=a, end=b, text=text))
        lecture = s.get(Lecture, lid)
        assert store_rule_hints(s, lecture) == 1  # type: ignore[arg-type]
        s.commit()
    return lid


def test_hints_are_confirmed_and_feed_the_other_sections(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    lid = _lecture(client)
    seen: dict[str, str] = {}
    original = MockProvider.complete

    def spy(self, req):  # type: ignore[no-untyped-def]
        seen[req.task] = req.instruction
        return original(self, req)

    monkeypatch.setattr(MockProvider, "complete", spy)
    assert client.post(f"/api/lectures/{lid}/generate", json={}).status_code == 202
    Worker().run_pending()

    # The hints call saw the keyword candidate and confirmed it (mock: kind "exam").
    assert '"Now this is important, and it will be in the exam."' in seen["hints"]
    m = client.get(f"/api/lectures/{lid}/materials").json()
    [hint] = m["hints"]
    assert hint["source"] == "llm" and hint["kind"] == "exam" and hint["topic"].startswith("Know:")
    # Later sections get the hint list after the cache breakpoint; the summary too.
    for task in ("notes", "questions", "flashcards", "summary"):
        assert "Exam hints: the lecturer flagged these moments" in seen[task], task
    assert "Exam hints" not in seen["hints"]
    assert m["explanations"] == []  # no confused bookmarks, no call
    assert "explanations" not in seen

    sid = client.get(f"/api/lectures/{lid}").json()["subject_id"]
    [row] = client.get(f"/api/subjects/{sid}/hints").json()
    assert row["lecture_number"] == 1 and row["kind"] == "exam"


def test_confused_bookmarks_get_explanations(client: TestClient) -> None:
    lid = _lecture(client)
    b1 = client.post(f"/api/lectures/{lid}/bookmarks", json={"t_s": 40, "kind": "confused"}).json()
    client.post(f"/api/lectures/{lid}/bookmarks", json={"t_s": 50, "kind": "important"})
    b3 = client.post(
        f"/api/lectures/{lid}/bookmarks",
        json={"t_s": 80, "kind": "question", "note": "why the diagonal?"},
    ).json()
    client.post(f"/api/lectures/{lid}/generate", json={"kinds": ["explanations"]})
    Worker().run_pending()

    ex = {
        e["bookmark_id"]: e
        for e in client.get(f"/api/lectures/{lid}/materials").json()["explanations"]
    }
    assert set(ex) == {b1["id"], b3["id"]}  # not the "important" bookmark
    assert ex[b1["id"]]["check_question"] and ex[b1["id"]]["source_refs"]
    assert ex[b1["id"]]["title"] == "The passage at 00:40"

    card = client.post(f"/api/explanations/{ex[b1['id']]['id']}/flashcard").json()
    assert card["front"] == "What was the main point?" and card["tags"] == ["confused"]
    again = client.post(f"/api/explanations/{ex[b1['id']]['id']}/flashcard").json()
    assert again["id"] == card["id"]  # no duplicate

    # Deleting the bookmark removes its explanation.
    client.delete(f"/api/lectures/{lid}/bookmarks/{b3['id']}")
    left = client.get(f"/api/lectures/{lid}/materials").json()["explanations"]
    assert [e["bookmark_id"] for e in left] == [b1["id"]]


def test_backfill_scans_old_lectures_once(client: TestClient) -> None:
    lid = _lecture(client)
    with session_scope() as s:
        s.exec(delete(ExamHint))  # type: ignore[call-overload]
        lecture = s.get(Lecture, lid)
        lecture.meta = {k: v for k, v in lecture.meta.items() if k != "hints_scanned"}  # type: ignore[union-attr]
        s.add(lecture)
        s.commit()
        assert backfill(s) == 1
        assert len(s.exec(select(ExamHint)).all()) == 1
        assert backfill(s) == 0
