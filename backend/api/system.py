from __future__ import annotations

import json
import shutil
from collections.abc import AsyncIterator

from fastapi import APIRouter, Request
from sse_starlette.sse import EventSourceResponse

from backend.core.config import get_settings
from backend.core.events import bus

router = APIRouter(prefix="/api", tags=["system"])


@router.get("/health")
def health() -> dict[str, object]:
    return {"ok": True, "app": "lecturelab"}


@router.get("/system")
def system_info() -> dict[str, object]:
    s = get_settings()
    usage = shutil.disk_usage(s.data_dir)
    return {
        "data_dir": str(s.data_dir),
        "free_gb": round(usage.free / 1e9, 1),
        "hotkeys": s.hotkeys.model_dump(),
        "recorder": {"mic_default": s.recorder.mic_default, "slides_default": s.slides.enabled},
    }


@router.get("/events")
async def events(request: Request) -> EventSourceResponse:
    async def stream() -> AsyncIterator[dict[str, str]]:
        async with bus.subscribe() as queue:
            yield {"event": "hello", "data": "{}"}
            while not await request.is_disconnected():
                evt = await queue.get()
                yield {"event": evt.type, "data": json.dumps(evt.data, default=str)}

    return EventSourceResponse(stream(), ping=15)
