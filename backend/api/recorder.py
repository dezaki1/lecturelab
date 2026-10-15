from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.capture.recorder import RecorderError, get_recorder
from backend.core.models import BookmarkKind

router = APIRouter(prefix="/api/recorder", tags=["recorder"])


class StartRequest(BaseModel):
    subject_id: int | None = None
    mic: bool | None = None
    slides: bool | None = None
    title: str | None = None
    kind: str = ""
    live: bool | None = None  # live transcript (default: the live-mode setting)


class BookmarkRequest(BaseModel):
    kind: BookmarkKind = BookmarkKind.important
    note: str = ""


@router.get("")
def status() -> dict[str, Any]:
    return get_recorder().status()


@router.post("/start")
def start(req: StartRequest) -> dict[str, Any]:
    try:
        return get_recorder().start(
            req.subject_id,
            mic=req.mic,
            slides=req.slides,
            title=req.title,
            kind=req.kind,
            live=req.live,
        )
    except RecorderError as e:
        raise HTTPException(409, str(e)) from e


@router.post("/stop")
def stop() -> dict[str, Any]:
    result = get_recorder().stop()
    if result is None:
        raise HTTPException(409, "Not recording")
    return result


@router.post("/bookmark")
def bookmark(req: BookmarkRequest) -> dict[str, Any]:
    try:
        return get_recorder().bookmark(req.kind, req.note)
    except RecorderError as e:
        raise HTTPException(409, str(e)) from e
