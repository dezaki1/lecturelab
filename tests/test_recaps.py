from __future__ import annotations

import shutil
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from backend.app import create_app
from backend.core.config import Settings
from backend.core.db import session_scope
from backend.core.jobs import Worker
from backend.core.library import abs_path
from backend.core.media import probe
from backend.core.models import Lecture, Note, Recap, Subject
from backend.pipeline import recap as rc
from backend.voice import tts

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not on PATH")


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(settings, start_worker=False)) as c:
        yield c


def _lecture(subject: str, slug: str, when: datetime, title: str) -> int:
    with session_scope() as s:
        sub = s.exec(select(Subject).where(Subject.slug == slug)).first()
        if sub is None:
            sub = Subject(name=subject, slug=slug)
            s.add(sub)
            s.commit()
        folder = f"subjects/{slug}/{when:%Y-%m-%d}_{slug}"
        abs_path(folder).mkdir(parents=True, exist_ok=True)
        lec = Lecture(
            subject_id=sub.id,  # type: ignore[arg-type]
            title=title,
            started_at=when,
            duration_s=600,
            folder=folder,
        )
        s.add(lec)
        s.commit()
        for kind, md in (
            ("summary", "## TL;DR\n- It matters"),
            ("notes", f"## {title} [00:10]\nBody"),
        ):
            s.add(Note(subject_id=sub.id, lecture_id=lec.id, kind=kind, content_md=md))  # type: ignore[arg-type]
        s.commit()
        return lec.id  # type: ignore[return-value]


MONDAY = (datetime.now(UTC) - timedelta(days=datetime.now(UTC).weekday())).replace(
    hour=10, minute=0, second=0, microsecond=0
)


def test_lecture_recap_audio_with_chapters(client: TestClient) -> None:
    lid = _lecture("Machine Learning", "ml", MONDAY, "PCA")
    r = client.post("/api/recaps", json={"scope": "lecture", "lecture_id": lid, "minutes": 5})
    assert r.status_code == 201, r.text
    rid = r.json()["id"]
    assert r.json()["status"] == "queued" and r.json()["title"] == "PCA"
    assert Worker().run_pending() == 1

    rec = client.get(f"/api/recaps/{rid}").json()
    assert rec["status"] == "ready", rec["error"]
    assert rec["title"] == "Mock recap" and [c["title"] for c in rec["chapters"]] == [
        "Intro",
        "Lecture 1",
        "Self-test",
    ]
    starts = [c["start_s"] for c in rec["chapters"]]
    assert starts[0] == 0 and starts == sorted(starts) and rec["duration_s"] > starts[-1]
    assert rec["chapters"][1]["refs"] == [
        {"lecture_id": lid, "start_s": 10.0, "title": "PCA", "at": "00:10"}
    ]

    audio = client.get(f"/api/recaps/{rid}/audio")
    assert audio.status_code == 200 and audio.headers["content-type"] == "audio/mp4"
    dl = client.get(f"/api/recaps/{rid}/audio", params={"download": True})
    assert "Mock%20recap.m4a" in dl.headers["content-disposition"]
    with session_scope() as s:
        path = abs_path(s.get(Recap, rid).audio_path)  # type: ignore[union-attr]
    assert path.name == "recap.m4a" and path.parent.name.endswith("_ml")
    info = probe(path)
    assert info.has_audio and abs(info.duration - rec["duration_s"]) < 0.5

    # A new recap of the same lecture replaces the old one (row and file).
    r2 = client.post("/api/recaps", json={"scope": "lecture", "lecture_id": lid}).json()
    assert not path.exists()  # (SQLite may reuse the old id, so compare the rows instead)
    rows = client.get("/api/recaps", params={"lecture_id": lid}).json()
    assert [(x["id"], x["status"]) for x in rows] == [(r2["id"], "queued")]
    assert client.delete(f"/api/recaps/{r2['id']}").status_code == 204
    assert Worker().run_pending() == 0  # its queued job was cancelled


def test_week_recap_across_subjects(client: TestClient) -> None:
    a = _lecture("Machine Learning", "ml", MONDAY, "PCA")
    b = _lecture("Algorithms", "ads", MONDAY + timedelta(days=2), "Heaps")
    _lecture("Algorithms", "ads", MONDAY - timedelta(days=7), "Last week")
    weeks = client.get("/api/recaps/weeks").json()
    assert weeks[0]["week_start"] == MONDAY.date().isoformat()
    assert weeks[0]["subjects"] == ["Algorithms", "Machine Learning"] and len(weeks) == 2

    wednesday = (MONDAY + timedelta(days=2)).date().isoformat()
    r = client.post("/api/recaps", json={"scope": "week", "week_start": wednesday})
    assert r.status_code == 201, r.text
    assert r.json()["week_start"] == MONDAY.date().isoformat()
    assert r.json()["title"].startswith("Week of ")
    Worker().run_pending()
    rec = client.get(f"/api/recaps/{r.json()['id']}").json()
    assert rec["status"] == "ready", rec["error"]
    lecture_refs = [c["refs"][0]["lecture_id"] for c in rec["chapters"] if c["refs"]]
    assert lecture_refs == [a, b]  # in time order, last week's lecture not included

    sid = client.get("/api/subjects").json()
    ads = next(x["id"] for x in sid if x["name"] == "Algorithms")
    one = client.post(
        "/api/recaps", json={"scope": "week", "week_start": wednesday, "subject_id": ads}
    )
    assert one.status_code == 201 and one.json()["title"].endswith(": Algorithms")
    # A subject-only week recap doesn't replace the all-subjects one.
    assert client.get(f"/api/recaps/{rec['id']}").status_code == 200

    empty = (MONDAY + timedelta(days=21)).date().isoformat()
    r = client.post("/api/recaps", json={"scope": "week", "week_start": empty})
    assert r.status_code == 422 and "No lecture" in r.json()["detail"]


def test_needs_voice_and_auto_recap(
    client: TestClient, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    lid = _lecture("Machine Learning", "ml", MONDAY, "PCA")
    monkeypatch.setattr(tts, "ready", lambda: False)
    r = client.post("/api/recaps", json={"scope": "lecture", "lecture_id": lid})
    assert r.status_code == 422 and "voice" in r.json()["detail"]
    monkeypatch.setattr(tts, "ready", lambda: True)

    assert client.get("/api/settings/recap").json() == {"auto_lecture": False, "minutes": 7}
    with session_scope() as s:
        assert not rc.maybe_auto_queue(s, lid)
    client.put("/api/settings/recap", json={"auto_lecture": True, "minutes": 5})
    with session_scope() as s:
        assert rc.maybe_auto_queue(s, lid)
        assert not rc.maybe_auto_queue(s, lid)  # one per lecture
        assert s.exec(select(Recap)).one().minutes == 5
