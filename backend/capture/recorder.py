"""The recorder: one lecture at a time, controlled from the UI, tray or global hotkeys.

Threads: one capture thread per source (see ``sources``) and one mixer loop that writes
wall-clock-aligned PCM to disk, fsyncs every few seconds and publishes level meters. On stop the
PCM is encoded to AAC by the ``encode_recording`` job. If the app dies mid-recording,
``recover_interrupted`` turns the leftover PCM into a normal lecture on the next start.
"""

from __future__ import annotations

import logging
import shutil
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from sqlmodel import col, select

from backend.capture.mixer import Mixer
from backend.capture.session import PCM_NAME, PcmWriter, SessionInfo, repair_pcm
from backend.capture.sources import CaptureSource, SoundcardSource
from backend.core import jobs
from backend.core.config import Settings
from backend.core.db import session_scope
from backend.core.events import bus
from backend.core.library import create_lecture, lecture_dir
from backend.core.models import (
    Bookmark,
    BookmarkKind,
    Lecture,
    LectureSource,
    LectureStatus,
    Subject,
)
from backend.core.state import load_state, update_state

log = logging.getLogger(__name__)

ENCODE_JOB = "encode_recording"
LOOP_INTERVAL_S = 0.1
DISK_CHECK_S = 30.0

SourceFactory = Callable[[Settings, bool, Callable[[str, str], None]], list[CaptureSource]]


class RecorderError(Exception):
    """User-facing error (shown in the UI / as a tray notification)."""


def default_sources(
    settings: Settings, mic: bool, on_device: Callable[[str, str], None]
) -> list[CaptureSource]:
    rc = settings.recorder
    sources: list[CaptureSource] = [
        SoundcardSource("loopback", rc.sample_rate, rc.device_poll_s, on_device)
    ]
    if mic:
        sources.append(SoundcardSource("mic", rc.sample_rate, rc.device_poll_s, on_device))
    return sources


def notify(message: str, title: str = "LectureLab", level: str = "info") -> None:
    bus.publish("notify", {"title": title, "message": message, "level": level})


@dataclass
class _Active:
    lecture_id: int
    subject_id: int
    subject_name: str
    title: str
    mic: bool
    folder: Any
    t0: float
    session: SessionInfo
    writer: PcmWriter
    mixer: Mixer
    sources: list[CaptureSource]
    devices: dict[str, str] = field(default_factory=dict)
    stop_event: threading.Event = field(default_factory=threading.Event)
    thread: threading.Thread | None = None
    disk_warned: bool = False
    stop_reason: str | None = None


class RecorderService:
    def __init__(self, settings: Settings, source_factory: SourceFactory = default_sources) -> None:
        self.settings = settings
        self.source_factory = source_factory
        self._lock = threading.RLock()
        self._active: _Active | None = None
        self._last_levels: dict[str, Any] = {}

    # Public API --------------------------------------------------------------------------
    @property
    def recording(self) -> bool:
        return self._active is not None

    def status(self) -> dict[str, Any]:
        a = self._active
        free_gb = shutil.disk_usage(self.settings.data_dir).free / 1e9
        if a is None:
            return {
                "recording": False,
                "last_subject_id": load_state().get("last_subject_id"),
                "free_gb": round(free_gb, 1),
            }
        return {
            "recording": True,
            "lecture_id": a.lecture_id,
            "subject_id": a.subject_id,
            "subject_name": a.subject_name,
            "title": a.title,
            "mic": a.mic,
            "elapsed_s": round(time.monotonic() - a.t0, 1),
            "devices": a.devices,
            "bookmarks": a.session.bookmarks,
            "free_gb": round(free_gb, 1),
        }

    def start(
        self,
        subject_id: int | None = None,
        *,
        mic: bool | None = None,
        title: str | None = None,
        kind: str = "",
    ) -> dict[str, Any]:
        with self._lock:
            if self._active is not None:
                raise RecorderError("Already recording")
            rc = self.settings.recorder
            mic = rc.mic_default if mic is None else mic
            self._check_disk_before_start()
            with session_scope() as s:
                subject = self._pick_subject(s, subject_id)
                lecture = create_lecture(
                    s,
                    subject,
                    source=LectureSource.recording,
                    status=LectureStatus.recording,
                    title=title,
                    kind=kind,
                )
                first_time = not subject.recording_notice_ack
                if first_time:
                    subject.recording_notice_ack = True
                    s.add(subject)
                    s.commit()
                subject_name = subject.name
                folder = lecture_dir(lecture)
                info = SessionInfo(
                    lecture_id=lecture.id,  # type: ignore[arg-type]
                    sample_rate=rc.sample_rate,
                    started_at=lecture.started_at.isoformat(),
                    mic=mic,
                )
                info.save(folder)
                lecture_id, lecture_title = lecture.id, lecture.title

            devices: dict[str, str] = {}

            def on_device(source: str, device: str) -> None:
                devices[source] = device
                if self._active is not None:
                    elapsed = round(time.monotonic() - self._active.t0, 1)
                    self._active.session.devices.append(
                        {"source": source, "device": device, "t_s": elapsed}
                    )
                    bus.publish("recorder.device", {"source": source, "device": device})

            sources = self.source_factory(self.settings, mic, on_device)
            mixer = Mixer(
                {src.name: src.buffer for src in sources},
                rc.sample_rate,
                gains={"system": rc.system_gain, "mic": rc.mic_gain},
                latency_margin_s=rc.latency_margin_s,
                max_buffer_s=rc.max_buffer_s,
            )
            active = _Active(
                lecture_id=lecture_id,  # type: ignore[arg-type]
                subject_id=subject.id,  # type: ignore[arg-type]
                subject_name=subject_name,
                title=lecture_title,
                mic=mic,
                folder=folder,
                t0=time.monotonic(),
                session=info,
                writer=PcmWriter(folder),
                mixer=mixer,
                sources=sources,
                devices=devices,
            )
            self._active = active
            for src in sources:
                src.start()
            active.thread = threading.Thread(target=self._loop, args=(active,), name="recorder")
            active.thread.start()
            update_state(last_subject_id=active.subject_id)

        log.info("Recording started: lecture %s (%s)", lecture_id, subject_name)
        self._publish_state()
        bus.publish("lecture", {"id": lecture_id, "subject_id": active.subject_id})
        msg = f"Recording {subject_name}"
        if first_time:
            msg += (
                ". Reminder: recording may need the lecturer's OK, and classmates may be audible."
                " Recordings stay on this computer."
            )
        notify(msg, title="● Recording started")
        return self.status()

    def stop(self, reason: str | None = None) -> dict[str, Any] | None:
        with self._lock:
            a = self._active
            if a is None:
                return None
            a.stop_reason = reason
            a.stop_event.set()
            if a.thread and a.thread is not threading.current_thread():
                a.thread.join(timeout=10)
            for src in a.sources:
                src.stop()
            self._finalize(a)
            self._active = None
        self._publish_state()
        duration = a.writer.samples / a.mixer.sample_rate
        mins, secs = divmod(int(duration), 60)
        notify(
            f"Saved {mins}:{secs:02d} of {a.subject_name}. Encoding…"
            + (f" ({reason})" if reason else ""),
            title="■ Recording stopped",
            level="warning" if reason else "info",
        )
        return {"lecture_id": a.lecture_id, "duration_s": duration}

    def toggle(self, subject_id: int | None = None) -> None:
        if self.recording:
            self.stop()
        else:
            self.start(subject_id)

    def bookmark(self, kind: BookmarkKind | str, note: str = "") -> dict[str, Any]:
        kind = BookmarkKind(kind)
        with self._lock:
            a = self._active
            if a is None:
                raise RecorderError("Not recording")
            t_s = round(time.monotonic() - a.t0, 2)
            with session_scope() as s:
                bm = Bookmark(lecture_id=a.lecture_id, t_s=t_s, kind=kind, note=note)
                s.add(bm)
                s.commit()
                s.refresh(bm)
                data = {"id": bm.id, "lecture_id": a.lecture_id, "t_s": t_s, "kind": kind.value}
            a.session.bookmarks.append({"t_s": t_s, "kind": kind.value, "note": note})
            a.session.save(a.folder)
        bus.publish("recorder.bookmark", data)
        mins, secs = divmod(int(t_s), 60)
        label = "★ Important" if kind == BookmarkKind.important else f"? {kind.value.title()}"
        notify(f"{label} at {mins}:{secs:02d}", title="Bookmark")
        return data

    def shutdown(self) -> None:
        if self.recording:
            self.stop(reason="app closed")

    # Internals ---------------------------------------------------------------------------
    def _pick_subject(self, s, subject_id: int | None) -> Subject:  # type: ignore[no-untyped-def]
        if subject_id is None:
            subject_id = load_state().get("last_subject_id")
        subject = s.get(Subject, subject_id) if subject_id is not None else None
        if subject is None:
            subject = s.exec(select(Subject).order_by(col(Subject.name))).first()
        if subject is None:
            raise RecorderError("Create a subject first")
        return subject

    def _check_disk_before_start(self) -> None:
        free_gb = shutil.disk_usage(self.settings.data_dir).free / 1e9
        if free_gb < self.settings.recorder.min_free_gb:
            raise RecorderError(
                f"Only {free_gb:.1f} GB free on the data drive; need at least "
                f"{self.settings.recorder.min_free_gb:g} GB to record safely."
            )

    def _loop(self, a: _Active) -> None:
        rc = self.settings.recorder
        next_sync = time.monotonic() + rc.fsync_interval_s
        next_disk = time.monotonic() + DISK_CHECK_S
        try:
            while not a.stop_event.wait(LOOP_INTERVAL_S):
                now = time.monotonic()
                self._write_step(a, now - a.t0, final=False)
                if now >= next_sync:
                    next_sync = now + rc.fsync_interval_s
                    a.writer.sync()
                    a.session.samples_written = a.writer.samples
                    a.session.save(a.folder)
                if now >= next_disk:
                    next_disk = now + DISK_CHECK_S
                    self._check_disk_during(a)
        except Exception:
            log.exception("Recorder loop crashed; stopping")
            threading.Thread(target=self.stop, args=("recorder error",), daemon=True).start()
        finally:
            # Final flush up to "now" so the file ends where the user pressed stop.
            try:
                self._write_step(a, time.monotonic() - a.t0, final=True)
            except Exception:
                log.exception("Final recorder flush failed")

    def _write_step(self, a: _Active, elapsed: float, final: bool) -> None:
        step = a.mixer.step(elapsed, final=final)
        if step is None:
            return
        a.writer.write(step.pcm)
        levels = {
            name: {
                "rms_db": round(lv.rms_db, 1),
                "peak_db": round(lv.peak_db, 1),
                "active": lv.active,
            }
            for name, lv in step.levels.items()
        }
        bus.publish("recorder.level", {"elapsed_s": round(elapsed, 1), "levels": levels})

    def _check_disk_during(self, a: _Active) -> None:
        rc = self.settings.recorder
        free_gb = shutil.disk_usage(a.folder).free / 1e9
        if free_gb < rc.stop_free_gb:
            log.error("Disk almost full (%.2f GB); stopping recording", free_gb)
            a.stop_event.set()
            threading.Thread(target=self.stop, args=("disk almost full",), daemon=True).start()
        elif free_gb < rc.warn_free_gb and not a.disk_warned:
            a.disk_warned = True
            notify(f"Only {free_gb:.1f} GB free on the data drive.", level="warning")

    def _finalize(self, a: _Active) -> None:
        a.writer.close()
        a.session.samples_written = a.writer.samples
        a.session.stopped = True
        a.session.save(a.folder)
        duration = a.writer.samples / a.mixer.sample_rate
        log.info(
            "Recording stopped: lecture %s, %.1f s (silence padded: %s)",
            a.lecture_id,
            duration,
            {k: round(v / a.mixer.sample_rate, 1) for k, v in a.mixer.total_padded.items()},
        )
        with session_scope() as s:
            lecture = s.get(Lecture, a.lecture_id)
            if lecture is not None:
                lecture.duration_s = duration
                lecture.status = LectureStatus.processing
                lecture.stages = {**lecture.stages, "capture": "running"}
                s.add(lecture)
                s.commit()
            jobs.enqueue(s, ENCODE_JOB, lecture_id=a.lecture_id)
        bus.publish("lecture", {"id": a.lecture_id, "subject_id": a.subject_id})

    def _publish_state(self) -> None:
        bus.publish("recorder.state", self.status())


def recover_interrupted() -> int:
    """Re-queue encoding for recordings that were cut off by a crash or power loss."""
    recovered = 0
    with session_scope() as s:
        lectures = s.exec(select(Lecture).where(Lecture.status == LectureStatus.recording)).all()
        for lecture in lectures:
            folder = lecture_dir(lecture)
            pcm = folder / PCM_NAME
            samples = repair_pcm(pcm) if pcm.exists() else 0
            info = SessionInfo.load(folder)
            if samples == 0 or info is None:
                lecture.status = LectureStatus.failed
                lecture.stages = {**lecture.stages, "capture": "failed"}
                lecture.error = "Recording was interrupted before any audio was saved."
                s.add(lecture)
                continue
            info.samples_written = samples
            info.stopped = True
            info.save(folder)
            lecture.duration_s = samples / info.sample_rate
            lecture.status = LectureStatus.processing
            lecture.error = None
            lecture.stages = {**lecture.stages, "capture": "running"}
            s.add(lecture)
            s.commit()
            jobs.enqueue(s, ENCODE_JOB, lecture_id=lecture.id, payload={"recovered": True})
            log.warning(
                "Recovered interrupted recording: lecture %s (%.0f s)",
                lecture.id,
                lecture.duration_s,
            )
            recovered += 1
        s.commit()
    return recovered


_service: RecorderService | None = None


def get_recorder() -> RecorderService:
    if _service is None:
        raise RuntimeError("Recorder not initialised")
    return _service


def set_recorder(service: RecorderService | None) -> None:
    global _service
    _service = service
