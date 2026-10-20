"""Watched Moodle courses: new Opencast recordings come in by themselves.

The browser extension does the looking, since only the browser is logged in to Moodle: once a
minute it asks ``state()`` which courses are due, fetches their pages and hands what it found to
``report()`` (episode id, title, length and a signed media link that works without cookies).
LectureLab decides everything else here: when a course is due, what is new, what the user
already has, and it downloads new recordings into the course's subject.

The first scan of a course finds its backlog; that goes to the review list instead of being
downloaded (some of it the user may have recorded themselves). Everything found later is
downloaded straight away.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from sqlmodel import Session, col, select

from backend.core.events import bus
from backend.core.models import Lecture, Subject, WatchedCourse, WatchedRecording, utcnow
from backend.pipeline.download import queue_download, retry_download

log = logging.getLogger(__name__)

SCAN_EVERY = timedelta(hours=1)
# Recordings go up a few hours after the lecture: check more often then.
SCAN_AFTER_SLOT = timedelta(minutes=15)
AFTER_SLOT_WINDOW = timedelta(hours=6)
RETRY_WANTED = timedelta(minutes=2)
SAME_LENGTH_S = 120

_scan_requested = False

_DATE = re.compile(r"\b(\d{1,2})[./](\d{1,2})[./](\d{4}|\d{2})\b")
_KIND = re.compile(r"\b(VL|VO|UE)\b")
_NOISE = re.compile(
    r"\d{1,2}[./]\d{1,2}[./]\d{2,4}|\d{1,2}:\d{2}(:\d{2})?|\bHS\s*\d+\b|\(opencast\)"
    r"|\b(recording|aufzeichnung|videos?|lecture videos|VL|VO|UE)\b",
    re.I,
)


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def request_scan() -> None:
    """Make every course due at the extension's next check (within a minute)."""
    global _scan_requested
    _scan_requested = True


def recorded_on(*titles: str) -> date | None:
    """The date in a recording's title ("HS 7 14.10.2026", "Recording 07.10.26")."""
    for title in titles:
        if m := _DATE.search(title or ""):
            d, mo, y = (int(x) for x in m.groups())
            try:
                return date(y + 2000 if y < 100 else y, mo, d)
            except ValueError:
                continue
    return None


def tidy_title(title: str) -> tuple[str, bool]:
    """(title to show, whether it says anything): "HS 7 14.10.2026" is only a date and a room,
    so generation names that lecture; "2. Basic classifiers and evaluation metrics" stays."""
    title = re.sub(r"\s*Video\s*\(Opencast\)\s*$|\s*Opencast Video\s*$", "", title or "").strip()
    # Series lists say "1:26:15 19.10.2026 HS 7 19.10.26": drop the length and the repeated date.
    title = re.sub(r"^\d{1,2}:\d{2}(:\d{2})?\s+", "", title)
    if len(_DATE.findall(title)) > 1:
        title = re.sub(r"\s+\d{1,2}[./]\d{1,2}[./]\d{2,4}$", "", title)
    words = re.findall(r"[A-Za-zÄÖÜäöüß]{3,}", _NOISE.sub(" ", title))
    return title, len(words) >= 2


def _started_at(s: Session, subject_id: int, day: date | None, kind: str) -> datetime | None:
    """When the recorded lecture began: that day's timetable slot of the subject, else noon."""
    if day is None:
        return None
    from backend.timetable.service import events_between

    local_noon = datetime.combine(day, time(12)).astimezone()
    start = local_noon - timedelta(hours=12)
    slots = [
        ev
        for ev in events_between(s, start, start + timedelta(days=1))
        if ev["subject_id"] == subject_id and not ev["ignored"]
    ]
    slots.sort(key=lambda ev: (ev["kind"] != kind, ev["start"]))
    return slots[0]["start"] if slots else local_noon.astimezone(UTC)


def _episode_lectures(s: Session) -> dict[str, int]:
    """Opencast episode id -> lecture, for lectures that came from Opencast (including links
    sent with the extension before watching existed: their URL holds the episode id)."""
    found: dict[str, int] = {}
    uuid = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
    for lecture in s.exec(select(Lecture)).all():
        oc = lecture.meta.get("opencast") or {}
        if oc.get("episode"):
            found[oc["episode"]] = lecture.id  # type: ignore[assignment]
            continue
        url = str((lecture.meta.get("download") or {}).get("url", ""))
        if "engage-player_" in url and (m := uuid.search(url.split("engage-player_", 1)[1])):
            found[m.group(0)] = lecture.id  # type: ignore[assignment]
    return found


def _same_length(s: Session, course: WatchedCourse, duration: float, taken: set[int]) -> str:
    """A lecture of the subject with the same length and no episode id: probably this one."""
    if duration <= 0:
        return ""
    for lecture in s.exec(select(Lecture).where(Lecture.subject_id == course.subject_id)).all():
        if lecture.id in taken or not lecture.duration_s:
            continue
        if abs(lecture.duration_s - duration) <= SAME_LENGTH_S:
            return f"Probably “{lecture.title}” ({round(lecture.duration_s / 60)} min)"
    return ""


def _needs_link(s: Session, rec: WatchedRecording) -> bool:
    return rec.status == "wanted" or (
        rec.status == "queued" and bool(_download_failed(s, rec.lecture_id))
    )


def course_due(s: Session, course: WatchedCourse, now: datetime) -> bool:
    if course.last_scan_at is None:
        return True
    since = now - _aware(course.last_scan_at)
    if since >= SCAN_EVERY:
        return True
    if since < min(SCAN_AFTER_SLOT, RETRY_WANTED):
        return False
    recs = s.exec(select(WatchedRecording).where(WatchedRecording.course_id == course.id))
    if any(_needs_link(s, r) for r in recs):
        return True
    if since < SCAN_AFTER_SLOT:
        return False
    from backend.timetable.service import events_between

    return any(
        ev["subject_id"] == course.subject_id and not ev["ignored"] and ev["end"] <= now
        for ev in events_between(s, now - AFTER_SLOT_WINDOW, now)
    )


def state(s: Session, now: datetime | None = None) -> dict[str, Any]:
    """For the extension: the courses to scan now, and per course the episodes it can skip
    (already known and not waiting for a link)."""
    global _scan_requested
    now = now or utcnow()
    requested, _scan_requested = _scan_requested, False
    out = []
    for course in s.exec(select(WatchedCourse)).all():
        recs = s.exec(select(WatchedRecording).where(WatchedRecording.course_id == course.id)).all()
        # Wanted ones and failed downloads need a (fresh) link: the extension opens those.
        known = [r.episode_id for r in recs if not _needs_link(s, r)]
        due = requested or course_due(s, course, now)
        out.append({"host": course.host, "moodle_id": course.moodle_id, "due": due, "known": known})
    return {"scan": any(c["due"] for c in out), "courses": out}


def _queue(
    s: Session, course: WatchedCourse, rec: WatchedRecording, url: str, audio_url: str
) -> Lecture:
    subject = s.get(Subject, course.subject_id)
    if subject is None:
        raise ValueError("The course's subject no longer exists")
    title, meaningful = tidy_title(rec.title)
    kind = m.group(1) if (m := _KIND.search(rec.title)) else course.kind
    return queue_download(
        s,
        subject,
        url,
        title=title,
        title_is_default=not meaningful,
        kind=kind,
        stored_url=rec.page_url,
        started_at=_started_at(s, course.subject_id, rec.recorded_on, kind),
        # The subject comes from the Moodle course: no "recorded during X's slot?" question.
        meta={"opencast": {"episode": rec.episode_id}, "slot_dismissed": True},
        audio_url=audio_url,
    )


def _download_failed(s: Session, lecture_id: int | None) -> bool | None:
    """None if the lecture is gone (deleted: never bring it back)."""
    lecture = s.get(Lecture, lecture_id) if lecture_id else None
    if lecture is None:
        return None
    return lecture.stages.get("capture") == "failed"


def report(
    s: Session, host: str, moodle_id: int, body: dict[str, Any], now: datetime | None = None
) -> dict[str, int]:
    """What the extension found in one course. Returns {"queued": n, "review": n}."""
    now = now or utcnow()
    course = s.exec(
        select(WatchedCourse).where(
            WatchedCourse.host == host, WatchedCourse.moodle_id == moodle_id
        )
    ).first()
    if course is None:
        raise LookupError("This course isn't watched")
    course.last_scan_at = now
    if error := (body.get("error") or "").strip():
        if error != course.last_error:
            bus.publish(
                "notify",
                {"title": "Moodle recordings", "message": error, "level": "warning"},
            )
        course.last_error = error
        s.add(course)
        s.commit()
        bus.publish("watch", {})
        return {"queued": 0, "review": 0}

    episodes = _episode_lectures(s)
    taken = set(episodes.values())
    queued: list[Lecture] = []
    review = 0
    for item in body.get("recordings") or []:
        eid = str(item.get("episode_id") or "").strip()
        if not eid:
            continue
        url = str(item.get("url") or "")
        audio_url = str(item.get("audio_url") or "")
        rec = s.get(WatchedRecording, eid)
        if rec is None:
            rec = WatchedRecording(
                episode_id=eid,
                course_id=course.id,  # type: ignore[arg-type]
                title=str(item.get("title") or "")[:300],
                duration_s=float(item.get("duration_s") or 0),
                recorded_on=recorded_on(item.get("title") or "", item.get("oc_title") or ""),
                page_url=str(item.get("page_url") or ""),
                found_at=now,
            )
            if eid in episodes:
                rec.status, rec.lecture_id = "have", episodes[eid]
            elif not course.initial_done:
                rec.status = "review"
                rec.have_hint = _same_length(s, course, rec.duration_s, taken)
                review += 1
            else:
                rec.status = "wanted"
            s.add(rec)
        elif rec.course_id != course.id:
            continue  # the same episode linked from another course: filed there already
        if rec.status == "queued":
            failed = _download_failed(s, rec.lecture_id)
            if failed is None:
                rec.status = "skipped"
            elif failed and url:  # the old link expired: same lecture, fresh link
                lecture = s.get(Lecture, rec.lecture_id)
                retry_download(s, lecture, url, stored_url=rec.page_url, audio_url=audio_url)  # type: ignore[arg-type]
                queued.append(lecture)  # type: ignore[arg-type]
        if rec.status == "wanted" and url:
            lecture = _queue(s, course, rec, url, audio_url)
            rec.status, rec.lecture_id = "queued", lecture.id
            queued.append(lecture)
        s.add(rec)
    course.initial_done = True
    course.last_error = ""
    s.add(course)
    s.commit()
    subject = s.get(Subject, course.subject_id)
    for lecture in queued:
        bus.publish("lecture", {"id": lecture.id, "subject_id": course.subject_id})
    if queued:
        n = len(queued)
        bus.publish(
            "notify",
            {
                "title": "New recordings from Moodle",
                "message": f"{n} new recording{'s' if n > 1 else ''} for "
                f"{subject.name if subject else 'your course'}: downloading.",
                "level": "info",
            },
        )
    if queued or review:
        bus.publish("watch", {})
    return {"queued": len(queued), "review": review}


def decide(s: Session, get: list[str], skip: list[str]) -> int:
    """The user's choice on the review list. Wanted ones get their link at the next scan. A
    downloaded one can be asked for again once its lecture is deleted or failed (cancelled)."""
    changed = 0
    for eid, status in [*((e, "wanted") for e in get), *((e, "skipped") for e in skip)]:
        rec = s.get(WatchedRecording, eid)
        if rec is None:
            continue
        if rec.status == "queued":
            lecture = s.get(Lecture, rec.lecture_id) if rec.lecture_id else None
            if lecture is not None and lecture.status != "failed":
                continue
            rec.lecture_id = None
        if rec.status in ("review", "skipped", "wanted", "queued"):
            rec.status = status
            s.add(rec)
            changed += 1
    s.commit()
    if get:
        request_scan()
    bus.publish("watch", {})
    return changed


def overview(s: Session) -> dict[str, Any]:
    subjects = {x.id: x.name for x in s.exec(select(Subject)).all()}
    courses = s.exec(select(WatchedCourse).order_by(col(WatchedCourse.name))).all()
    recs = s.exec(select(WatchedRecording).order_by(col(WatchedRecording.recorded_on))).all()
    by_course: dict[int, list[WatchedRecording]] = {}
    for r in recs:
        by_course.setdefault(r.course_id, []).append(r)
    names = {c.id: c for c in courses}
    return {
        "courses": [
            {
                "id": c.id,
                "host": c.host,
                "moodle_id": c.moodle_id,
                "name": c.name,
                "subject_id": c.subject_id,
                "subject_name": subjects.get(c.subject_id, ""),
                "kind": c.kind,
                "last_scan_at": _aware(c.last_scan_at).isoformat() if c.last_scan_at else None,
                "last_error": c.last_error,
                "counts": {
                    st: sum(r.status == st for r in by_course.get(c.id or 0, []))
                    for st in ("review", "wanted", "queued", "have", "skipped")
                },
            }
            for c in courses
        ],
        "review": [
            {
                "episode_id": r.episode_id,
                "course_id": r.course_id,
                "subject_name": subjects.get(names[r.course_id].subject_id, "")
                if r.course_id in names
                else "",
                "title": tidy_title(r.title)[0],
                "duration_s": r.duration_s,
                "recorded_on": r.recorded_on.isoformat() if r.recorded_on else None,
                "have_hint": r.have_hint,
                "status": r.status,
            }
            for r in recs
            if r.status in ("review", "wanted")
        ],
    }


def upsert_course(
    s: Session, host: str, moodle_id: int, name: str, subject_id: int, kind: str
) -> WatchedCourse:
    if s.get(Subject, subject_id) is None:
        raise LookupError("Subject not found")
    course = s.exec(
        select(WatchedCourse).where(
            WatchedCourse.host == host, WatchedCourse.moodle_id == moodle_id
        )
    ).first() or WatchedCourse(host=host, moodle_id=moodle_id, subject_id=subject_id)
    course.name = name.strip()[:200] or course.name
    course.subject_id = subject_id
    course.kind = kind
    s.add(course)
    s.commit()
    s.refresh(course)
    request_scan()
    bus.publish("watch", {})
    return course


def remove_course(s: Session, course_id: int) -> None:
    course = s.get(WatchedCourse, course_id)
    if course is None:
        raise LookupError("Course not found")
    for r in s.exec(select(WatchedRecording).where(WatchedRecording.course_id == course_id)):
        s.delete(r)
    s.flush()  # no ORM relationship: without this the course can go first (foreign key)
    s.delete(course)
    s.commit()
    bus.publish("watch", {})
