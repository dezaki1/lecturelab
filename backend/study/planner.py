"""Study planner: a day-by-day revision plan up to each exam, without an LLM.

Inputs: exam dates (Subject.exam_date), topic strength (``weak.topic_stats``), cards due per day
(FSRS schedule), open deadlines, and the student's time budget (``PlannerConfig``).

Each study day gets:

- a review block sized from the cards due that day,
- the last study day before an exam: the subject's cheat sheet, two study days earlier: a mock
  exam in the simulator,
- topic blocks for the remaining time. Each block goes to the subject with the most remaining
  work x urgency (work = summed topic need, need = 1 - strength; urgency = 1 / sqrt(days to the
  exam)), divided by 1 + blocks it already got that day so subjects share the day. Inside a
  subject, the weakest topic outside its spacing gap wins (exam-relevant ones get a bonus); a
  studied topic counts as stronger and comes back after growing gaps (2, 4, 7, 14 days). When
  no subject has such a topic: one practice-questions block per subject and day, then topics
  not studied the day before are repeated early, then more practice.

The plan is stored (``StudyPlanItem``) so blocks can be ticked off. It is rebuilt each new day
and whenever exam dates or settings change; done blocks are kept.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from sqlmodel import Session, col, delete, select

from backend.core.config import get_settings
from backend.core.events import bus
from backend.core.models import Deadline, Flashcard, StudyPlanItem, Subject
from backend.core.state import load_state, update_state
from backend.study.weak import topic_stats

MAX_DAYS = 120
GAPS = (2, 4, 7, 14)
UNTESTED_NEED = 0.7
RELEVANT_BONUS = 1.25
STUDIED_FACTOR = 0.65
MIN_BLOCK = 15
MOCK_MIN = 60
CHEATSHEET_MIN = 30
EDITABLE = ("minutes_per_day", "weekdays", "start_time", "block_min")


class PlannerError(Exception):
    pass


@dataclass
class _Topic:
    name: str
    need: float
    relevant: bool
    meta: dict[str, Any]
    reps: int = 0
    next_ok: date = date.min
    last: date = date.min


@dataclass
class _Subject:
    id: int
    name: str
    exam: date
    topics: list[_Topic]
    special: dict[date, list[tuple[str, str, int]]] = field(default_factory=dict)

    def remaining(self) -> float:
        """Work left: summed topic need (more weak material gets more time)."""
        return sum(t.need for t in self.topics) if self.topics else UNTESTED_NEED

    def next_topic(self, d: date, early: bool = False) -> _Topic | None:
        if early:  # never the day after it was studied
            pool = [t for t in self.topics if t.last < d - timedelta(days=1)]
        else:
            pool = [t for t in self.topics if t.next_ok <= d]
        if not pool:
            return None
        if early:
            return min(pool, key=lambda t: (t.next_ok, -t.need))
        return max(pool, key=lambda t: t.need * (RELEVANT_BONUS if t.relevant else 1))


def apply_saved_settings(settings) -> None:  # type: ignore[no-untyped-def]
    saved = load_state().get("planner") or {}
    for k in EDITABLE:
        if k in saved:
            setattr(settings.planner, k, saved[k])


def settings_dict() -> dict[str, Any]:
    cfg = get_settings().planner
    return {k: getattr(cfg, k) for k in EDITABLE}


def _due_by_day(s: Session, today: date, days: int) -> dict[date, int]:
    out: dict[date, int] = {}
    end = datetime.combine(today + timedelta(days=days), datetime.min.time()).astimezone()
    for due in s.exec(
        select(Flashcard.due).where(
            col(Flashcard.is_current),
            col(Flashcard.suspended).is_(False),
            col(Flashcard.due).is_not(None),
            Flashcard.due < end,
        )
    ).all():
        d = max(today, due.astimezone().date())  # type: ignore[union-attr]
        out[d] = out.get(d, 0) + 1
    return out


def _subjects(s: Session, today: date) -> list[_Subject]:
    out = []
    for sub in s.exec(
        select(Subject).where(col(Subject.archived).is_(False), col(Subject.exam_date).is_not(None))
    ).all():
        if sub.exam_date is None or sub.exam_date <= today:
            continue
        topics = []
        for r in topic_stats(s, sub.id):  # type: ignore[arg-type]
            place = r["places"][0] if r["places"] else None
            need = UNTESTED_NEED if r["strength"] is None else max(0.05, 1 - r["strength"])
            topics.append(
                _Topic(
                    name=r["name"],
                    need=need,
                    relevant=bool(r["exam_relevant"]),
                    meta={
                        "topic": r["name"],
                        "strength": r["strength"],
                        **(
                            {"lecture_id": place["lecture_id"], "start_s": place["start_s"]}
                            if place
                            else {}
                        ),
                    },
                )
            )
        out.append(_Subject(sub.id, sub.name, sub.exam_date, topics))  # type: ignore[arg-type]
    return out


def _signature(s: Session, today: date) -> str:
    """What the plan depends on besides the day: settings, exam dates, open deadlines."""
    exams = s.exec(
        select(Subject.id, Subject.exam_date).where(
            col(Subject.archived).is_(False), col(Subject.exam_date).is_not(None)
        )
    ).all()
    dls = s.exec(
        select(Deadline.id, Deadline.due_date, Deadline.title).where(Deadline.status == "open")
    ).all()
    raw = json.dumps(
        [
            settings_dict(),
            sorted((i, str(d)) for i, d in exams if d and d > today),
            sorted((i, str(d), t) for i, d, t in dls),
        ],
        sort_keys=True,
    )
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


def build(s: Session, today: date | None = None) -> int:
    """Rebuild the plan from ``today`` on. Returns the number of blocks written."""
    today = today or date.today()
    cfg = get_settings().planner
    subjects = _subjects(s, today)
    s.exec(
        delete(StudyPlanItem).where(StudyPlanItem.day >= today, col(StudyPlanItem.done).is_(False))
    )  # type: ignore[call-overload]
    done_today = s.exec(
        select(StudyPlanItem).where(StudyPlanItem.day == today, col(StudyPlanItem.done))
    ).all()
    last = max((x.exam for x in subjects), default=today)
    horizon = min((last - today).days, MAX_DAYS)
    weekdays = set(cfg.weekdays) or set(range(7))
    study_days = [
        today + timedelta(days=i)
        for i in range(horizon + 1)
        if (today + timedelta(days=i)).weekday() in weekdays
    ]
    for x in subjects:
        before = [d for d in study_days if d < x.exam]
        if before:
            x.special.setdefault(before[-1], []).append(
                ("cheatsheet", f"Cheat sheet: {x.name}", CHEATSHEET_MIN)
            )
        if len(before) >= 2:
            mock_day = before[-3] if len(before) >= 3 else before[-2]
            x.special.setdefault(mock_day, []).append(
                ("mock_exam", f"Mock exam: {x.name}", MOCK_MIN)
            )
    due = _due_by_day(s, today, horizon + 1)
    has_cards = s.exec(select(Flashcard.id).where(col(Flashcard.is_current)).limit(1)).first()
    deadlines = s.exec(
        select(Deadline).where(
            Deadline.status == "open",
            col(Deadline.due_date).is_not(None),
            Deadline.due_date >= today,  # type: ignore[operator]
        )
    ).all()
    subject_names = {x.id: x.name for x in s.exec(select(Subject)).all()}
    done_kinds = {(i.kind, i.subject_id) for i in done_today}

    written = 0
    for i in range(horizon + 1):
        d = today + timedelta(days=i)
        items: list[StudyPlanItem] = []

        def add(  # only called within this iteration
            kind: str, title: str, minutes: int, sid: int | None, meta: dict, d: date = d
        ) -> None:
            items.append(  # noqa: B023
                StudyPlanItem(
                    day=d, kind=kind, title=title, minutes=minutes, subject_id=sid, meta=meta
                )
            )

        for x in subjects:
            if x.exam == d:
                add("exam", f"Exam: {x.name}", 0, x.id, {})
        for dl in deadlines:
            if dl.due_date == d:
                sub = subject_names.get(dl.subject_id or 0, "")
                add(
                    "deadline",
                    dl.title + (f" ({sub})" if sub else ""),
                    0,
                    dl.subject_id,
                    {"deadline_id": dl.id, "due_time": dl.due_time, "deadline_kind": dl.kind},
                )
        active = [x for x in subjects if x.exam > d]
        if d in study_days and (active or has_cards):
            budget = cfg.minutes_per_day
            if d == today:
                budget -= sum(i.minutes for i in done_today)
            n_due = due.get(d, 0)
            if n_due and ("review", None) not in done_kinds:
                mins = min(30, max(5, math.ceil(n_due * cfg.review_sec_per_card / 60)), budget)
                if mins > 0:
                    add("review", f"Review {n_due} cards", mins, None, {"cards": n_due})
                    budget -= mins
            for x in active:
                for kind, title, mins in x.special.get(d, []):
                    if budget < MIN_BLOCK or (kind, x.id) in done_kinds:
                        continue
                    mins = min(mins, budget)
                    add(kind, title, mins, x.id, {"subject_id": x.id})
                    budget -= mins
            given: dict[int, int] = {}
            practiced: set[int] = set()
            while active and budget >= MIN_BLOCK:
                mins = min(cfg.block_min, budget)
                ranked = sorted(
                    active,
                    key=lambda x: (
                        -x.remaining()
                        / math.sqrt(max((x.exam - d).days, 1))
                        / (1 + given.get(x.id, 0))
                    ),
                )
                # The neediest subject with a topic due (outside its spacing gap); else one
                # practice block per subject and day; else repeat a topic early.
                pick = next(((x, t) for x in ranked if (t := x.next_topic(d))), None)
                if pick is None and (x := next((x for x in ranked if x.id not in practiced), None)):
                    practiced.add(x.id)
                    add(
                        "practice",
                        f"Practice questions: {x.name}",
                        mins,
                        x.id,
                        {"subject_id": x.id},
                    )
                    given[x.id] = given.get(x.id, 0) + 1
                    budget -= mins
                    continue
                if pick is None:
                    x = ranked[0]
                    t = x.next_topic(d, early=True)
                    if t is None:
                        if x.topics:
                            add(
                                "practice",
                                f"Practice questions: {x.name}",
                                mins,
                                x.id,
                                {"subject_id": x.id},
                            )
                        else:  # no topics yet: revise the subject's material
                            add("topic", f"{x.name}: revise the lecture notes", mins, x.id, {})
                        given[x.id] = given.get(x.id, 0) + 1
                        budget -= mins
                        continue
                    pick = (x, t)
                x, t = pick
                given[x.id] = given.get(x.id, 0) + 1
                add("topic", f"{x.name}: {t.name}", mins, x.id, t.meta)
                t.need *= STUDIED_FACTOR
                t.next_ok = d + timedelta(days=GAPS[min(t.reps, len(GAPS) - 1)])
                t.reps += 1
                t.last = d
                budget -= mins
        start = len(done_today) if d == today else 0
        for pos, item in enumerate(items, start=start):
            item.position = pos
            s.add(item)
        written += len(items)
    s.commit()
    update_state(planner_built={"day": today.isoformat(), "sig": _signature(s, today)})
    bus.publish("planner", {})
    return written


def ensure_current(s: Session, today: date | None = None) -> None:
    """Rebuild when the day, the exam dates or the settings changed since the last build."""
    today = today or date.today()
    built = load_state().get("planner_built") or {}
    if built.get("day") != today.isoformat() or built.get("sig") != _signature(s, today):
        build(s, today)


def plan(s: Session, days: int = 21, today: date | None = None) -> dict[str, Any]:
    today = today or date.today()
    ensure_current(s, today)
    rows = s.exec(
        select(StudyPlanItem)
        .where(StudyPlanItem.day >= today, StudyPlanItem.day < today + timedelta(days=days))
        .order_by(col(StudyPlanItem.day), col(StudyPlanItem.position))
    ).all()
    colors = {x.id: x.color for x in s.exec(select(Subject)).all()}
    by_day: dict[date, list[dict[str, Any]]] = {}
    for r in rows:
        by_day.setdefault(r.day, []).append(item_dict(r, colors))
    weekdays = set(get_settings().planner.weekdays)
    last = s.exec(select(StudyPlanItem.day).order_by(col(StudyPlanItem.day).desc())).first()
    return {
        "settings": settings_dict(),
        "last_day": last.isoformat() if last and last >= today else None,
        "days": [
            {
                "date": d.isoformat(),
                "study_day": d.weekday() in weekdays,
                "minutes": sum(i["minutes"] for i in items),
                "done_minutes": sum(i["minutes"] for i in items if i["done"]),
                "items": items,
            }
            for d, items in sorted(by_day.items())
        ],
    }


def item_dict(r: StudyPlanItem, colors: dict[int, str] | None = None) -> dict[str, Any]:
    return {
        "id": r.id,
        "date": r.day.isoformat(),
        "kind": r.kind,
        "title": r.title,
        "minutes": r.minutes,
        "subject_id": r.subject_id,
        "subject_color": (colors or {}).get(r.subject_id or 0, ""),
        "meta": r.meta,
        "done": r.done,
    }


def set_done(s: Session, item_id: int, done: bool) -> dict[str, Any]:
    r = s.get(StudyPlanItem, item_id)
    if r is None:
        raise PlannerError("Plan item not found")
    r.done = done
    s.add(r)
    s.commit()
    s.refresh(r)
    bus.publish("planner", {})
    return item_dict(r)


def update_settings(s: Session, fields: dict[str, Any]) -> dict[str, Any]:
    cfg = get_settings().planner
    if "weekdays" in fields:
        days = sorted({int(x) for x in fields["weekdays"] if 0 <= int(x) <= 6})
        if not days:
            raise PlannerError("Pick at least one study day")
        fields["weekdays"] = days
    if "start_time" in fields:
        h, _, m = str(fields["start_time"]).partition(":")
        if not (h.isdigit() and m.isdigit() and int(h) < 24 and int(m) < 60):
            raise PlannerError("Start time must look like 18:00")
        fields["start_time"] = f"{int(h):02d}:{int(m):02d}"
    for k, v in fields.items():
        setattr(cfg, k, v)
    update_state(planner=settings_dict())
    build(s)
    return settings_dict()
