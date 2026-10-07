"""In-process event bus, fanned out to browsers over SSE.

``publish`` is thread-safe: the recorder, job worker and tray threads all call it. Each SSE
subscriber gets its own bounded asyncio queue; slow consumers drop events rather than block.
"""

from __future__ import annotations

import asyncio
import contextlib
import threading
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any


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

    def publish(self, type: str, data: dict[str, Any] | None = None) -> None:
        evt = Event(type, data or {})
        with self._lock:
            subs = list(self._subs)
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
