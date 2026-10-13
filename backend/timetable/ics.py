"""Parse .ics calendars (KUSSS export, Outlook, Google...) into plain event dicts.

Recurring events are expanded (``recurring_ical_events``) over a window around today, with
time zones and DST handled. Titles are normalised into a course key: KUSSS-style LVA numbers
("344.031") and course types ("VL", "UE", ...) are removed, so all sessions of a course share
one key, and the type is kept separately (it becomes the lecture's ``kind``).
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime, timedelta
from typing import Any

import icalendar
import recurring_ical_events

TYPES = ("VL", "VO", "UE", "KV", "PR", "SE", "KS", "PS", "VU", "IK", "EX", "LAB", "TUT")
TYPE_RE = re.compile(rf"(?<![\w.])(?:{'|'.join(TYPES)})(?![\w])", re.I)
CODE_RE = re.compile(r"\b\d{3}\.\d{3}\b")
PAST_DAYS = 30
FUTURE_DAYS = 200


class IcsError(Exception):
    pass


# Course type in the title -> the lecture's session type (VL/VO/UE/other) in LectureLab.
LECTURE_KIND = {
    "VL": "VL", "VO": "VO", "VU": "VL", "KV": "VL", "IK": "VL",
    "UE": "UE", "PR": "UE", "PS": "UE", "LAB": "UE", "TUT": "UE", "EX": "UE",
    "SE": "other", "KS": "other",
}  # fmt: skip


def course_kind(title: str) -> str:
    m = TYPE_RE.search(title)
    return LECTURE_KIND.get(m.group(0).upper(), "other") if m else ""


def course_key(title: str) -> str:
    t = CODE_RE.sub(" ", title)
    t = TYPE_RE.sub(" ", t)
    t = re.sub(r"[()\[\],;:/|_-]+", " ", t.lower())
    return " ".join(t.split())


def _as_utc(value: Any) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.astimezone()  # floating time: the laptop's zone
        return value.astimezone(UTC)
    if isinstance(value, date):  # all-day event
        return datetime(value.year, value.month, value.day, tzinfo=UTC)
    raise IcsError(f"Unexpected date value {value!r}")


def parse(data: bytes, now: datetime | None = None) -> list[dict[str, Any]]:
    """Timed events between PAST_DAYS ago and FUTURE_DAYS ahead, sorted by start."""
    try:
        cal = icalendar.Calendar.from_ical(data)
    except Exception as e:
        raise IcsError(f"Not a valid .ics calendar: {e}") from e
    now = now or datetime.now(UTC)
    window = recurring_ical_events.of(cal).between(
        now - timedelta(days=PAST_DAYS), now + timedelta(days=FUTURE_DAYS)
    )
    out = []
    for e in window:
        start_raw = e.decoded("DTSTART") if e.get("DTSTART") else None
        if start_raw is None or not isinstance(start_raw, datetime):
            continue  # all-day items (holidays, deadlines) aren't lecture slots
        start = _as_utc(start_raw)
        end = _as_utc(e.decoded("DTEND")) if e.get("DTEND") else start + timedelta(minutes=90)
        title = str(e.get("SUMMARY") or "").strip()
        if not title or str(e.get("STATUS") or "").upper() == "CANCELLED":
            continue
        out.append(
            {
                "uid": str(e.get("UID") or ""),
                "summary": title,
                "location": str(e.get("LOCATION") or "").strip(),
                "start": start,
                "end": max(end, start + timedelta(minutes=10)),
                "kind": course_kind(title),
                "key": course_key(title),
            }
        )
    out.sort(key=lambda x: x["start"])
    return out
