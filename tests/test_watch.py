"""Watched Moodle courses: what the extension reports becomes lectures (local server, no Moodle)."""

from __future__ import annotations

import shutil
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from backend.core.db import session_scope
from backend.core.jobs import Worker
from backend.core.lan import _blocked
from backend.core.library import create_lecture
from backend.core.models import LectureSource, LectureStatus, Subject, WatchedCourse, utcnow
from backend.pipeline import watch
from tests.test_download import site  # noqa: F401  (module fixture: mp4 on a local server)

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not on PATH")

EXT = {"Origin": "chrome-extension://abcdef"}
OLD = "11111111-1111-4111-8111-111111111111"  # imported with the extension before watching
SAME = "22222222-2222-4222-8222-222222222222"  # same length as a file the user imported
NEW = "33333333-3333-4333-8333-333333333333"
LATER = "44444444-4444-4444-8444-444444444444"


def _report(c: TestClient, recordings: list[dict], **kw) -> dict:
    r = c.post(
        "/api/downloads/watch/report",
        json={"moodle_id": 45500, "recordings": recordings, **kw},
        headers=EXT,
    )
    assert r.status_code == 200, r.text
    return r.json()


def _state(c: TestClient) -> dict:
    return c.get("/api/downloads/watch/state", headers=EXT).json()


def _setup(c: TestClient) -> int:
    sid = c.post("/api/subjects", json={"name": "ML Supervised"}).json()["id"]
    with session_scope() as s:
        subject = s.get(Subject, sid)
        old = create_lecture(s, subject, source=LectureSource.import_, status=LectureStatus.ready)
        old.meta = {
            **old.meta,
            "download": {"url": f"https://stream.example/smil:engage-player_{OLD}_presentation"},
        }
        own = create_lecture(
            s, subject, source=LectureSource.import_, status=LectureStatus.ready, title="Intro"
        )
        own.duration_s = 3000
        s.add_all([old, own])
        s.commit()
    r = c.put(
        "/api/downloads/watch/courses",
        json={"moodle_id": 45500, "name": "VL ML Supervised", "subject_id": sid, "kind": "VL"},
        headers=EXT,
    )
    assert r.status_code == 200, r.text
    return sid


def test_backlog_is_reviewed_new_recordings_download(
    client: TestClient,
    site: str,  # noqa: F811
) -> None:
    sid = _setup(client)
    st = _state(client)
    assert st["scan"] and st["courses"][0]["due"] and st["courses"][0]["known"] == []

    # First scan: the backlog. Nothing downloads; the old import is recognised by its id.
    got = _report(
        client,
        [
            {"episode_id": OLD, "title": "1. Introduction", "duration_s": 3200},
            {"episode_id": SAME, "title": "HS 7 07.10.2026", "duration_s": 3050},
            {
                "episode_id": NEW,
                "title": "1:04:00 HS 7 14.10.2026 14.10.26",
                "duration_s": 3.0,
                "page_url": "https://moodle.example/mod/opencast/view.php?id=1&e=x",
            },
        ],
    )
    assert got == {"queued": 0, "review": 2}
    ov = client.get("/api/downloads/watch").json()
    review = {r["episode_id"]: r for r in ov["review"]}
    assert set(review) == {SAME, NEW}
    assert review[SAME]["have_hint"].startswith("Probably “Intro”")
    assert review[NEW]["recorded_on"] == "2026-10-14"
    assert ov["courses"][0]["counts"]["have"] == 1
    assert not _state(client)["scan"]  # just scanned

    # The user picks one: the next tick scans and the report carries its link.
    client.post("/api/downloads/watch/decide", json={"get": [NEW], "skip": [SAME]}, headers=EXT)
    st = _state(client)
    assert st["scan"] and NEW not in st["courses"][0]["known"]
    got = _report(
        client,
        [
            {
                "episode_id": NEW,
                "title": "1:04:00 HS 7 14.10.2026 14.10.26",
                "url": f"{site}/lecture.mp4",
                "page_url": "https://moodle.example/mod/opencast/view.php?id=1&e=x",
            }
        ],
    )
    assert got["queued"] == 1
    Worker().run_pending()
    lectures = client.get("/api/lectures", params={"subject_id": sid}).json()
    lec = next(x for x in lectures if x["id"] not in (1, 2))
    detail = client.get(f"/api/lectures/{lec['id']}").json()
    assert detail["has_audio"] and detail["kind"] == "VL"
    assert detail["started_at"].startswith("2026-10-14")  # from the title, not the download
    with session_scope() as s:
        from backend.core.models import Lecture

        meta = s.get(Lecture, lec["id"]).meta
    assert meta["opencast"] == {"episode": NEW}
    assert meta["download"]["url"].startswith("https://moodle.example/")  # not the signed link

    # Later finds download straight away; titles that say something are kept, kind from them.
    got = _report(
        client,
        [
            {"episode_id": NEW, "title": "x", "url": f"{site}/lecture.mp4"},
            {
                "episode_id": LATER,
                "title": "15.10.2026 UE Multi-Armed Bandits Video (Opencast)",
                "url": f"{site}/lecture.mp4",
            },
        ],
    )
    assert got == {"queued": 1, "review": 0}
    lectures = client.get("/api/lectures", params={"subject_id": sid}).json()
    later = next(x for x in lectures if x["title"] == "15.10.2026 UE Multi-Armed Bandits")
    assert later["kind"] == "UE"
    Worker().run_pending()
    assert _report(client, [{"episode_id": LATER, "url": f"{site}/lecture.mp4"}])["queued"] == 0


def test_expired_link_is_retried_and_deleted_lectures_stay_gone(
    client: TestClient,
    site: str,  # noqa: F811
) -> None:
    sid = _setup(client)
    _report(client, [])  # first scan: empty course
    # /private/ refuses requests without the cookie: like a signed link that expired.
    assert _report(client, [{"episode_id": LATER, "url": f"{site}/private/lecture.mp4"}]) == {
        "queued": 1,
        "review": 0,
    }
    Worker().run_pending()
    lecture_id = next(
        x["id"]
        for x in client.get("/api/lectures", params={"subject_id": sid}).json()
        if x["status"] == "failed"
    )
    assert LATER not in _state(client)["courses"][0]["known"]  # wants a fresh link
    assert _report(client, [{"episode_id": LATER, "url": f"{site}/lecture.mp4"}])["queued"] == 1
    Worker().run_pending()
    lec = client.get(f"/api/lectures/{lecture_id}").json()  # the same lecture, now fine
    assert lec["has_audio"] and lec["status"] != "failed"

    assert client.delete(f"/api/lectures/{lecture_id}").status_code == 204
    assert _report(client, [{"episode_id": LATER, "url": f"{site}/lecture.mp4"}])["queued"] == 0
    assert LATER in _state(client)["courses"][0]["known"]


def test_login_error_and_scheduling(client: TestClient) -> None:
    _setup(client)
    _report(client, [], error="Log in to Moodle in your browser")
    course = client.get("/api/downloads/watch").json()["courses"][0]
    assert course["last_error"] == "Log in to Moodle in your browser"

    with session_scope() as s:
        c = s.exec(__import__("sqlmodel").select(WatchedCourse)).one()
        now = utcnow()
        c.last_scan_at = now - timedelta(minutes=30)
        s.add(c)
        s.commit()
        assert not watch.course_due(s, c, now)
        assert watch.course_due(s, c, now + timedelta(minutes=31))  # hourly
    client.post("/api/downloads/watch/check", headers=EXT)
    assert _state(client)["scan"]

    _report(client, [{"episode_id": NEW, "title": "x"}])
    course_id = client.get("/api/downloads/watch").json()["courses"][0]["id"]
    r = client.delete(f"/api/downloads/watch/courses/{course_id}", headers=EXT)
    assert r.status_code == 204
    assert client.get("/api/downloads/watch").json() == {"courses": [], "review": []}


def test_titles_and_dates() -> None:
    assert watch.recorded_on("1:28:36 12.10.2026 HS 7 12.10.26") == date(2026, 10, 12)
    assert watch.recorded_on("Ex Class 1", "HS 5 - 13.10.26") == date(2026, 10, 13)
    assert watch.recorded_on("Lecture videos") is None
    assert watch.tidy_title("2. Basic classifiers and evaluation metrics") == (
        "2. Basic classifiers and evaluation metrics",
        True,
    )
    assert watch.tidy_title("HS 7 14.10.2026")[1] is False
    assert watch.tidy_title("1:26:15 19.10.2026 HS 7 19.10.26") == ("19.10.2026 HS 7", False)
    assert watch.tidy_title("51:42 Ex Class 1 14.10.26") == ("Ex Class 1 14.10.26", False)
    assert watch.tidy_title("08.10.2026 VL Introduction Video Video (Opencast)") == (
        "08.10.2026 VL Introduction Video",
        False,
    )


def test_watch_is_pc_only(client: TestClient) -> None:
    assert _blocked("GET", "/api/downloads/watch/state")
    assert _blocked("POST", "/api/downloads/watch/report")
    r = client.get("/api/downloads/watch/state", headers={"Origin": "https://evil.example"})
    assert r.status_code == 403
