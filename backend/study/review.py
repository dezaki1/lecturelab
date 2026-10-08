"""Daily flashcard review with FSRS (``fsrs`` package).

A card's FSRS state lives in ``Flashcard.fsrs`` (``fsrs.Card.to_dict()``) with ``due`` mirrored
into an indexed column. An empty ``fsrs`` means the card was never reviewed ("new"). The daily
queue is: due reviews (oldest first), then new cards up to the daily limit, in lecture order.
"Today" is the local calendar day.
"""

from __future__ import annotations

import copy
from datetime import UTC, datetime, timedelta
from typing import Any

from fsrs import Card, Rating, Scheduler
from sqlmodel import Session, col, func, select

from backend.core.config import get_settings
from backend.core.models import Flashcard, Lecture, ReviewLog, Subject
from backend.core.state import load_state, update_state


class ReviewError(Exception):
    pass


def scheduler() -> Scheduler:
    return Scheduler(desired_retention=get_settings().study.desired_retention)


def now_utc() -> datetime:
    return datetime.now(UTC)


def local_day_start(now: datetime | None = None) -> datetime:
    local = (now or now_utc()).astimezone()
    return local.replace(hour=0, minute=0, second=0, microsecond=0)


def _fsrs_card(card: Flashcard) -> Card:
    if card.fsrs:
        return Card.from_dict(card.fsrs)
    return Card(card_id=card.id)


def _active():  # type: ignore[no-untyped-def]
    return (col(Flashcard.is_current), col(Flashcard.suspended).is_(False))


def new_introduced_today(s: Session, now: datetime | None = None) -> int:
    """Cards whose very first review happened today."""
    first = (
        select(ReviewLog.card_id, func.min(ReviewLog.reviewed_at).label("first"))
        .group_by(ReviewLog.card_id)
        .subquery()
    )
    start = local_day_start(now)
    return s.exec(select(func.count()).select_from(first).where(first.c.first >= start)).one()


def new_limit_today(s: Session, now: datetime | None = None) -> int:
    cfg = get_settings().study
    extra = load_state().get("extra_new", {})
    bonus = (
        extra.get("count", 0) if extra.get("date") == local_day_start(now).date().isoformat() else 0
    )
    return max(0, cfg.new_cards_per_day + bonus - new_introduced_today(s, now))


def _scope(q, subject_id: int | None):  # type: ignore[no-untyped-def]
    return q.where(Flashcard.subject_id == subject_id) if subject_id else q


def due_query(subject_id: int | None, now: datetime):  # type: ignore[no-untyped-def]
    q = select(Flashcard).where(*_active(), col(Flashcard.due).is_not(None), Flashcard.due <= now)
    return _scope(q, subject_id).order_by(col(Flashcard.due))


def new_query(subject_id: int | None):  # type: ignore[no-untyped-def]
    q = (
        select(Flashcard)
        .join(Lecture, col(Lecture.id) == col(Flashcard.lecture_id), isouter=True)
        .where(*_active(), col(Flashcard.due).is_(None))
    )
    return _scope(q, subject_id).order_by(col(Lecture.started_at), col(Flashcard.id))


def next_card(
    s: Session, subject_id: int | None = None, now: datetime | None = None
) -> Flashcard | None:
    now = now or now_utc()
    due = s.exec(due_query(subject_id, now).limit(1)).first()
    if due is not None:
        return due
    if new_limit_today(s, now) > 0:
        return s.exec(new_query(subject_id).limit(1)).first()
    return None


def counts(
    s: Session, subject_id: int | None = None, now: datetime | None = None
) -> dict[str, Any]:
    now = now or now_utc()
    due = s.exec(select(func.count()).select_from(due_query(subject_id, now).subquery())).one()
    new_total = s.exec(select(func.count()).select_from(new_query(subject_id).subquery())).one()
    learning_next = s.exec(
        _scope(
            select(func.min(Flashcard.due)).where(*_active(), Flashcard.due > now),
            subject_id,
        )
    ).one()
    return {
        "due": due,
        "new": min(new_total, new_limit_today(s, now)),
        "new_total": new_total,
        "next_due": learning_next,
    }


def preview_intervals(card: Flashcard, now: datetime | None = None) -> dict[int, float]:
    """Seconds until the card would be due again for each rating (for the button labels)."""
    now = now or now_utc()
    sch = Scheduler(desired_retention=get_settings().study.desired_retention, enable_fuzzing=False)
    out = {}
    for rating in Rating:
        updated, _ = sch.review_card(copy.deepcopy(_fsrs_card(card)), rating, now)
        out[int(rating)] = max(0.0, (updated.due - now).total_seconds())
    return out


def review(
    s: Session,
    card_id: int,
    rating: int,
    duration_ms: int | None = None,
    now: datetime | None = None,
) -> Flashcard:
    if rating not in (1, 2, 3, 4):
        raise ReviewError("rating must be 1 (again), 2 (hard), 3 (good) or 4 (easy)")
    card = s.get(Flashcard, card_id)
    if card is None:
        raise ReviewError("Card not found")
    now = now or now_utc()
    before, due_before = dict(card.fsrs), card.due
    updated, _ = scheduler().review_card(_fsrs_card(card), Rating(rating), now)
    card.fsrs = updated.to_dict()
    card.due = updated.due
    s.add(card)
    s.add(
        ReviewLog(
            card_id=card_id,
            rating=rating,
            reviewed_at=now,
            duration_ms=duration_ms,
            card_before=before,
            due_before=due_before,
            state_after=int(updated.state),
            stability_after=updated.stability,
        )
    )
    s.commit()
    s.refresh(card)
    return card


def undo_last(s: Session) -> Flashcard | None:
    """Revert the most recent review (if it was today)."""
    log = s.exec(select(ReviewLog).order_by(col(ReviewLog.id).desc())).first()
    if log is None or log.reviewed_at < local_day_start():
        return None
    card = s.get(Flashcard, log.card_id)
    if card is not None:
        card.fsrs = log.card_before
        card.due = log.due_before
        s.add(card)
    s.delete(log)
    s.commit()
    if card is not None:
        s.refresh(card)
    return card


def add_extra_new(count: int) -> None:
    today = local_day_start().date().isoformat()
    extra = load_state().get("extra_new", {})
    current = extra.get("count", 0) if extra.get("date") == today else 0
    update_state(extra_new={"date": today, "count": current + max(0, count)})


def stats(s: Session, now: datetime | None = None) -> dict[str, Any]:
    now = now or now_utc()
    start = local_day_start(now)
    reviewed_today = s.exec(
        select(func.count()).select_from(ReviewLog).where(ReviewLog.reviewed_at >= start)
    ).one()
    # Streak: consecutive local days (ending today or yesterday) with at least one review.
    stamps = s.exec(
        select(ReviewLog.reviewed_at).where(ReviewLog.reviewed_at >= start - timedelta(days=400))
    ).all()
    days = {t.astimezone().date() for t in stamps}
    streak, day = 0, start.date()
    if day not in days:
        day -= timedelta(days=1)
    while day in days:
        streak += 1
        day -= timedelta(days=1)
    by_subject = []
    for sub in s.exec(select(Subject).order_by(col(Subject.name))).all():
        c = counts(s, sub.id, now)
        if c["due"] or c["new_total"]:
            by_subject.append(
                {
                    "subject_id": sub.id,
                    "name": sub.name,
                    "color": sub.color,
                    "due": c["due"],
                    "new": c["new_total"],
                }
            )
    return {
        **counts(s, None, now),
        "reviewed_today": reviewed_today,
        "streak": streak,
        "by_subject": by_subject,
    }
