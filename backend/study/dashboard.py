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
# Readiness: flashcards weigh more than exam questions (there are more of them and they are
# the course's facts); questions show whether you can use what you know.
CARD_WEIGHT, QUESTION_WEIGHT = 0.7, 0.3


def _mean(xs: list[float]) -> float | None:
    return sum(xs) / len(xs) if xs else None


def _r3(x: float | None) -> float | None:
    return None if x is None else round(x, 3)


def readiness(card_recall: list[float | None], question_scores: list[float | None]) -> dict:
    """How ready you are for a course, 0..1, and its parts.

    ``card_recall``: per active card, the chance you'd recall it today, or None if you never
    studied it. ``question_scores``: per question, your latest score (0..1), or None if never
    answered. An unseen card or unanswered question counts as 0, so the score grows with
    coverage, memory and practice alike.
    """
    seen = [r for r in card_recall if r is not None]
    answered = [q for q in question_scores if q is not None]
    total = len(card_recall) + len(question_scores)
    parts: list[tuple[float, float]] = []
    if card_recall:
        parts.append((CARD_WEIGHT, sum(seen) / len(card_recall)))
    if question_scores:
        parts.append((QUESTION_WEIGHT, sum(answered) / len(question_scores)))
    weight = sum(w for w, _ in parts)
    score = sum(w * v for w, v in parts) / weight if weight else None
    return {
        "score": _r3(score),
        "covered": _r3((len(seen) + len(answered)) / total) if total else None,
        "remembered": _r3(_mean(seen)),
        "practised": _r3(_mean(answered)),
        "cards_unseen": len(card_recall) - len(seen),
        "questions_unanswered": len(question_scores) - len(answered),
    }


def _latest_scores(s: Session, subject_id: int) -> dict[int, float]:
    """Question id -> your latest answer's score (0..1)."""
    rows = s.exec(
        select(Attempt.question_id, Attempt.score, Attempt.max_points, Attempt.correct)
        .join(Question, col(Question.id) == col(Attempt.question_id))
        .where(Question.subject_id == subject_id)
        .order_by(col(Attempt.created_at), col(Attempt.id))
    ).all()
    out: dict[int, float] = {}
    for qid, score, mx, correct in rows:
        if mx and mx > 0:
            out[qid] = max(0.0, min(1.0, score / mx))
        else:
            out[qid] = 1.0 if correct else 0.0
    return out


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
        questions = s.exec(
            select(Question.id).where(Question.subject_id == sub.id, col(Question.is_current))
        ).all()
        latest = _latest_scores(s, sub.id)  # type: ignore[arg-type]
        ready = readiness(
            [None if c.due is None else r for c, r in zip(active, recall, strict=True)],
            [latest.get(q) for q in questions],  # type: ignore[arg-type]
        )
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
                "questions": len(questions),
                "readiness": ready,
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
