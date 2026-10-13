"""Audio recaps: a 5-10 minute spoken summary of a lecture or a week, for listening on the go.

Job ``recap``: the main model writes a spoken-style script in chapters from the lectures'
summary, notes and exam hints (``prompts/recap.md``); ``voice.tts_worker`` (child process)
reads it with Piper into one WAV with pauses between chapters; ffmpeg encodes mono AAC
``.m4a`` with chapter marks. Lecture recaps live in the lecture folder (so they move to the
trash with it), week recaps in ``<data>/recaps/``. A new recap replaces the old one of the same
lecture or week.
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlmodel import Session, col, select

from backend.ai import schemas as sc
from backend.ai.prompts import load_prompt
from backend.ai.providers import get_provider, is_configured
from backend.ai.types import LLMRequest
from backend.core import jobs
from backend.core.config import get_settings
from backend.core.db import session_scope
from backend.core.events import bus
from backend.core.jobs import JobContext, JobError, handler
from backend.core.library import abs_path, rel_path
from backend.core.media import encode_audio
from backend.core.models import Job, JobStatus, Lecture, Note, Recap, Subject, utcnow
from backend.core.util import format_hms
from backend.pipeline.generate import parse_ts, record_usage
from backend.pipeline.hints import lecture_hints
from backend.pipeline.subject_hub import lectures_with_notes
from backend.voice import tts

log = logging.getLogger(__name__)

RECAP_JOB = "recap"
WORDS_PER_MIN = 160  # Piper (lessac) speaks ~165 words per minute
CHARS_TOTAL = 60_000
CHARS_PER_LECTURE = 20_000
REPO_ROOT = Path(__file__).resolve().parents[2]
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
SYSTEM = (
    "You are a friendly, precise university tutor who turns lecture notes into spoken audio "
    "recaps. You stay faithful to the material."
)


class RecapError(Exception):
    pass


# Scope ----------------------------------------------------------------------------------------


def week_monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


def _local_day(dt: datetime) -> date:
    return dt.astimezone().date()


def lectures_in_scope(s: Session, recap: Recap) -> list[tuple[Subject, int, Lecture]]:
    """(subject, lecture number, lecture) with study notes, in time order."""
    if recap.scope == "lecture":
        lec = s.get(Lecture, recap.lecture_id)
        if lec is None:
            return []
        sub = s.get(Subject, lec.subject_id)
        n = next((n for n, x in lectures_with_notes(s, lec.subject_id) if x.id == lec.id), 1)
        return [(sub, n, lec)] if sub and _notes(s, lec.id) else []  # type: ignore[list-item]
    assert recap.week_start is not None
    end = recap.week_start + timedelta(days=7)
    subjects = s.exec(select(Subject).where(col(Subject.archived).is_(False))).all()
    out = []
    for sub in subjects:
        if recap.subject_id and sub.id != recap.subject_id:
            continue
        for n, lec in lectures_with_notes(s, sub.id):  # type: ignore[arg-type]
            if recap.week_start <= _local_day(lec.started_at) < end and _notes(s, lec.id):  # type: ignore[arg-type]
                out.append((sub, n, lec))
    out.sort(key=lambda x: x[2].started_at)
    return out


def _notes(s: Session, lecture_id: int, kind: str = "notes") -> Note | None:
    return s.exec(
        select(Note).where(Note.lecture_id == lecture_id, Note.kind == kind, col(Note.is_current))
    ).first()


def scope_title(s: Session, recap: Recap) -> str:
    if recap.scope == "lecture":
        lec = s.get(Lecture, recap.lecture_id)
        return lec.title if lec else "Lecture"
    assert recap.week_start is not None
    sub = s.get(Subject, recap.subject_id) if recap.subject_id else None
    label = f"Week of {recap.week_start.strftime('%d %b')}"
    return f"{label}: {sub.name}" if sub else label


def material(s: Session, recap: Recap) -> tuple[str, dict[int, Lecture], list[str]]:
    """Context text, [#k] -> lecture, and the exam-hint lines."""
    items = lectures_in_scope(s, recap)
    if not items:
        raise RecapError("No lecture with study notes in this scope yet")
    per = min(CHARS_PER_LECTURE, CHARS_TOTAL // len(items))
    parts, keys, hints = [], {}, []
    for k, (sub, n, lec) in enumerate(items, start=1):
        keys[k] = lec
        summary = _notes(s, lec.id, "summary")  # type: ignore[arg-type]
        notes = _notes(s, lec.id)  # type: ignore[arg-type]
        body = (summary.content_md + "\n\n" if summary else "") + (
            notes.content_md if notes else ""
        )
        parts.append(f'## [#{k}] {sub.name}, L{n} "{lec.title}"\n\n{body[:per]}')
        for h in lecture_hints(s, lec.id):  # type: ignore[arg-type]
            hints.append(f"- [#{k} {format_hms(h.start_s)}] {h.kind}: {h.topic or h.quote}")
    return "\n\n".join(parts), keys, hints


# Queue ----------------------------------------------------------------------------------------


def create(s: Session, scope: str, minutes: int, lecture_id: int | None = None,
           subject_id: int | None = None, week_start: date | None = None) -> Recap:  # fmt: skip
    if not is_configured():
        raise RecapError("No AI provider is set up (Settings → AI provider)")
    if not tts.ready():
        raise RecapError("Download a voice first (Settings → Voice)")
    if scope == "lecture":
        lec = s.get(Lecture, lecture_id)
        if lec is None:
            raise RecapError("Lecture not found")
        subject_id, week_start = lec.subject_id, None
    elif week_start is None:
        raise RecapError("Pick a week")
    else:
        week_start = week_monday(week_start)
    recap = Recap(
        scope=scope,
        lecture_id=lecture_id if scope == "lecture" else None,
        subject_id=subject_id,
        week_start=week_start,
        minutes=minutes,
        voice=get_settings().voice.voice,
    )
    if not lectures_in_scope(s, recap):
        raise RecapError("No lecture with study notes in this scope yet")
    recap.title = scope_title(s, recap)
    for old in _same_scope(s, recap):
        delete(s, old)
    s.add(recap)
    s.commit()
    s.refresh(recap)
    jobs.enqueue(s, RECAP_JOB, lecture_id=recap.lecture_id, payload={"recap_id": recap.id})
    bus.publish("recap", {"id": recap.id})
    return recap


def _same_scope(s: Session, recap: Recap) -> list[Recap]:
    q = select(Recap).where(Recap.scope == recap.scope)
    if recap.scope == "lecture":
        q = q.where(Recap.lecture_id == recap.lecture_id)
    else:
        q = q.where(Recap.week_start == recap.week_start, Recap.subject_id == recap.subject_id)
        if recap.subject_id is None:
            q = q.where(col(Recap.subject_id).is_(None))
    return list(s.exec(q).all())


def delete(s: Session, recap: Recap) -> None:
    for job in s.exec(select(Job).where(Job.kind == RECAP_JOB, Job.status == JobStatus.queued)):
        if job.payload.get("recap_id") == recap.id:
            job.status = JobStatus.cancelled
            s.add(job)
    if recap.audio_path:
        abs_path(recap.audio_path).unlink(missing_ok=True)
    s.delete(recap)
    s.commit()


def maybe_auto_queue(s: Session, lecture_id: int) -> bool:
    """After a lecture's study material is generated (Settings: recap every new lecture)."""
    if not get_settings().recap.auto_lecture or not tts.ready():
        return False
    if s.exec(select(Recap.id).where(Recap.lecture_id == lecture_id)).first() is not None:
        return False
    try:
        create(s, "lecture", get_settings().recap.minutes, lecture_id=lecture_id)
    except RecapError as e:
        log.info("No automatic recap for lecture %s: %s", lecture_id, e)
        return False
    return True


# The job --------------------------------------------------------------------------------------


def _set(recap_id: int, **fields: Any) -> None:
    with session_scope() as s:
        r = s.get(Recap, recap_id)
        if r is None:
            return
        for k, v in fields.items():
            setattr(r, k, v)
        r.updated_at = utcnow()
        s.add(r)
        s.commit()
    bus.publish("recap", {"id": recap_id, **{k: v for k, v in fields.items() if k == "status"}})


@handler(RECAP_JOB)
def build_recap(ctx: JobContext) -> None:
    recap_id = ctx.payload["recap_id"]
    try:
        _build(ctx, recap_id)
    except Exception as e:
        _set(recap_id, status="failed", error=str(e)[:500])
        raise


def _build(ctx: JobContext, recap_id: int) -> None:
    settings = get_settings()
    with session_scope() as s:
        recap = s.get(Recap, recap_id)
        if recap is None:
            return  # deleted meanwhile
        text, keys, hints = material(s, recap)
        scope = (
            f'the lecture "{recap.title}"'
            if recap.scope == "lecture"
            else f"the lectures of {recap.title[0].lower() + recap.title[1:]}"
        )
        lecture_id, subject_id, minutes = recap.lecture_id, recap.subject_id, recap.minutes
        out_path = _audio_path(s, recap)
    _set(recap_id, status="writing", error="")
    ctx.progress(0.05, "Writing the script")
    closing = (
        "A short wrap-up: the three things to remember."
        if len(keys) == 1
        else "A short wrap-up that connects the lectures: the three things to remember."
    )
    result = get_provider().complete(
        LLMRequest(
            task="recap",
            system=SYSTEM,
            context=f"# Lecture notes\n\n{text}",
            instruction=load_prompt("recap").render(
                scope=scope,
                minutes=minutes,
                words=minutes * WORDS_PER_MIN,
                closing=closing,
                hints=(
                    "Exam hints (moments the lecturer flagged):\n" + "\n".join(hints)
                    if hints
                    else ""
                ),
            ),
            output=sc.RecapOut,
            max_tokens=12_000,
        )
    )
    first_subject = subject_id or keys[min(keys)].subject_id
    cost = record_usage(lecture_id, first_subject, "recap", result)
    out: sc.RecapOut = result.parsed
    sections = [x for x in out.sections if x.text.strip()]
    if not sections:
        raise JobError("The script came back empty")
    script = [{"title": x.title, "text": x.text, "refs": _refs(x.refs, keys)} for x in sections]
    _set(recap_id, status="speaking", script=script, title=out.title or "", cost_usd=cost)

    ctx.progress(0.3, "Reading it aloud")
    wav = out_path.with_suffix(".wav")
    starts, duration = _speak([x["text"] for x in script], wav, ctx)
    ctx.progress(0.92, "Encoding")
    try:
        meta = out_path.with_suffix(".chapters.txt")
        meta.write_text(_ffmetadata(out.title, script, starts, duration), encoding="utf-8")
        encode_audio(
            ["-i", str(wav), "-i", str(meta), "-map", "0:a", "-map_metadata", "1",
             "-map_chapters", "1"],
            out_path,
            duration,
        )  # fmt: skip
    finally:
        wav.unlink(missing_ok=True)
        out_path.with_suffix(".chapters.txt").unlink(missing_ok=True)
    chapters = [
        {"title": x["title"], "start_s": round(t, 2), "refs": x["refs"]}
        for x, t in zip(script, starts, strict=True)
    ]
    _set(
        recap_id,
        status="ready",
        chapters=chapters,
        audio_path=rel_path(out_path),
        duration_s=round(duration, 1),
        voice=settings.voice.voice,
    )
    bus.publish(
        "notify",
        {"title": "Audio recap ready", "message": out.title or "Recap", "level": "success"},
    )


def _audio_path(s: Session, recap: Recap) -> Path:
    if recap.scope == "lecture":
        lec = s.get(Lecture, recap.lecture_id)
        assert lec is not None
        path = abs_path(lec.folder) / "recap.m4a"
    else:
        assert recap.week_start is not None
        sub = s.get(Subject, recap.subject_id) if recap.subject_id else None
        name = f"{recap.week_start.isoformat()}_{sub.slug if sub else 'all'}.m4a"
        path = get_settings().data_dir / "recaps" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _refs(refs: list[sc.LectureRef], keys: dict[int, Lecture]) -> list[dict[str, Any]]:
    out = []
    for r in refs:
        lec = keys.get(r.lecture)
        t = parse_ts(r.at)
        if lec is not None and t is not None:
            out.append({"lecture_id": lec.id, "start_s": t, "title": lec.title, "at": r.at})
    return out


def _speak(sections: list[str], wav: Path, ctx: JobContext) -> tuple[list[float], float]:
    cfg = get_settings().voice
    model, config = tts.voice_files(cfg.voice)
    args = {
        "engine": cfg.tts_engine,
        "model": str(model),
        "config": str(config),
        "rate": cfg.rate,
        "out": str(wav),
        "pause_s": 0.9,
        "sections": sections,
    }
    proc = subprocess.Popen(
        [sys.executable, "-m", "backend.voice.tts_worker"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=REPO_ROOT,
        creationflags=CREATE_NO_WINDOW,
    )
    assert proc.stdin and proc.stdout and proc.stderr
    jobs.track_child(proc)
    try:
        proc.stdin.write(json.dumps(args))
        proc.stdin.close()
        done: dict[str, Any] | None = None
        error = ""
        for line in proc.stdout:
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if msg.get("type") == "progress":
                ctx.progress(0.3 + 0.6 * float(msg["fraction"]), "Reading it aloud")
            elif msg.get("type") == "error":
                error = msg.get("message", "")
            elif msg.get("type") == "done":
                done = msg
        stderr = proc.stderr.read()
        code = proc.wait()
    finally:
        jobs.untrack_child(proc)
    if code != 0 or done is None:
        raise JobError(f"Text-to-speech failed: {error or stderr.strip()[-300:] or code}")
    return [float(x) for x in done["starts"]], float(done["duration"])


def _ffmetadata(title: str, script: list[dict], starts: list[float], duration: float) -> str:
    def esc(x: str) -> str:
        for ch in "\\=;#\n":
            x = x.replace(ch, "\\" + ch)
        return x

    lines = [";FFMETADATA1", f"title={esc(title or 'LectureLab recap')}", "artist=LectureLab"]
    ends = [*starts[1:], duration]
    for x, a, b in zip(script, starts, ends, strict=True):
        lines += [
            "[CHAPTER]",
            "TIMEBASE=1/1000",
            f"START={int(a * 1000)}",
            f"END={int(b * 1000)}",
            f"title={esc(x['title'])}",
        ]
    return "\n".join(lines) + "\n"


def recap_dict(r: Recap) -> dict[str, Any]:
    return {
        "id": r.id,
        "scope": r.scope,
        "subject_id": r.subject_id,
        "lecture_id": r.lecture_id,
        "week_start": r.week_start.isoformat() if r.week_start else None,
        "title": r.title,
        "minutes": r.minutes,
        "status": r.status,
        "error": r.error,
        "script": r.script,
        "chapters": r.chapters,
        "duration_s": r.duration_s,
        "voice": r.voice,
        "cost_usd": round(r.cost_usd or 0, 4),
        "has_audio": bool(r.audio_path),
        "created_at": r.created_at,
        "updated_at": r.updated_at,
    }


def weeks(s: Session, count: int = 12) -> list[dict[str, Any]]:
    """Recent weeks that have lectures with notes (for the week picker)."""
    by_week: dict[date, set[str]] = {}
    for sub in s.exec(select(Subject).where(col(Subject.archived).is_(False))).all():
        for _, lec in lectures_with_notes(s, sub.id):  # type: ignore[arg-type]
            if _notes(s, lec.id) is None:  # type: ignore[arg-type]
                continue
            by_week.setdefault(week_monday(_local_day(lec.started_at)), set()).add(sub.name)
    return [
        {"week_start": w.isoformat(), "subjects": sorted(names)}
        for w, names in sorted(by_week.items(), reverse=True)[:count]
    ]
