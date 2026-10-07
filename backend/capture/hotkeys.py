"""Global hotkeys (pynput). Callbacks are handed to a worker thread immediately: Windows drops
low-level keyboard hooks whose callbacks take too long, and starting a recording can take a
moment.

We use a plain ``Listener`` + ``HotKey`` rather than ``GlobalHotKeys`` because the latter ignores
injected key events, which would break macro tools (AutoHotkey, Stream Deck, remapped mouse
buttons) and makes the hotkeys impossible to test end to end."""

from __future__ import annotations

import logging
import queue
import threading
from collections.abc import Callable

from backend.capture.recorder import RecorderError, RecorderService, notify
from backend.core.config import HotkeyConfig

log = logging.getLogger(__name__)


class Hotkeys:
    def __init__(self, config: HotkeyConfig, recorder: RecorderService) -> None:
        self.config = config
        self.recorder = recorder
        self._queue: queue.Queue[Callable[[], None] | None] = queue.Queue()
        self._listener = None
        self._worker = threading.Thread(target=self._run, name="hotkey-actions", daemon=True)

    def start(self) -> None:
        from pynput import keyboard

        mapping = {
            self.config.start_stop: lambda: self._queue.put(self._toggle),
            self.config.important: lambda: self._queue.put(lambda: self._bookmark("important")),
            self.config.confused: lambda: self._queue.put(lambda: self._bookmark("confused")),
        }
        try:
            hotkeys = [
                keyboard.HotKey(keyboard.HotKey.parse(combo), fn) for combo, fn in mapping.items()
            ]
        except ValueError as e:
            log.error("Invalid hotkey in config: %s", e)
            notify(f"Invalid hotkey in config.toml: {e}", level="error")
            return

        def on_press(key, injected=False):  # type: ignore[no-untyped-def]
            k = listener.canonical(key)
            for hk in hotkeys:
                hk.press(k)

        def on_release(key, injected=False):  # type: ignore[no-untyped-def]
            k = listener.canonical(key)
            for hk in hotkeys:
                hk.release(k)

        listener = keyboard.Listener(on_press=on_press, on_release=on_release)
        self._listener = listener
        self._worker.start()
        listener.start()
        log.info("Hotkeys: %s", ", ".join(mapping))

    def stop(self) -> None:
        if self._listener is not None:
            self._listener.stop()
        self._queue.put(None)

    def _run(self) -> None:
        while (action := self._queue.get()) is not None:
            try:
                action()
            except RecorderError as e:
                notify(str(e), level="warning")
            except Exception:
                log.exception("Hotkey action failed")

    def _toggle(self) -> None:
        self.recorder.toggle()

    def _bookmark(self, kind: str) -> None:
        if not self.recorder.recording:
            notify("Not recording, so no bookmark was added.", level="warning")
            return
        self.recorder.bookmark(kind)
