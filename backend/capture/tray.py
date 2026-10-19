"""System tray icon (pystray): record/stop, bookmarks, open the UI, quit. Shows notifications."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Iterator

from PIL import Image, ImageDraw
from sqlmodel import col, select

from backend.capture.recorder import RecorderError, RecorderService
from backend.core.db import session_scope
from backend.core.events import Event, bus
from backend.core.models import Subject

log = logging.getLogger(__name__)


def _icon_image(recording: bool) -> Image.Image:
    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    if recording:
        d.ellipse((4, 4, 60, 60), fill=(220, 38, 38, 255))
        d.ellipse((22, 22, 42, 42), fill=(255, 255, 255, 255))
    else:
        d.rounded_rectangle((4, 4, 60, 60), radius=14, fill=(79, 70, 229, 255))
        # a small "L"
        d.rectangle((22, 16, 29, 46), fill=(255, 255, 255, 255))
        d.rectangle((22, 40, 44, 47), fill=(255, 255, 255, 255))
    return img


def _fmt(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


class Tray:
    def __init__(self, recorder: RecorderService, url: str, on_quit: Callable[[], None]) -> None:
        self.recorder = recorder
        self.url = url
        self.on_quit = on_quit
        self._icon = None
        self._subjects: list[tuple[int, str]] = []
        self._stop = threading.Event()

    def start(self) -> None:
        import pystray

        self._refresh_subjects()
        item = pystray.MenuItem
        menu = pystray.Menu(
            item(lambda _: self._headline(), None, enabled=False),
            pystray.Menu.SEPARATOR,
            item("Open LectureLab", self._open, default=True),
            item(
                "Start recording",
                pystray.Menu(self._subject_items),
                visible=lambda _: not self.recorder.recording,
            ),
            item("Stop recording", self._stop_recording, visible=lambda _: self.recorder.recording),
            item(
                "★ Mark important",
                lambda: self._bookmark("important"),
                visible=lambda _: self.recorder.recording,
            ),
            item(
                "? Mark confused",
                lambda: self._bookmark("confused"),
                visible=lambda _: self.recorder.recording,
            ),
            pystray.Menu.SEPARATOR,
            item("Quit", self._quit),
        )
        self._icon = pystray.Icon("LectureLab", _icon_image(False), "LectureLab", menu)
        bus.add_listener(self._on_event)
        threading.Thread(target=self._icon.run, name="tray", daemon=True).start()
        threading.Thread(target=self._tick, name="tray-tick", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()
        bus.remove_listener(self._on_event)
        if self._icon is not None:
            self._icon.stop()

    # Menu ----------------------------------------------------------------------------------
    def _headline(self) -> str:
        st = self.recorder.status()
        if st["recording"]:
            return f"● Recording {st['subject_name']} · {_fmt(st['elapsed_s'])}"
        return "LectureLab · not recording"

    def _subject_items(self) -> Iterator[object]:
        import pystray

        last = self.recorder.status().get("last_subject_id")
        for sid, name in self._subjects:
            yield pystray.MenuItem(
                name, self._start_for(sid), checked=lambda _, sid=sid: sid == last, radio=True
            )
        if not self._subjects:
            yield pystray.MenuItem("Create a subject in the app first", None, enabled=False)

    def _start_for(self, subject_id: int) -> Callable[[], None]:
        def action() -> None:
            self._run_safely(lambda: self.recorder.start(subject_id))

        return action

    def _stop_recording(self) -> None:
        self._run_safely(self.recorder.stop)

    def _bookmark(self, kind: str) -> None:
        self._run_safely(lambda: self.recorder.bookmark(kind))

    def _open(self) -> None:
        from backend.core.window import open_ui

        open_ui(self.url)

    def _quit(self) -> None:
        self._run_safely(self.recorder.shutdown)
        self.stop()
        self.on_quit()

    def _run_safely(self, fn: Callable[[], object]) -> None:
        def run() -> None:
            try:
                fn()
            except RecorderError as e:
                self.notify(str(e))
            except Exception:
                log.exception("Tray action failed")

        threading.Thread(target=run, daemon=True).start()

    # Updates -------------------------------------------------------------------------------
    def notify(self, message: str, title: str = "LectureLab") -> None:
        if self._icon is not None and getattr(self._icon, "HAS_NOTIFICATION", False):
            try:
                self._icon.notify(message, title)
            except Exception:
                log.debug("Tray notification failed", exc_info=True)

    def _refresh_subjects(self) -> None:
        try:
            with session_scope() as s:
                rows = s.exec(select(Subject).order_by(col(Subject.name))).all()
                self._subjects = [(r.id, r.name) for r in rows if not r.archived]  # type: ignore[misc]
        except Exception:
            log.exception("Could not load subjects for tray")

    def _on_event(self, evt: Event) -> None:
        if self._icon is None:
            return
        if evt.type == "recorder.state":
            self._icon.icon = _icon_image(bool(evt.data.get("recording")))
            self._icon.title = self._headline()
            self._icon.update_menu()
        elif evt.type == "subjects":
            self._refresh_subjects()
            self._icon.update_menu()
        elif evt.type == "notify":
            self.notify(evt.data.get("message", ""), evt.data.get("title", "LectureLab"))

    def _tick(self) -> None:
        while not self._stop.wait(5):
            if self._icon is not None and self.recorder.recording:
                self._icon.title = self._headline()
