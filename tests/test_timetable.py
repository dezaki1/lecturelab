from __future__ import annotations

import shutil
import time
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.core.config import Settings
from backend.core.events import Event, bus
from backend.core.models import Subject
from backend.timetable import service as tt
from backend.timetable.ics import course_key, course_kind, parse
from tests.fakes import fake_factory

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not on PATH")


def ics(*events: tuple[str, datetime, int, str]) -> bytes:
    """Minimal calendar: (summary, start UTC, minutes, extra lines)."""
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//test//EN"]
    for i, (summary, start, minutes, extra) in enumerate(events):
        end = start + timedelta(minutes=minutes)
        lines += [
            "BEGIN:VEVENT",
            f"UID:ev{i}@test",
            f"SUMMARY:{summary}",
            f"DTSTART:{start:%Y%m%dT%H%M%SZ}",
            f"DTEND:{end:%Y%m%dT%H%M%SZ}",
            "LOCATION:HS 1",
            *([extra] if extra else []),
            "END:VEVENT",
        ]
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines).encode()


NOW = datetime.now(UTC).replace(microsecond=0)


def test_parse_keys_and_kinds() -> None:
    data = ics(
        (
            "344.031 VL Machine Learning: Supervised Techniques",
            NOW + timedelta(days=1),
            90,
            "RRULE:FREQ=WEEKLY;COUNT=3",
        ),
        ("UE Algorithms and Data Structures 2", NOW + timedelta(days=2), 90, ""),
        ("Cancelled thing", NOW + timedelta(days=3), 60, "STATUS:CANCELLED"),
    )
    events = parse(data, NOW)
    assert [e["summary"][:9] for e in events] == [
        "344.031 V",
        "UE Algori",
        "344.031 V",
        "344.031 V",
    ]
    ml = events[0]
    assert ml["key"] == "machine learning supervised techniques" and ml["kind"] == "VL"
    assert events[1]["kind"] == "UE" and events[1]["key"] == "algorithms and data structures 2"
    assert course_kind("Seminar in AI (SE)") == "other" and course_kind("Hands-on AI I") == ""
    assert course_key("KV Hands-on AI I") == "hands on ai i"


def test_match_subject() -> None:
    subjects = [
        Subject(id=1, name="Machine Learning: Supervised Techniques", slug="ml"),
        Subject(id=2, name="Algorithms and Data Structures 2", slug="ads2"),
        Subject(id=3, name="Hands-on AI I", slug="hoai", course_code="365.101"),
    ]
    assert tt.match_subject("machine learning supervised techniques", "", subjects) == 1
    assert tt.match_subject("algorithms and data structures 2 group 3", "", subjects) == 2
    assert tt.match_subject("whatever", "365.101 KV Something", subjects) == 3  # course code
    assert tt.match_subject("mensa lunch", "Mensa lunch", subjects) is None


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(settings, start_worker=False, source_factory=fake_factory())) as c:
        yield c


def _subjects(c: TestClient) -> tuple[int, int]:
    ml = c.post("/api/subjects", json={"name": "Machine Learning: Supervised Techniques"}).json()[
        "id"
    ]
    ads = c.post("/api/subjects", json={"name": "Algorithms and Data Structures 2"}).json()["id"]
    return ml, ads


def test_upload_map_and_now(client: TestClient) -> None:
    ml, ads = _subjects(client)
    data = ics(
        ("344.031 VL Machine Learning: Supervised Techniques", NOW - timedelta(minutes=20), 90, ""),
        ("UE Algorithms and Data Structures 2", NOW + timedelta(hours=3), 90, ""),
        ("Mensa", NOW + timedelta(hours=1), 60, ""),
    )
    r = client.post("/api/timetable/upload", params={"filename": "kusss.ics"}, content=data)
    assert r.status_code == 201, r.text
    assert (
        client.post(
            "/api/timetable/upload", params={"filename": "x.ics"}, content=b"nope"
        ).status_code
        == 422
    )

    tt_ = client.get("/api/timetable").json()
    courses = {c["label"]: c for c in tt_["courses"]}
    assert courses["344.031 VL Machine Learning: Supervised Techniques"]["subject_id"] == ml
    assert courses["Mensa"]["subject_id"] is None
    client.patch(f"/api/timetable/courses/{courses['Mensa']['id']}", json={"ignored": True})

    now = client.get("/api/timetable/now").json()
    assert now["current"]["subject_id"] == ml and now["current"]["kind"] == "VL"
    assert now["next"]["subject_id"] == ads  # Mensa is ignored
    assert len(client.get("/api/timetable/week").json()) >= 1

    # Starting a recording without choosing a subject uses the running slot.
    st = client.post("/api/recorder/start", json={}).json()
    assert st["subject_id"] == ml
    time.sleep(0.3)
    lid = client.post("/api/recorder/stop").json()["lecture_id"]
    assert client.get(f"/api/lectures/{lid}").json()["kind"] == "VL"

    src = tt_["sources"][0]
    assert client.delete(f"/api/timetable/sources/{src['id']}").status_code == 204
    assert client.get("/api/timetable/now").json()["current"] is None


def test_subscribed_link_syncs(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _subjects(client)
    calls: list[str] = []
    monkeypatch.setattr(
        tt,
        "fetch",
        lambda url: (
            calls.append(url)
            or ics(("VL Machine Learning: Supervised Techniques", NOW + timedelta(days=1), 90, ""))
        ),
    )
    r = client.post("/api/timetable/sources", json={"url": "webcal://kusss.jku.at/cal.ics"})
    assert r.status_code == 201
    sch = tt.TimetableScheduler(find_zoom=lambda: False)
    sch.sync_due(NOW + timedelta(hours=1))  # synced on add: not due yet
    assert len(calls) == 1
    sch.sync_due(NOW + timedelta(hours=7))
    assert len(calls) == 2


def test_reminder_once_and_auto_record(client: TestClient, settings: Settings) -> None:
    ml, _ = _subjects(client)
    start = NOW + timedelta(seconds=90)
    client.post(
        "/api/timetable/upload",
        params={"filename": "t.ics"},
        content=ics(("VL Machine Learning: Supervised Techniques", start, 60, "")),
    )
    notes: list[dict] = []

    def listen(evt: Event) -> None:
        if evt.type == "notify":
            notes.append(evt.data)

    bus.add_listener(listen)
    try:
        sch = tt.TimetableScheduler(find_zoom=lambda: zoom[0])
        zoom = [False]
        sch.remind(NOW)
        sch.remind(NOW + timedelta(seconds=10))
        reminders = [n for n in notes if "starts in" in n["title"]]
        assert len(reminders) == 1 and reminders[0]["action"]["subject_id"] == ml
        assert reminders[0]["title"].startswith(
            "Machine Learning: Supervised Techniques starts in 2 min"
        )

        settings.timetable.auto_record = True
        sch.auto_record(start)  # slot running, but Zoom is closed
        assert not client.get("/api/recorder").json()["recording"]
        zoom[0] = True
        sch.auto_record(start + timedelta(minutes=1))
        st = client.get("/api/recorder").json()
        assert st["recording"] and st["subject_id"] == ml
        time.sleep(0.3)
        after = start + timedelta(minutes=61)
        sch.auto_record(after)  # slot over, but Zoom still open: keep recording
        assert client.get("/api/recorder").json()["recording"]
        zoom[0] = False
        sch.auto_record(after + timedelta(seconds=30))
        sch.auto_record(after + timedelta(seconds=95))
        assert not client.get("/api/recorder").json()["recording"]
        sch.auto_record(start + timedelta(minutes=2))  # same slot isn't recorded twice
        assert not client.get("/api/recorder").json()["recording"]
    finally:
        bus.remove_listener(listen)
