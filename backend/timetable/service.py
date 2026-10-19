"""Timetable: sync calendars, map courses to subjects, know what's on now, remind, auto-record.

- Sync: subscribed URLs every ``sync_hours`` (and on demand), uploaded files once. Events in
  the window are replaced; reminder/auto-record flags survive for unchanged events.
- Mapping: each course (normalised title) is matched to a subject by course code or name
  similarity; the student can override it (``mapped_by = "user"``) or ignore a course.
- Now: the event running now (from ``early_min`` before its start) decides the subject and
  session type of a recording started without choosing one (hotkey, tray, Record button).
- Reminders: "‹Subject› starts in 2 min" (tray + app), once per event.
- Auto-record (opt-in): during a slot, if the Zoom meeting window is open, recording starts for
  that subject; an auto-started recording stops once the slot is over and Zoom has closed.
"""

from __future__ import annotations

import difflib
import logging
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx2 as httpx
from sqlmodel import Session, col, delete, select

from backend.core.config import get_settings
from backend.core.db import session_scope
from backend.core.events import bus
from backend.core.library import abs_path
from backend.core.models import Subject, TimetableCourse, TimetableEvent, TimetableSource
from backend.timetable.ics import IcsError, course_key, parse

log = logging.getLogger(__name__)

MATCH_MIN = 0.6
FETCH_TIMEOUT_S = 30


class TimetableError(Exception):
    pass


def utcnow() -> datetime:
    return datetime.now(UTC)


# Matching ---------------------------------------------------------------------------------------
def match_subject(key: str, title: str, subjects: list[Subject]) -> int | None:
    best, best_score = None, 0.0
    for sub in subjects:
        if sub.course_code and sub.course_code.strip() and sub.course_code.strip() in title:
            return sub.id
        name = course_key(sub.name)
        if not name:
            continue
        score = difflib.SequenceMatcher(None, key, name).ratio()
        name_tokens, key_tokens = set(name.split()), set(key.split())
        if name_tokens and name_tokens <= key_tokens:
            score = max(score, 0.95)  # "machine learning supervised techniques" in a longer title
        elif key_tokens and key_tokens <= name_tokens and len(key_tokens) >= 2:
            score = max(score, 0.85)
        if score > best_score:
            best, best_score = sub.id, score
    return best if best_score >= MATCH_MIN else None


def _course(s: Session, key: str, title: str, subjects: list[Subject]) -> TimetableCourse:
    course = s.exec(select(TimetableCourse).where(TimetableCourse.key == key)).first()
    if course is None:
        course = TimetableCourse(
            key=key, label=title, subject_id=match_subject(key, title, subjects)
        )
        s.add(course)
        s.flush()
    elif course.mapped_by == "auto" and course.subject_id is None:
        course.subject_id = match_subject(key, title, subjects)  # subjects may have been added
        s.add(course)
    return course


# Sync -------------------------------------------------------------------------------------------
def fetch(url: str) -> bytes:
    if url.startswith("webcal://"):
        url = "https://" + url.removeprefix("webcal://")
    try:
        r = httpx.get(url, timeout=FETCH_TIMEOUT_S, follow_redirects=True)
        r.raise_for_status()
    except httpx.HTTPError as e:
        raise TimetableError(f"Couldn't download the calendar: {e}") from e
    return r.content


def sync_source(source_id: int, data: bytes | None = None, now: datetime | None = None) -> int:
    """Re-read one source. Returns the number of events stored."""
    now = now or utcnow()
    with session_scope() as s:
        src = s.get(TimetableSource, source_id)
        if src is None:
            raise TimetableError("Calendar not found")
        url, file_path = src.url, src.file_path
    try:
        if data is None:
            data = fetch(url) if url else abs_path(file_path).read_bytes()
        events = parse(data, now)
    except (TimetableError, IcsError, OSError) as e:
        with session_scope() as s:
            src = s.get(TimetableSource, source_id)
            if src is not None:
                src.error = str(e)
                s.add(src)
                s.commit()
        raise TimetableError(str(e)) from e
    with session_scope() as s:
        subjects = list(s.exec(select(Subject)).all())
        old = {
            (e.uid, e.start.astimezone(UTC).replace(tzinfo=UTC)): e
            for e in s.exec(
                select(TimetableEvent).where(TimetableEvent.source_id == source_id)
            ).all()
        }
        flags = {k: (e.reminded, e.auto_recorded) for k, e in old.items()}
        s.exec(delete(TimetableEvent).where(TimetableEvent.source_id == source_id))  # type: ignore[call-overload]
        for ev in events:
            course = _course(s, ev["key"], ev["summary"], subjects)
            reminded, auto = flags.get((ev["uid"], ev["start"]), (False, False))
            s.add(
                TimetableEvent(
                    source_id=source_id,
                    course_id=course.id,  # type: ignore[arg-type]
                    uid=ev["uid"],
                    summary=ev["summary"],
                    location=ev["location"],
                    kind=ev["kind"],
                    start=ev["start"],
                    end=ev["end"],
                    reminded=reminded,
                    auto_recorded=auto,
                )
            )
        src = s.get(TimetableSource, source_id)
        assert src is not None
        src.last_sync_at, src.error = now, ""
        s.add(src)
        s.commit()
    bus.publish("timetable", {})
    return len(events)


def add_source(name: str, url: str = "", data: bytes | None = None) -> TimetableSource:
    """Subscribe to a URL or import an uploaded file (kept in <data>/timetable/)."""
    if not url and data is None:
        raise TimetableError("Give a calendar link or a file")
    if data is not None:
        parse(data)  # validate before storing
    with session_scope() as s:
        src = TimetableSource(
            name=name or ("Calendar link" if url else "Calendar file"), url=url.strip()
        )
        s.add(src)
        s.commit()
        s.refresh(src)
        if data is not None:
            folder = get_settings().data_dir / "timetable"
            folder.mkdir(exist_ok=True)
            path = folder / f"calendar-{src.id}.ics"
            path.write_bytes(data)
            src.file_path = path.relative_to(get_settings().data_dir).as_posix()
            s.add(src)
            s.commit()
        source_id = src.id
    try:
        sync_source(source_id, data)  # type: ignore[arg-type]
    except TimetableError:
        with session_scope() as s:  # a link that doesn't work isn't worth keeping
            src = s.get(TimetableSource, source_id)
            if src is not None:
                s.delete(src)
                s.commit()
        raise
    with session_scope() as s:
        src = s.get(TimetableSource, source_id)
        assert src is not None
        s.expunge(src)
        return src


def remove_source(source_id: int) -> None:
    with session_scope() as s:
        src = s.get(TimetableSource, source_id)
        if src is None:
            raise TimetableError("Calendar not found")
        if src.file_path:
            abs_path(src.file_path).unlink(missing_ok=True)
        s.exec(delete(TimetableEvent).where(TimetableEvent.source_id == source_id))  # type: ignore[call-overload]
        s.delete(src)
        s.commit()
    bus.publish("timetable", {})


# Queries ----------------------------------------------------------------------------------------
def _event_dict(
    e: TimetableEvent, course: TimetableCourse, subjects: dict[int, Subject]
) -> dict[str, Any]:
    sub = subjects.get(course.subject_id) if course.subject_id else None
    return {
        "id": e.id,
        "summary": e.summary,
        "location": e.location,
        "kind": e.kind,
        "start": e.start.replace(tzinfo=UTC) if e.start.tzinfo is None else e.start,
        "end": e.end.replace(tzinfo=UTC) if e.end.tzinfo is None else e.end,
        "course_id": course.id,
        "ignored": course.ignored,
        "subject_id": sub.id if sub else None,
        "subject_name": sub.name if sub else "",
        "subject_color": sub.color if sub else "",
    }


def events_between(s: Session, start: datetime, end: datetime) -> list[dict[str, Any]]:
    rows = s.exec(
        select(TimetableEvent, TimetableCourse)
        .join(TimetableCourse, col(TimetableCourse.id) == col(TimetableEvent.course_id))
        .where(TimetableEvent.end > start, TimetableEvent.start < end)
        .order_by(col(TimetableEvent.start))
    ).all()
    subjects = {x.id: x for x in s.exec(select(Subject)).all()}
    return [_event_dict(e, c, subjects) for e, c in rows]  # type: ignore[arg-type]


def current_slot(s: Session, now: datetime | None = None) -> dict[str, Any] | None:
    """The mapped, not ignored event running now (or starting within ``early_min``)."""
    now = now or utcnow()
    early = timedelta(minutes=get_settings().timetable.early_min)
    for ev in events_between(s, now - timedelta(hours=6), now + early):
        if not ev["ignored"] and ev["subject_id"] and ev["start"] - early <= now < ev["end"]:
            return ev
    return None


def now_and_next(s: Session, now: datetime | None = None) -> dict[str, Any]:
    now = now or utcnow()
    upcoming = [
        e
        for e in events_between(s, now, now + timedelta(days=7))
        if not e["ignored"] and e["start"] > now
    ]
    local = now.astimezone()
    day_start = local.replace(hour=0, minute=0, second=0, microsecond=0)
    today = [
        e
        for e in events_between(
            s, day_start.astimezone(UTC), (day_start + timedelta(days=1)).astimezone(UTC)
        )
        if not e["ignored"]
    ]
    return {
        "current": current_slot(s, now),
        "next": upcoming[0] if upcoming else None,
        "today": today,
    }


def courses(s: Session) -> list[dict[str, Any]]:
    subjects = {x.id: x for x in s.exec(select(Subject)).all()}
    out = []
    for c in s.exec(select(TimetableCourse).order_by(col(TimetableCourse.label))).all():
        n = len(s.exec(select(TimetableEvent.id).where(TimetableEvent.course_id == c.id)).all())
        if not n:
            continue
        sub = subjects.get(c.subject_id) if c.subject_id else None
        out.append(
            {
                "id": c.id,
                "label": c.label,
                "subject_id": c.subject_id,
                "subject_name": sub.name if sub else "",
                "mapped_by": c.mapped_by,
                "ignored": c.ignored,
                "events": n,
            }
        )
    return out


def update_course(course_id: int, subject_id: int | None, ignored: bool | None) -> None:
    with session_scope() as s:
        c = s.get(TimetableCourse, course_id)
        if c is None:
            raise TimetableError("Course not found")
        if subject_id is not None:
            c.subject_id = subject_id or None
            c.mapped_by = "user"
        if ignored is not None:
            c.ignored = ignored
        s.add(c)
        s.commit()
    bus.publish("timetable", {})


# Scheduler --------------------------------------------------------------------------------------
class TimetableScheduler:
    """Sync, reminders and auto-record, checked every ``tick_s`` seconds."""

    def __init__(self, tick_s: float = 30.0, find_zoom=None) -> None:  # type: ignore[no-untyped-def]
        self.tick_s = tick_s
        self.find_zoom = find_zoom or _zoom_window_open
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="timetable", daemon=True)
        self._zoom_gone_since: datetime | None = None

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        while not self._stop.wait(self.tick_s):
            try:
                self.tick()
            except Exception:
                log.exception("Timetable scheduler error")

    def tick(self, now: datetime | None = None) -> None:
        now = now or utcnow()
        self.sync_due(now)
        self.remind(now)
        self.auto_record(now)

    def sync_due(self, now: datetime) -> None:
        hours = get_settings().timetable.sync_hours
        with session_scope() as s:
            due = [
                src.id
                for src in s.exec(select(TimetableSource).where(TimetableSource.url != "")).all()
                if src.last_sync_at is None
                or now - src.last_sync_at.replace(tzinfo=UTC) >= timedelta(hours=hours)
            ]
        for sid in due:
            try:
                sync_source(sid, now=now)  # type: ignore[arg-type]
            except TimetableError as e:
                log.warning("Timetable sync failed: %s", e)

    def remind(self, now: datetime) -> None:
        from backend.capture.recorder import get_recorder

        minutes = get_settings().timetable.remind_min
        if minutes <= 0:
            return
        horizon = now + timedelta(minutes=minutes)
        with session_scope() as s:
            for ev in events_between(s, now, horizon + timedelta(minutes=1)):
                if ev["ignored"] or not ev["subject_id"] or not now <= ev["start"] <= horizon:
                    continue
                row = s.get(TimetableEvent, ev["id"])
                if row is None or row.reminded:
                    continue
                row.reminded = True
                s.add(row)
                s.commit()
                if get_recorder().recording:
                    continue
                mins = max(1, round((ev["start"] - now).total_seconds() / 60))
                hotkey = get_settings().hotkeys.start_stop.replace("<", "").replace(">", "")
                bus.publish(
                    "notify",
                    {
                        "title": f"{ev['subject_name']} starts in {mins} min",
                        "message": f"Record? Press {hotkey.title()} or click Record."
                        + (f" ({ev['location']})" if ev["location"] else ""),
                        "level": "info",
                        "action": {
                            "type": "record",
                            "subject_id": ev["subject_id"],
                            "kind": ev["kind"],
                        },
                    },
                )

    def auto_record(self, now: datetime) -> None:
        from backend.capture.recorder import RecorderError, get_recorder

        if not get_settings().timetable.auto_record:
            return
        rec = get_recorder()
        with session_scope() as s:
            slot = current_slot(s, now)
            if rec.recording:
                self._maybe_stop(rec, now)
                return
            if slot is None or not self.find_zoom():
                return
            row = s.get(TimetableEvent, slot["id"])
            if row is None or row.auto_recorded:
                return  # started (and maybe stopped by hand) once already
            row.auto_recorded = True
            s.add(row)
            s.commit()
        try:
            rec.start(slot["subject_id"], kind=slot["kind"])
        except RecorderError as e:
            log.warning("Auto-record failed: %s", e)
            return
        from backend.core.state import update_state

        update_state(auto_recording={"event_id": slot["id"], "end": slot["end"].isoformat()})
        bus.publish(
            "notify",
            {
                "title": "● Recording started automatically",
                "message": f"{slot['subject_name']} (Zoom is open)",
                "level": "info",
            },
        )

    def _maybe_stop(self, rec, now: datetime) -> None:  # type: ignore[no-untyped-def]
        """Stop an auto-started recording once its slot is over and Zoom has been closed for
        a minute (a lecture that overruns keeps recording while Zoom is open)."""
        from backend.core.state import load_state, update_state

        auto = load_state().get("auto_recording")
        if not auto or now < datetime.fromisoformat(auto["end"]):
            return
        if self.find_zoom():
            self._zoom_gone_since = None
            return
        if self._zoom_gone_since is None:
            self._zoom_gone_since = now
            return
        if now - self._zoom_gone_since >= timedelta(seconds=60):
            self._zoom_gone_since = None
            update_state(auto_recording=None)
            rec.stop(reason="Zoom meeting ended")


def _zoom_window_open() -> bool:
    import sys

    if sys.platform != "win32":
        return False
    from backend.capture.screens import find_window, thread_dpi_aware

    thread_dpi_aware()
    return find_window(get_settings().slides) is not None


def uploaded_path(src: TimetableSource) -> Path | None:
    return abs_path(src.file_path) if src.file_path else None


def slot_for(s: Session, start: datetime, end: datetime) -> dict[str, Any] | None:
    """The mapped, not ignored timetable slot a recording from ``start`` to ``end`` belongs to:
    the one it overlaps most, if that covers at least half the recording (or 20 minutes)."""
    if end <= start:
        end = start + timedelta(minutes=1)
    best, best_overlap = None, 0.0
    for ev in events_between(s, start, end):
        if ev["ignored"] or not ev["subject_id"]:
            continue
        overlap = (min(end, ev["end"]) - max(start, ev["start"])).total_seconds()
        if overlap > best_overlap:
            best, best_overlap = ev, overlap
    need = min((end - start).total_seconds() / 2, 20 * 60)
    return best if best is not None and best_overlap >= need else None


def suggest_subject(s: Session, lecture: Any) -> dict[str, Any] | None:
    """If the timetable says this lecture happened during another subject's slot, what to show
    on the lecture page ("Recorded during ...: move it there?"). None if it fits."""
    start = lecture.started_at
    if start.tzinfo is None:
        start = start.replace(tzinfo=UTC)
    slot = slot_for(s, start, start + timedelta(seconds=lecture.duration_s or 0))
    if slot is None or slot["subject_id"] == lecture.subject_id:
        return None
    return {
        "subject_id": slot["subject_id"],
        "subject_name": slot["subject_name"],
        "summary": slot["summary"],
        "kind": slot["kind"],
        "start": slot["start"].isoformat(),
    }


def remember_suggestion(s: Session, lecture: Any) -> None:
    """Store the other-subject guess on the lecture (the page shows it with a Move button)."""
    try:
        hint = suggest_subject(s, lecture)
    except Exception:  # the timetable is only a hint: never break an import or a recording
        log.exception("Timetable check failed")
        return
    meta = {k: v for k, v in lecture.meta.items() if k != "slot_suggestion"}
    lecture.meta = {**meta, "slot_suggestion": hint} if hint else meta
