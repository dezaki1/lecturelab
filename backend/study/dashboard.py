"""Numbers for the home dashboard: today's work, streak, retention, mastery per subject, exams.

Mastery is the average recall probability (FSRS retrievability) over a subject's active cards
right now, with unseen cards counting as 0, i.e. "how much of this deck would I recall today".
Retention is the share of reviews of mature-state cards (FSRS "review") in the last 30 days
that weren't rated Again, the usual "true retention".
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from fsrs import Card, Scheduler
from sqlmodel import Session, col, func, select

from backend.core.config import get_settings
from backend.core.models import (
    Attempt,
    Flashcard,
    Lecture,
    LectureStatus,
    Question,
    ReviewLog,
    Subject,
)
from backend.study.review import counts, local_day_start, now_utc, stats

MATURE_DAYS = 21.0


def retrievability(sch: Scheduler, card: Flashcard, now: datetime) -> float:
    if card.due is None or not card.fsrs or card.fsrs.get("stability") is None:
        return 0.0
    try:
        return float(sch.get_card_retrievability(Card.from_dict(card.fsrs), now))
    except (KeyError, ValueError, TypeError):
        return 0.0


def _activity(s: Session, now: datetime, days: int = 30) -> list[dict[str, Any]]:
    start = local_day_start(now) - timedelta(days=days - 1)
    rows = s.exec(
        select(ReviewLog.reviewed_at, ReviewLog.rating).where(ReviewLog.reviewed_at >= start)
    ).all()
    per_day: dict[date, list[int]] = {}
    for t, rating in rows:
        per_day.setdefault(t.astimezone().date(), []).append(rating)
    out = []
    for i in range(days):
        d = (start + timedelta(days=i)).date()
        ratings = per_day.get(d, [])
        out.append({"date": d.isoformat(), "reviews": len(ratings), "again": ratings.count(1)})
    return out


def _forecast(s: Session, now: datetime, days: int = 7) -> list[dict[str, Any]]:
    """Cards due per day for the next week (today includes anything overdue)."""
    start = local_day_start(now)
    end = start + timedelta(days=days)
    rows = s.exec(
        select(Flashcard.due).where(
            col(Flashcard.is_current),
            col(Flashcard.suspended).is_(False),
            col(Flashcard.due).is_not(None),
            Flashcard.due < end,
        )
    ).all()
    buckets = [0] * days
    for due in rows:
        i = max(0, (due.astimezone().date() - start.date()).days)  # type: ignore[union-attr]
        if i < days:
            buckets[i] += 1
    return [
        {"date": (start + timedelta(days=i)).date().isoformat(), "due": n}
        for i, n in enumerate(buckets)
    ]


def _retention(s: Session, now: datetime) -> dict[str, Any]:
    logs = s.exec(
        select(ReviewLog.rating, ReviewLog.card_before).where(
            ReviewLog.reviewed_at >= now - timedelta(days=30)
        )
    ).all()
    mature = [r for r, before in logs if before.get("state") == 2]
    passed = sum(1 for r in mature if r > 1)
    return {"rate": round(passed / len(mature), 3) if mature else None, "reviews": len(mature)}


def _subject_rows(s: Session, now: datetime) -> list[dict[str, Any]]:
    sch = Scheduler(desired_retention=get_settings().study.desired_retention)
    today = now.astimezone().date()
    practice = {
        sid: (n, score or 0.0, mx or 0.0)
        for sid, n, score, mx in s.exec(
            select(
                Question.subject_id,
                func.count(col(Attempt.id)),
                func.sum(Attempt.score),
                func.sum(Attempt.max_points),
            )
            .join(Question, col(Question.id) == col(Attempt.question_id))
            .group_by(Question.subject_id)
        ).all()
    }
    out = []
    for sub in s.exec(select(Subject).where(col(Subject.archived).is_(False))).all():
        cards = s.exec(
            select(Flashcard).where(Flashcard.subject_id == sub.id, col(Flashcard.is_current))
        ).all()
        active = [c for c in cards if not c.suspended]
        recall = [retrievability(sch, c, now) for c in active]
        stab = [c.fsrs.get("stability") or 0 for c in active if c.due is not None]
        lectures = s.exec(select(Lecture.status).where(Lecture.subject_id == sub.id)).all()
        n, score, mx = practice.get(sub.id, (0, 0.0, 0.0))  # type: ignore[arg-type]
        c = counts(s, sub.id, now)
        out.append(
            {
                "subject_id": sub.id,
                "name": sub.name,
                "color": sub.color,
                "exam_date": sub.exam_date.isoformat() if sub.exam_date else None,
                "days_left": (sub.exam_date - today).days if sub.exam_date else None,
                "lectures": len(lectures),
                "lectures_ready": sum(1 for st in lectures if st == LectureStatus.ready),
                "cards": len(active),
                "new": sum(1 for c_ in active if c_.due is None),
                "young": sum(1 for x in stab if x < MATURE_DAYS),
                "mature": sum(1 for x in stab if x >= MATURE_DAYS),
                "suspended": len(cards) - len(active),
                "due": c["due"],
                "mastery": round(sum(recall) / len(recall), 3) if recall else None,
                "practice": {"attempts": n, "score": round(score, 1), "max": round(mx, 1)},
            }
        )
    # Exams soonest first, then subjects without an exam date by name.
    out.sort(
        key=lambda r: (r["days_left"] is None or r["days_left"] < 0, r["days_left"] or 0, r["name"])
    )
    return out


def dashboard(s: Session, now: datetime | None = None) -> dict[str, Any]:
    now = now or now_utc()
    return {
        **stats(s, now),
        "retention": _retention(s, now),
        "activity": _activity(s, now),
        "forecast": _forecast(s, now),
        "subjects": _subject_rows(s, now),
        "desired_retention": get_settings().study.desired_retention,
    }
