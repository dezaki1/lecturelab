"""Deadlines: collected from each lecture's announcements note, reviewed, or added by hand.

Every generation of the announcements note re-collects its items. Items are keyed by lecture and
normalised text, so a regenerated note doesn't duplicate them. A choice the student already made
(accepted, edited, dismissed, done) is never overwritten. Suggestions from the old version that
the new one no longer contains are dropped.
"""

from __future__ import annotations

import logging
import re
from datetime import date
from typing import Any

from sqlmodel import Session, col, select

from backend.core.events import bus
from backend.core.models import Deadline, Lecture, Note, Subject, utcnow
from backend.pipeline.generate import parse_ts

log = logging.getLogger(__name__)

KINDS = ("deadline", "exam", "assignment", "admin", "other")
STATUSES = ("suggested", "open", "done", "dismissed")
# Announcements of these kinds are always worth a look; admin/other only with a date.
ALWAYS = ("deadline", "exam", "assignment")
TIME_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")


class DeadlineError(Exception):
    pass


def _key(lecture_id: int, text: str) -> str:
    return f"L{lecture_id}:" + " ".join(re.sub(r"[^\w]+", " ", text.casefold()).split())[:120]


def _date(value: str) -> date | None:
    try:
        return date.fromisoformat(value.strip()[:10]) if value.strip() else None
    except ValueError:
        return None


def collect_from_note(s: Session, note: Note) -> int:
    """Sync suggestions with a (current) announcements note. Returns the number of new items."""
    if note.lecture_id is None or note.kind != "announcements":
        return 0
    items = (note.data or {}).get("items") or []
    wanted: dict[str, dict[str, Any]] = {}
    for it in items:
        kind = it.get("kind") if it.get("kind") in KINDS else "other"
        text = str(it.get("text") or "").strip()
        due = _date(str(it.get("date") or ""))
        if not text or (kind not in ALWAYS and due is None):
            continue
        if due is not None and due < date.today():  # already over: nothing to act on
            continue
        wanted.setdefault(
            _key(note.lecture_id, text),
            {
                "kind": kind,
                "title": text,
                "due_date": due,
                "t_s": parse_ts(str(it.get("at") or "")),
            },
        )
    existing = {
        d.source_key: d
        for d in s.exec(
            select(Deadline).where(
                Deadline.lecture_id == note.lecture_id, Deadline.source == "lecture"
            )
        ).all()
    }
    for key, d in existing.items():
        if key not in wanted and d.status == "suggested":
            s.delete(d)
    added = 0
    for key, w in wanted.items():
        if key in existing:
            continue
        s.add(
            Deadline(
                subject_id=note.subject_id,
                lecture_id=note.lecture_id,
                source="lecture",
                source_key=key,
                status="suggested",
                **w,
            )
        )
        added += 1
    s.commit()
    if added:
        bus.publish("deadlines", {"subject_id": note.subject_id})
    return added


def backfill(s: Session) -> int:
    """Collect from every current announcements note that has no deadline rows yet."""
    done = set(
        s.exec(select(Deadline.lecture_id).where(Deadline.source == "lecture").distinct()).all()
    )
    n = 0
    for note in s.exec(
        select(Note).where(Note.kind == "announcements", col(Note.is_current))
    ).all():
        if note.lecture_id not in done:
            n += collect_from_note(s, note)
    return n


def _row(d: Deadline, subjects: dict[int, Subject], lectures: dict[int, Lecture]) -> dict:
    sub = subjects.get(d.subject_id) if d.subject_id else None
    lec = lectures.get(d.lecture_id) if d.lecture_id else None
    return {
        "id": d.id,
        "kind": d.kind,
        "title": d.title,
        "due_date": d.due_date.isoformat() if d.due_date else None,
        "due_time": d.due_time,
        "status": d.status,
        "source": d.source,
        "subject_id": d.subject_id,
        "subject_name": sub.name if sub else "",
        "subject_color": sub.color if sub else "",
        "subject_exam_date": sub.exam_date.isoformat() if sub and sub.exam_date else None,
        "lecture_id": d.lecture_id,
        "lecture_title": lec.title if lec else "",
        "t_s": d.t_s,
    }


def listing(s: Session, today: date | None = None) -> dict[str, Any]:
    today = today or date.today()
    rows = s.exec(select(Deadline).where(Deadline.status != "dismissed")).all()
    subjects = {x.id: x for x in s.exec(select(Subject)).all()}
    lec_ids = {d.lecture_id for d in rows if d.lecture_id}
    lectures = {x.id: x for x in s.exec(select(Lecture).where(col(Lecture.id).in_(lec_ids))).all()}
    far = date.max

    def order(d: Deadline) -> tuple:
        return (d.due_date or far, d.due_time or "99", d.id)

    out: dict[str, list] = {"suggested": [], "open": [], "done": []}
    for d in sorted(rows, key=order):
        out[d.status].append(_row(d, subjects, lectures))
    out["done"] = out["done"][-30:]
    out["exams"] = [
        {
            "subject_id": x.id,
            "subject_name": x.name,
            "subject_color": x.color,
            "date": x.exam_date.isoformat(),
            "days_left": (x.exam_date - today).days,
        }
        for x in sorted(subjects.values(), key=lambda x: x.exam_date or far)
        if x.exam_date and not x.archived and x.exam_date >= today
    ]
    return out


def _apply(d: Deadline, fields: dict[str, Any]) -> None:
    if "kind" in fields and fields["kind"] not in KINDS:
        raise DeadlineError(f"Unknown kind {fields['kind']!r}")
    if "status" in fields and fields["status"] not in STATUSES:
        raise DeadlineError(f"Unknown status {fields['status']!r}")
    if fields.get("due_time") and not TIME_RE.match(fields["due_time"]):
        raise DeadlineError("Time must look like 14:30")
    if "title" in fields and not str(fields["title"]).strip():
        raise DeadlineError("A deadline needs a title")
    for k, v in fields.items():
        setattr(d, k, v.strip() if isinstance(v, str) else v)
    if d.due_time and len(d.due_time) == 4:
        d.due_time = "0" + d.due_time
    d.updated_at = utcnow()


def create(s: Session, fields: dict[str, Any]) -> Deadline:
    if fields.get("subject_id") and s.get(Subject, fields["subject_id"]) is None:
        raise DeadlineError("Subject not found")
    d = Deadline(title="", status="open", source="manual")
    _apply(d, {"kind": "deadline", **fields})
    s.add(d)
    s.commit()
    s.refresh(d)
    bus.publish("deadlines", {"subject_id": d.subject_id})
    return d


def update(s: Session, deadline_id: int, fields: dict[str, Any]) -> Deadline:
    d = s.get(Deadline, deadline_id)
    if d is None:
        raise DeadlineError("Deadline not found")
    _apply(d, fields)
    s.add(d)
    s.commit()
    s.refresh(d)
    bus.publish("deadlines", {"subject_id": d.subject_id})
    return d


def delete(s: Session, deadline_id: int) -> None:
    d = s.get(Deadline, deadline_id)
    if d is None:
        raise DeadlineError("Deadline not found")
    if d.source == "lecture":  # keep the row so a regeneration doesn't suggest it again
        d.status = "dismissed"
        s.add(d)
    else:
        s.delete(d)
    s.commit()
    bus.publish("deadlines", {"subject_id": d.subject_id})
