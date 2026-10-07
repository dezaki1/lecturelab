"""In-process event bus, fanned out to browsers over SSE.

``publish`` is thread-safe: the recorder, job worker and tray threads all call it. Each SSE
subscriber gets its own bounded asyncio queue; slow consumers drop events rather than block.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import threading
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)


@dataclass(slots=True)
class Event:
    type: str
    data: dict[str, Any]
    ts: float = field(default_factory=time.time)


class EventBus:
    def __init__(self, queue_size: int = 256) -> None:
        self._subs: set[tuple[asyncio.AbstractEventLoop, asyncio.Queue[Event]]] = set()
        self._lock = threading.Lock()
        self._queue_size = queue_size
        self._listeners: list[Callable[[Event], None]] = []

    def add_listener(self, fn: Callable[[Event], None]) -> None:
        """Synchronous listener, called on the publishing thread (keep it fast)."""
        with self._lock:
            self._listeners.append(fn)

    def remove_listener(self, fn: Callable[[Event], None]) -> None:
        with self._lock:
            if fn in self._listeners:
                self._listeners.remove(fn)

    def publish(self, type: str, data: dict[str, Any] | None = None) -> None:
        evt = Event(type, data or {})
        with self._lock:
            subs = list(self._subs)
            listeners = list(self._listeners)
        for fn in listeners:
            try:
                fn(evt)
            except Exception:
                log.exception("Event listener failed for %s", type)
        for loop, queue in subs:
            try:
                loop.call_soon_threadsafe(_offer, queue, evt)
            except RuntimeError:  # loop closed
                with self._lock:
                    self._subs.discard((loop, queue))

    @asynccontextmanager
    async def subscribe(self) -> AsyncIterator[asyncio.Queue[Event]]:
        queue: asyncio.Queue[Event] = asyncio.Queue(self._queue_size)
        entry = (asyncio.get_running_loop(), queue)
        with self._lock:
            self._subs.add(entry)
        try:
            yield queue
        finally:
            with self._lock:
                self._subs.discard(entry)


def _offer(queue: asyncio.Queue[Event], evt: Event) -> None:
    with contextlib.suppress(asyncio.QueueFull):
        queue.put_nowait(evt)


bus = EventBus()
