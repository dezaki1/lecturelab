from __future__ import annotations

import shutil
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from backend.app import create_app
from backend.core.config import Settings
from backend.core.db import session_scope
from backend.core.jobs import Worker
from backend.core.library import abs_path
from backend.core.models import (
    Chunk,
    Deadline,
    ExamHint,
    Flashcard,
    Job,
    Lecture,
    Note,
    Question,
    Recap,
    Slide,
)
from backend.pipeline.lecture_ops import rebase
from tests.fakes import fake_factory
from tests.test_generate import _lecture

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not on PATH")


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    settings.generation.auto = True
    with TestClient(create_app(settings, start_worker=False, source_factory=fake_factory())) as c:
        yield c


def _setup(c: TestClient) -> tuple[int, int, int]:
    """A generated lecture filed under the wrong subject, plus a slide, a week recap and a
    cheat sheet built from it."""
    lid = _lecture(c, "Machine Learning")
    wrong = c.get(f"/api/lectures/{lid}").json()["subject_id"]
    right = c.post("/api/subjects", json={"name": "Reinforcement Learning"}).json()["id"]
    with session_scope() as s:
        lec = s.get(Lecture, lid)
        assert lec is not None
        img = abs_path(lec.folder) / "slides" / "1000.jpg"
        img.parent.mkdir(parents=True, exist_ok=True)
        img.write_bytes(b"jpg")
        s.add(Slide(lecture_id=lid, t_s=1.0, image_path=f"{lec.folder}/slides/1000.jpg"))
        monday = lec.started_at.astimezone().date()
        monday = monday.fromordinal(monday.toordinal() - monday.weekday())
        s.add(Recap(scope="week", subject_id=wrong, week_start=monday, status="ready"))
        s.add(Note(subject_id=wrong, kind="cheatsheet", content_md="PCA...", data={"pages": 1}))
        s.add(Note(subject_id=wrong, kind="course", content_md="Course", data={"topics": []}))
        s.commit()
    return lid, wrong, right


def _subjects_of(lid: int) -> set[int]:
    with session_scope() as s:
        out = set()
        for model in (Note, Flashcard, Question, ExamHint, Chunk, Deadline):
            out |= set(s.exec(select(model.subject_id).where(model.lecture_id == lid)).all())
        return out


def test_moving_a_lecture_takes_everything_along(client: TestClient) -> None:
    lid, wrong, right = _setup(client)
    assert _subjects_of(lid) == {wrong}
    with session_scope() as s:
        cards = s.exec(select(Flashcard).where(Flashcard.lecture_id == lid)).all()
        assert cards and s.exec(select(Deadline).where(Deadline.lecture_id == lid)).first()
    n_cards = len(cards)

    r = client.patch(f"/api/lectures/{lid}", json={"subject_id": right})
    assert r.status_code == 200, r.text
    lec = r.json()
    assert lec["subject_id"] == right and lec["moved_from"]["name"] == "Machine Learning"

    # Every row made from the lecture moved with it; nothing of it is left in the old subject.
    assert _subjects_of(lid) == {right}
    with session_scope() as s:
        assert len(s.exec(select(Flashcard).where(Flashcard.subject_id == right)).all()) == n_cards
        assert not s.exec(select(Flashcard).where(Flashcard.subject_id == wrong)).all()
        slide = s.exec(select(Slide).where(Slide.lecture_id == lid)).one()
        lecture = s.get(Lecture, lid)
        assert lecture is not None and "reinforcement-learning" in lecture.folder
        assert slide.image_path.startswith(lecture.folder + "/")
        assert abs_path(slide.image_path).read_bytes() == b"jpg"  # the file moved too
        assert abs_path(lecture.media_path).exists()  # type: ignore[arg-type]
        # The old subject has no lectures left: its overview and cheat sheet are retired.
        old_notes = s.exec(
            select(Note).where(Note.subject_id == wrong, Note.lecture_id == None)  # noqa: E711
        ).all()
        assert old_notes and not any(n.is_current for n in old_notes)
        recap = s.exec(select(Recap).where(Recap.subject_id == wrong)).one()
        assert recap.error.startswith("Outdated")

    hits = client.get("/api/search", params={"q": "placeholder", "subject_id": right}).json()
    assert hits and all(h["subject_id"] == right for h in hits)
    assert client.get("/api/search", params={"q": "placeholder", "subject_id": wrong}).json() == []
    assert client.get(f"/api/subjects/{wrong}/hub").json()["topics"] == []

    # The notice offers to rewrite the material for the new subject; regenerating clears it.
    client.post(f"/api/lectures/{lid}/generate", json={})
    Worker().run_pending()
    assert client.get(f"/api/lectures/{lid}").json()["moved_from"] is None
    assert _subjects_of(lid) == {right}


def test_dismiss_notice_and_cheatsheet_rebuild(client: TestClient) -> None:
    lid, _wrong, right = _setup(client)
    with session_scope() as s:  # the target subject already has a cheat sheet
        s.add(Note(subject_id=right, kind="cheatsheet", content_md="RL", data={"pages": 2}))
        s.commit()
    client.patch(f"/api/lectures/{lid}", json={"subject_id": right})
    with session_scope() as s:
        jobs = [j for j in s.exec(select(Job).where(Job.kind == "cheatsheet")).all()]
        assert [j.payload["subject_id"] for j in jobs] == [right]
        assert jobs[0].payload["pages"] == 2
    r = client.patch(f"/api/lectures/{lid}", json={"dismiss_moved": True})
    assert r.json()["moved_from"] is None


def test_deleting_a_lecture_refreshes_its_subject(client: TestClient) -> None:
    lid, wrong, _ = _setup(client)
    assert client.delete(f"/api/lectures/{lid}").status_code == 204
    assert _subjects_of(lid) == set()  # cascaded
    with session_scope() as s:
        assert not any(
            n.is_current for n in s.exec(select(Note).where(Note.subject_id == wrong)).all()
        )
        assert s.exec(select(Recap).where(Recap.subject_id == wrong)).one().error


def test_rebase() -> None:
    assert (
        rebase(
            "subjects/ml/2026-10-15_ml/slides/1.jpg", "subjects/ml/2026-10-15_ml", "subjects/rl/x"
        )
        == "subjects/rl/x/slides/1.jpg"
    )
    assert (
        rebase("subjects/ml/other/1.jpg", "subjects/ml/2026-10-15_ml", "x")
        == "subjects/ml/other/1.jpg"
    )
    assert (
        rebase("subjects\\ml\\a\\b.jpg", "subjects/ml/a", "subjects/rl/a") == "subjects/rl/a/b.jpg"
    )
    assert rebase(None, "a", "b") is None


def test_repair_lectures_moved_by_old_versions(client: TestClient) -> None:
    from backend.core.library import move_lecture as old_style_move
    from backend.core.models import Subject
    from backend.pipeline import lecture_ops

    lid, wrong, right = _setup(client)
    with session_scope() as s:  # what earlier versions did: only the folder and the lecture
        lec, target = s.get(Lecture, lid), s.get(Subject, right)
        assert lec is not None and target is not None
        old_style_move(lec, target)
        s.add(lec)
        s.commit()
        assert _subjects_of(lid) == {wrong}
        slide = s.exec(select(Slide).where(Slide.lecture_id == lid)).one()
        assert not abs_path(slide.image_path).exists()  # broken image
    with session_scope() as s:
        fixed = lecture_ops.repair(s)
    assert fixed[lid] > 0 and _subjects_of(lid) == {right}
    with session_scope() as s:
        slide = s.exec(select(Slide).where(Slide.lecture_id == lid)).one()
        assert abs_path(slide.image_path).read_bytes() == b"jpg"
    assert client.get(f"/api/lectures/{lid}").json()["moved_from"]["subject_id"] == wrong
    with session_scope() as s:
        assert lecture_ops.repair(s) == {}  # idempotent
