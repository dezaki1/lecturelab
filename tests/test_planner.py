from __future__ import annotations

import shutil
from collections.abc import Iterator
from datetime import date, timedelta
from itertools import pairwise

import icalendar
import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from backend.app import create_app
from backend.core.config import Settings
from backend.core.db import session_scope
from backend.core.models import Note, StudyPlanItem, Subject
from backend.study import planner
from backend.study.deadlines import collect_from_note
from tests.test_weak import _setup

TODAY = date.today()


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(settings, start_worker=False)) as c:
        yield c


def _announcements(sub: int, lec: int, items: list[dict]) -> None:
    with session_scope() as s:
        for n in s.exec(
            select(Note).where(Note.lecture_id == lec, Note.kind == "announcements")
        ).all():
            n.is_current = False
            s.add(n)
        note = Note(subject_id=sub, lecture_id=lec, kind="announcements", data={"items": items})
        s.add(note)
        s.commit()
        s.refresh(note)
        collect_from_note(s, note)


def test_deadlines_collected_and_reviewed(client: TestClient) -> None:
    ids = _setup()
    sub, lec = ids["subject"], ids["lecture"]
    soon = (TODAY + timedelta(days=5)).isoformat()
    _announcements(
        sub,
        lec,
        [
            {"kind": "assignment", "text": "Sheet 3 due", "date": soon, "at": "12:00"},
            {"kind": "exam", "text": "Exam in HS 1, closed book", "date": "", "at": "50:00"},
            {"kind": "admin", "text": "No lecture next week", "date": "", "at": "01:00"},  # no date
            {"kind": "admin", "text": "Room change", "date": "nonsense", "at": "02:00"},
            {"kind": "deadline", "text": "Old sheet", "date": "2020-01-01", "at": "03:00"},  # past
        ],
    )
    d = client.get("/api/deadlines").json()
    titles = {x["title"]: x for x in d["suggested"]}
    assert set(titles) == {"Sheet 3 due", "Exam in HS 1, closed book"}
    sheet = titles["Sheet 3 due"]
    assert sheet["due_date"] == soon and sheet["t_s"] == 720 and sheet["lecture_title"] == "Bellman"

    # Accept one (with a time), dismiss the other.
    r = client.patch(f"/api/deadlines/{sheet['id']}", json={"status": "open", "due_time": "9:30"})
    assert r.status_code == 200
    exam_id = titles["Exam in HS 1, closed book"]["id"]
    assert client.delete(f"/api/deadlines/{exam_id}").status_code == 204
    assert (
        client.patch(f"/api/deadlines/{sheet['id']}", json={"due_time": "25:00"}).status_code == 422
    )

    # Regenerated note: same items again plus a new one; decisions stay, nothing duplicates.
    _announcements(
        sub,
        lec,
        [
            {"kind": "assignment", "text": "Sheet 3 due!", "date": soon, "at": "12:05"},
            {"kind": "exam", "text": "Exam in HS 1, closed book", "date": "", "at": "50:00"},
            {"kind": "deadline", "text": "Project proposal", "date": soon, "at": "30:00"},
        ],
    )
    d = client.get("/api/deadlines").json()
    assert [x["title"] for x in d["suggested"]] == ["Project proposal"]
    assert [(x["title"], x["due_time"]) for x in d["open"]] == [("Sheet 3 due", "09:30")]

    # Manual deadline, then done.
    r = client.post("/api/deadlines", json={"title": "Register for exam", "due_date": soon})
    assert r.status_code == 201
    client.patch(f"/api/deadlines/{r.json()['id']}", json={"status": "done"})
    d = client.get("/api/deadlines").json()
    assert [x["title"] for x in d["done"]] == ["Register for exam"]
    assert client.delete(f"/api/deadlines/{r.json()['id']}").status_code == 204


def _exam_in(days: int, name: str = "RL") -> None:
    with session_scope() as s:
        sub = s.exec(select(Subject).where(Subject.name == name)).one()
        sub.exam_date = TODAY + timedelta(days=days)
        s.add(sub)
        s.commit()


def test_plan_targets_weak_topics_and_ends_with_exam(client: TestClient) -> None:
    _setup()
    no_exam = client.get("/api/planner").json()["days"]  # no exam date yet: only reviews
    assert {i["kind"] for d in no_exam for i in d["items"]} <= {"review"}
    _exam_in(10)
    p = client.get("/api/planner").json()
    days = {d["date"]: d for d in p["days"]}
    exam_day = (TODAY + timedelta(days=10)).isoformat()
    assert [i["kind"] for i in days[exam_day]["items"]] == ["exam"]
    for d in p["days"]:
        if d["date"] != exam_day:
            assert d["minutes"] <= 90
    first = [i for i in p["days"][0]["items"] if i["kind"] == "topic"]
    # Bellman (weak, exam-relevant) first, then untested topics; not the strong one first.
    assert first[0]["title"] == "RL: Bellman equation"
    assert first[0]["meta"]["lecture_id"] and first[0]["meta"]["start_s"] == 600
    kinds = [i["kind"] for d in p["days"] for i in d["items"]]
    assert kinds.count("cheatsheet") == 1 and kinds.count("mock_exam") == 1
    day_before = (TODAY + timedelta(days=9)).isoformat()
    assert "cheatsheet" in [i["kind"] for i in days[day_before]["items"]]
    # Spacing: Bellman isn't scheduled on consecutive days.
    bellman = [d["date"] for d in p["days"] for i in d["items"] if "Bellman" in i["title"]]
    gaps = [(date.fromisoformat(b) - date.fromisoformat(a)).days for a, b in pairwise(bellman)]
    assert gaps and min(gaps) >= 2

    # Ticking a block off keeps it through a rebuild; the rebuild fills the rest of today.
    item = first[0]
    assert client.patch(f"/api/planner/items/{item['id']}", json={"done": True}).json()["done"]
    client.post("/api/planner/rebuild")
    today = client.get("/api/planner").json()["days"][0]
    assert today["items"][0]["id"] == item["id"] and today["items"][0]["done"]
    assert today["minutes"] <= 90 and today["done_minutes"] == item["minutes"]


def test_settings_and_auto_rebuild(client: TestClient) -> None:
    _setup()
    _exam_in(14)
    client.get("/api/planner")
    r = client.put("/api/planner/settings", json={"minutes_per_day": 60, "weekdays": [0, 2, 4]})
    assert r.status_code == 200 and r.json()["weekdays"] == [0, 2, 4]
    p = client.get("/api/planner").json()
    for d in p["days"]:
        if date.fromisoformat(d["date"]).weekday() not in (0, 2, 4):
            assert all(i["minutes"] == 0 for i in d["items"])
        assert d["minutes"] <= 60
    assert client.put("/api/planner/settings", json={"weekdays": []}).status_code == 422
    assert client.put("/api/planner/settings", json={"start_time": "7pm"}).status_code == 422

    # Changing the exam date rebuilds on the next read.
    _exam_in(3)
    p = client.get("/api/planner").json()
    assert p["last_day"] == (TODAY + timedelta(days=3)).isoformat()
    with session_scope() as s:
        assert (
            s.exec(select(StudyPlanItem).where(StudyPlanItem.day > TODAY + timedelta(days=3))).all()
            == []
        )


def test_calendar_export(client: TestClient) -> None:
    _setup()
    _exam_in(7)
    sid = client.get("/api/subjects").json()[0]["id"]
    due = (TODAY + timedelta(days=2)).isoformat()
    client.post(
        "/api/deadlines",
        json={"title": "Sheet 4", "kind": "assignment", "due_date": due, "subject_id": sid},
    )
    client.post(
        "/api/deadlines",
        json={"title": "Talk", "kind": "other", "due_date": due, "due_time": "14:00"},
    )
    client.put("/api/planner/settings", json={"start_time": "17:30"})
    r = client.get("/api/calendar.ics", params={"download": True})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/calendar")
    assert "attachment" in r.headers["content-disposition"]
    cal = icalendar.Calendar.from_ical(r.content)
    events = {str(e["UID"]): e for e in cal.walk("VEVENT")}
    assert str(events[f"exam-{sid}@lecturelab"]["SUMMARY"]) == "Exam: RL"
    sheet = next(e for e in events.values() if "Sheet 4" in str(e["SUMMARY"]))
    assert str(sheet["SUMMARY"]) == "Due: Sheet 4 (RL)" and sheet.decoded(
        "DTSTART"
    ) == date.fromisoformat(due)
    talk = next(e for e in events.values() if str(e["SUMMARY"]) == "Talk")
    assert talk.decoded("DTSTART").astimezone().strftime("%H:%M") == "14:00"
    plan = events[f"plan-{TODAY.isoformat()}@lecturelab"]
    assert plan.decoded("DTSTART").astimezone().strftime("%H:%M") == "17:30"
    assert "Bellman" in str(plan["DESCRIPTION"]) and str(plan["SUMMARY"]).startswith("Study (")
    only = icalendar.Calendar.from_ical(
        client.get("/api/calendar.ics?plan=false&exams=false").content
    )
    assert len(only.walk("VEVENT")) == 2


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not on PATH")
def test_generation_suggests_deadlines(settings: Settings) -> None:
    from tests.fakes import fake_factory
    from tests.test_generate import _lecture

    settings.generation.auto = True
    with TestClient(create_app(settings, start_worker=False, source_factory=fake_factory())) as c:
        _lecture(c)
        d = c.get("/api/deadlines").json()
    assert [x["title"] for x in d["suggested"]] == ["Assignment due"]


def test_plan_without_cards_or_exams_is_empty(client: TestClient) -> None:
    with session_scope() as s:
        assert planner.build(s) == 0
