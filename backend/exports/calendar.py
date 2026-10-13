"""The study calendar as .ics: exams, open deadlines and the study plan.

Stable UIDs (``exam-<subject>``, ``deadline-<id>``, ``plan-<date>``) mean a re-import or a
subscribed calendar updates events in place instead of duplicating them. The study plan is one
event per day at the configured start time, lasting the planned minutes, with the blocks in the
description.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import icalendar
from sqlmodel import Session, col, select

from backend.core.config import get_settings
from backend.core.models import Deadline, StudyPlanItem, Subject, utcnow
from backend.study.planner import ensure_current

DOMAIN = "lecturelab"


def _local(d: date, hhmm: str) -> datetime:
    h, m = (int(x) for x in hhmm.split(":"))
    return datetime(d.year, d.month, d.day, h, m).astimezone().astimezone(UTC)


def _alarm(trigger: timedelta, text: str) -> icalendar.Alarm:
    a = icalendar.Alarm()
    a.add("action", "DISPLAY")
    a.add("description", text)
    a.add("trigger", trigger)
    return a


def _event(uid: str, summary: str) -> icalendar.Event:
    e = icalendar.Event()
    e.add("uid", f"{uid}@{DOMAIN}")
    e.add("dtstamp", utcnow())
    e.add("summary", summary)
    return e


def _all_day(e: icalendar.Event, d: date) -> None:
    e.add("dtstart", d)
    e.add("dtend", d + timedelta(days=1))
    e.add("transp", "TRANSPARENT")


def build_ics(
    s: Session,
    plan: bool = True,
    deadlines: bool = True,
    exams: bool = True,
    today: date | None = None,
) -> bytes:
    today = today or date.today()
    cal = icalendar.Calendar()
    cal.add("prodid", "-//LectureLab//study calendar//EN")
    cal.add("version", "2.0")
    cal.add("x-wr-calname", "LectureLab")
    subjects = {x.id: x for x in s.exec(select(Subject)).all()}

    if exams:
        for x in subjects.values():
            if x.exam_date and x.exam_date >= today - timedelta(days=30) and not x.archived:
                e = _event(f"exam-{x.id}", f"Exam: {x.name}")
                _all_day(e, x.exam_date)
                e.add_component(_alarm(timedelta(days=-2), f"Exam in 2 days: {x.name}"))
                cal.add_component(e)

    if deadlines:
        for d in s.exec(
            select(Deadline).where(Deadline.status == "open", col(Deadline.due_date).is_not(None))
        ).all():
            sub = subjects.get(d.subject_id or 0)
            due = d.due_date
            assert due is not None
            label = "Due: " if d.kind in ("deadline", "assignment") else ""
            e = _event(f"deadline-{d.id}", f"{label}{d.title}" + (f" ({sub.name})" if sub else ""))
            if d.due_time:
                start = _local(due, d.due_time)
                e.add("dtstart", start)
                e.add("dtend", start + timedelta(minutes=30))
            else:
                _all_day(e, due)
            e.add_component(_alarm(timedelta(days=-1), f"Tomorrow: {d.title}"))
            cal.add_component(e)

    if plan:
        ensure_current(s, today)
        start_time = get_settings().planner.start_time
        by_day: dict[date, list[StudyPlanItem]] = {}
        for it in s.exec(
            select(StudyPlanItem)
            .where(StudyPlanItem.day >= today, StudyPlanItem.minutes > 0)
            .order_by(col(StudyPlanItem.day), col(StudyPlanItem.position))
        ).all():
            by_day.setdefault(it.day, []).append(it)
        for day, items in by_day.items():
            total = sum(i.minutes for i in items)
            names = []
            for i in items:
                sub = subjects.get(i.subject_id or 0)
                if sub and sub.name not in names:
                    names.append(sub.name)
            e = _event(
                f"plan-{day.isoformat()}",
                f"Study ({total} min)" + (f": {', '.join(names)}" if names else ""),
            )
            start = _local(day, start_time)
            e.add("dtstart", start)
            e.add("dtend", start + timedelta(minutes=total))
            e.add(
                "description",
                "\n".join(f"{'✓' if i.done else '•'} {i.minutes} min {i.title}" for i in items),
            )
            cal.add_component(e)
    return cal.to_ical()
