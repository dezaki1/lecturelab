"""Card management: browse/filter, bulk actions, duplicate detection and merging.

Only current cards (``is_current``) are managed; cards retired by regeneration stay hidden.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import or_
from sqlmodel import Session, col, func, select

from backend.core.models import Flashcard, Lecture, ReviewLog, Subject
from backend.search.retrieve import lecture_numbers
from backend.study.review import now_utc

STATES = ("new", "learning", "review", "relearning", "suspended", "due", "flagged")
FSRS_STATE = {1: "learning", 2: "review", 3: "relearning"}
DUPLICATE_THRESHOLD = 0.82


def card_state(card: Flashcard) -> str:
    if card.suspended:
        return "suspended"
    if card.due is None:
        return "new"
    return FSRS_STATE.get(int(card.fsrs.get("state", 1)), "learning")


def _filtered(
    subject_id: int | None,
    lecture_id: int | None,
    state: str | None,
    query: str,
    now: datetime,
):  # type: ignore[no-untyped-def]
    q = select(Flashcard).where(col(Flashcard.is_current))
    if subject_id:
        q = q.where(Flashcard.subject_id == subject_id)
    if lecture_id:
        q = q.where(Flashcard.lecture_id == lecture_id)
    if query.strip():
        like = f"%{query.strip()}%"
        q = q.where(or_(col(Flashcard.front).ilike(like), col(Flashcard.back).ilike(like)))
    active = col(Flashcard.suspended).is_(False)
    if state == "new":
        q = q.where(active, col(Flashcard.due).is_(None))
    elif state == "due":
        q = q.where(active, col(Flashcard.due).is_not(None), Flashcard.due <= now)
    elif state == "suspended":
        q = q.where(col(Flashcard.suspended))
    elif state == "flagged":
        q = q.where(Flashcard.verified == "flagged")
    elif state in ("learning", "review", "relearning"):
        code = {v: k for k, v in FSRS_STATE.items()}[state]
        q = q.where(
            active,
            col(Flashcard.due).is_not(None),
            func.json_extract(Flashcard.fsrs, "$.state") == code,
        )
    return q


def browse(
    s: Session,
    *,
    subject_id: int | None = None,
    lecture_id: int | None = None,
    state: str | None = None,
    query: str = "",
    sort: str = "lecture",
    offset: int = 0,
    limit: int = 50,
) -> dict[str, Any]:
    now = now_utc()
    base = _filtered(subject_id, lecture_id, state, query, now)
    total = s.exec(select(func.count()).select_from(base.subquery())).one()
    q = base.join(Lecture, col(Lecture.id) == col(Flashcard.lecture_id), isouter=True)
    order = {
        "lecture": (col(Lecture.started_at).desc(), col(Flashcard.id)),
        "due": (col(Flashcard.due).is_(None), col(Flashcard.due), col(Flashcard.id)),
        "newest": (col(Flashcard.id).desc(),),
        "lapses": (col(Flashcard.id),),  # re-sorted below
    }.get(sort, (col(Flashcard.id),))
    if sort == "lapses":
        cards = list(s.exec(q).all())
    else:
        cards = list(s.exec(q.order_by(*order).offset(offset).limit(limit)).all())
    reps = _reps(s, [c.id for c in cards])  # type: ignore[misc]
    if sort == "lapses":
        cards.sort(key=lambda c: (-reps.get(c.id, (0, 0))[1], c.id))  # type: ignore[arg-type]
        cards = cards[offset : offset + limit]
    return {"total": total, "items": describe(s, cards, reps)}


def describe(
    s: Session, cards: list[Flashcard], reps: dict[int, tuple[int, int]] | None = None
) -> list[dict[str, Any]]:
    """Cards plus state, review counts and lecture/subject labels, for the card browser."""
    if reps is None:
        reps = _reps(s, [c.id for c in cards])  # type: ignore[misc]
    lectures = {
        lec.id: lec
        for lec in s.exec(
            select(Lecture).where(col(Lecture.id).in_({c.lecture_id for c in cards}))
        ).all()
    }
    numbers = lecture_numbers(s, {c.subject_id for c in cards})
    subjects = {x.id: x for x in s.exec(select(Subject)).all()}
    items = []
    for c in cards:
        lec = lectures.get(c.lecture_id)
        n, lapses = reps.get(c.id, (0, 0))  # type: ignore[arg-type]
        items.append(
            {
                **c.model_dump(exclude={"fsrs"}),
                "state": card_state(c),
                "reps": n,
                "lapses": lapses,
                "stability": c.fsrs.get("stability"),
                "lecture_number": numbers.get(c.lecture_id) if c.lecture_id else None,
                "lecture_title": lec.title if lec else "",
                "subject_name": subjects[c.subject_id].name if c.subject_id in subjects else "",
                "subject_color": subjects[c.subject_id].color if c.subject_id in subjects else "",
            }
        )
    return items


def _reps(s: Session, ids: list[int]) -> dict[int, tuple[int, int]]:
    """card id -> (reviews, lapses = times rated Again)."""
    if not ids:
        return {}
    rows = s.exec(
        select(ReviewLog.card_id, func.count(), func.sum(func.iif(ReviewLog.rating == 1, 1, 0)))
        .where(col(ReviewLog.card_id).in_(ids))
        .group_by(ReviewLog.card_id)
    ).all()
    return {cid: (n, lapses or 0) for cid, n, lapses in rows}


def matching(
    s: Session,
    *,
    subject_id: int | None = None,
    lecture_id: int | None = None,
    state: str | None = None,
    query: str = "",
    ids: list[int] | None = None,
) -> list[Flashcard]:
    """All cards matching the browser's filters (or exactly ``ids``), for export."""
    if ids:
        return selected_cards(s, ids)
    q = _filtered(subject_id, lecture_id, state, query, now_utc())
    return list(s.exec(q.order_by(col(Flashcard.subject_id), col(Flashcard.id))).all())


def selected_cards(s: Session, ids: list[int]) -> list[Flashcard]:
    return list(
        s.exec(select(Flashcard).where(col(Flashcard.id).in_(ids), col(Flashcard.is_current)))
    )


def bulk(s: Session, ids: list[int], action: str) -> int:
    """suspend | unsuspend | delete | forget (reset scheduling to new)."""
    cards = selected_cards(s, ids)
    for c in cards:
        if action == "suspend":
            c.suspended = True
        elif action == "unsuspend":
            c.suspended = False
        elif action == "forget":
            c.fsrs, c.due = {}, None
        elif action == "delete":
            s.delete(c)
            continue
        else:
            raise ValueError(f"Unknown action {action!r}")
        s.add(c)
    s.commit()
    return len(cards)


# Duplicates ------------------------------------------------------------------------------------
def _norm(text: str) -> str:
    return " ".join(text.lower().split())


@dataclass
class _Item:
    id: int
    key: str


def duplicate_groups(s: Session, subject_id: int | None = None) -> list[list[int]]:
    """Groups of near-identical current cards within each subject."""
    q = select(Flashcard).where(col(Flashcard.is_current))
    if subject_id:
        q = q.where(Flashcard.subject_id == subject_id)
    by_subject: dict[int, list[_Item]] = {}
    for c in s.exec(q.order_by(col(Flashcard.id))).all():
        by_subject.setdefault(c.subject_id, []).append(
            _Item(c.id, _norm(c.front) + " || " + _norm(c.back))  # type: ignore[arg-type]
        )
    groups: list[list[int]] = []
    for items in by_subject.values():
        groups.extend(_clusters(items))
    return groups


def _clusters(items: list[_Item]) -> list[list[int]]:
    """Connected groups of similar items (union-find over pairwise similarity)."""
    parent = {it.id: it.id for it in items}

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i, a in enumerate(items):
        for b in items[i + 1 :]:
            m = difflib.SequenceMatcher(None, a.key, b.key, autojunk=False)
            # cheap upper bounds first: most pairs are rejected without the full ratio()
            if (
                m.real_quick_ratio() >= DUPLICATE_THRESHOLD
                and m.quick_ratio() >= DUPLICATE_THRESHOLD
                and m.ratio() >= DUPLICATE_THRESHOLD
            ):
                parent[find(b.id)] = find(a.id)
    clusters: dict[int, list[int]] = {}
    for it in items:
        clusters.setdefault(find(it.id), []).append(it.id)
    return [sorted(g) for g in clusters.values() if len(g) > 1]


def merge(s: Session, keep_id: int, merge_ids: list[int]) -> Flashcard:
    """Fold duplicates into one card: union of sources and tags; the most-practised scheduling
    state wins; review history moves over so stats and streaks stay intact."""
    keep = s.get(Flashcard, keep_id)
    if keep is None or not keep.is_current:
        raise ValueError("Card to keep not found")
    others = [c for c in selected_cards(s, merge_ids) if c.id != keep_id]
    if any(c.subject_id != keep.subject_id for c in others):
        raise ValueError("Only cards of the same subject can be merged")
    group = [keep, *others]
    reviews = {
        c.id: s.exec(select(func.count()).where(ReviewLog.card_id == c.id)).one() for c in group
    }
    best = max(group, key=lambda c: (reviews[c.id], c.fsrs.get("stability") or 0))
    keep.fsrs, keep.due = dict(best.fsrs), best.due
    refs = list(keep.source_refs)
    for c in others:
        refs += [r for r in c.source_refs if r not in refs]
        keep.tags = sorted({*keep.tags, *c.tags})
    keep.source_refs = refs
    keep.suspended = all(c.suspended for c in group)
    keep.edited_by_me = True  # regeneration must not retire the merged card
    for c in others:
        for log in s.exec(select(ReviewLog).where(ReviewLog.card_id == c.id)).all():
            log.card_id = keep_id
            s.add(log)
        s.flush()
        s.delete(c)
    s.add(keep)
    s.commit()
    s.refresh(keep)
    return keep
