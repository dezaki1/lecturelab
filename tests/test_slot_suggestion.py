from __future__ import annotations

import shutil
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from backend.app import create_app
from backend.core.config import Settings
from backend.core.db import session_scope
from backend.core.jobs import Worker
from backend.core.media import probe
from backend.core.models import Flashcard, Note
from backend.pipeline.ingest import guess_started_at
from tests.fakes import fake_factory
from tests.test_timetable import ics

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not on PATH")
FIXTURE = Path(__file__).parent / "fixtures" / "lecture.m4a"
MODIFIED = datetime(2026, 10, 12, 10, 40, tzinfo=UTC)  # the tablet's file time


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    settings.generation.auto = True
    with TestClient(create_app(settings, start_worker=False, source_factory=fake_factory())) as c:
        yield c


def _setup(c: TestClient) -> tuple[int, int]:
    ml = c.post("/api/subjects", json={"name": "Machine Learning: Supervised Techniques"}).json()
    rl = c.post("/api/subjects", json={"name": "Reinforcement Learning"}).json()
    started = guess_started_at(probe(FIXTURE), MODIFIED)
    cal = ics(
        (
            "344.031 VL Machine Learning: Supervised Techniques",
            started - timedelta(minutes=5),
            90,
            "",
        )
    )
    c.post("/api/timetable/upload", params={"filename": "kusss.ics"}, content=cal)
    return ml["id"], rl["id"]


def _import(c: TestClient, subject_id: int) -> dict:
    r = c.post(
        "/api/imports",
        params={
            "subject_id": subject_id,
            "filename": "Screen_Recording_20261012.mp4",
            "last_modified_ms": int(MODIFIED.timestamp() * 1000),
        },
        content=FIXTURE.read_bytes(),
    )
    assert r.status_code in (200, 201), r.text
    return r.json()


def test_import_into_the_wrong_subject_is_noticed_and_moved_in_time(client: TestClient) -> None:
    ml, rl = _setup(client)
    lec = _import(client, rl)  # filed under RL by mistake
    hint = client.get(f"/api/lectures/{lec['id']}").json()["slot_suggestion"]
    assert hint["subject_id"] == ml and hint["kind"] == "VL"

    # Still processing: the move is queued and runs before transcription and generation.
    r = client.patch(f"/api/lectures/{lec['id']}", json={"subject_id": ml})
    assert r.status_code == 200, r.text
    assert r.json()["move_queued"] == ml and r.json()["slot_suggestion"] is None
    Worker().run_pending()
    done = client.get(f"/api/lectures/{lec['id']}").json()
    assert done["subject_id"] == ml and done["move_queued"] is None
    assert done["moved_from"] is None  # nothing had been written yet: no rewrite needed
    with session_scope() as s:
        assert {n.subject_id for n in s.exec(select(Note).where(Note.lecture_id == lec["id"]))} == {
            ml
        }
        cards = s.exec(select(Flashcard).where(Flashcard.lecture_id == lec["id"])).all()
        assert cards and {c.subject_id for c in cards} == {ml}


def test_right_subject_and_dismiss(client: TestClient) -> None:
    ml, rl = _setup(client)
    lec = _import(client, ml)  # matches the timetable: no hint
    assert client.get(f"/api/lectures/{lec['id']}").json()["slot_suggestion"] is None
    other = _import(client, rl)
    r = client.patch(f"/api/lectures/{other['id']}", json={"dismiss_slot": True})
    assert r.json()["slot_suggestion"] is None and r.json()["subject_id"] == rl
    Worker().run_pending()  # the hint isn't added back when the audio is ready
    assert client.get(f"/api/lectures/{other['id']}").json()["slot_suggestion"] is None
