"""Keep Windows from going to sleep while LectureLab records or processes.

``SetThreadExecutionState`` stops Windows from sleeping after a while without input (the 4
minutes on battery): a recording must not get a gap, a queue of lectures should finish while
you are away. While recording the screen stays on too, since slide capture grabs the Zoom
window. Closing the lid or choosing Sleep still sleeps: Windows lets no program prevent that.

The request belongs to the thread that made it, so one thread owns it and re-checks every few
seconds; when nothing is going on it hands Windows its normal sleep rules back.
"""

from __future__ import annotations

import logging
import os
import threading
from collections.abc import Callable

from sqlmodel import col, select

from backend.core.db import session_scope
from backend.core.models import Job, JobStatus

log = logging.getLogger(__name__)

ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001
ES_DISPLAY_REQUIRED = 0x00000002


def flags(recording: bool, busy: bool) -> int:
    if recording:
        return ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED
    if busy:
        return ES_CONTINUOUS | ES_SYSTEM_REQUIRED  # the screen may turn off
    return ES_CONTINUOUS  # normal sleep rules


def jobs_pending() -> bool:
    with session_scope() as s:
        return (
            s.exec(
                select(Job.id).where(col(Job.status).in_([JobStatus.queued, JobStatus.running]))
            ).first()
            is not None
        )


def _windows_set_state(value: int) -> None:
    import ctypes

    ctypes.windll.kernel32.SetThreadExecutionState(value)  # type: ignore[attr-defined]


class KeepAwake:
    def __init__(
        self,
        is_recording: Callable[[], bool],
        check_s: float = 15.0,
        set_state: Callable[[int], None] | None = None,
        is_busy: Callable[[], bool] = jobs_pending,
    ) -> None:
        self.is_recording = is_recording
        self.is_busy = is_busy
        self.check_s = check_s
        self.set_state = set_state or (_windows_set_state if os.name == "nt" else lambda v: None)
        self.state = ES_CONTINUOUS
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="keep-awake", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=5)

    def check(self) -> None:
        """One look: change the request only when the situation changed."""
        try:
            recording = self.is_recording()
            want = flags(recording, recording or self.is_busy())
        except Exception:  # never let a database hiccup end the thread
            log.debug("Keep-awake check failed", exc_info=True)
            return
        if want != self.state:
            self.set_state(want)
            self.state = want
            log.info(
                "Keeping the PC awake: %s",
                "recording"
                if want & ES_DISPLAY_REQUIRED
                else "processing"
                if want & ES_SYSTEM_REQUIRED
                else "no (normal sleep rules again)",
            )

    def _run(self) -> None:
        while not self._stop.is_set():
            self.check()
            self._stop.wait(self.check_s)
        if self.state != ES_CONTINUOUS:
            self.set_state(ES_CONTINUOUS)
