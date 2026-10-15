"""Live mode: the transcript while recording, switching it on/off, quick questions."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from backend.ai.types import LLMError
from backend.capture.recorder import RecorderError, get_recorder
from backend.core.config import get_settings
from backend.core.state import load_state, update_state
from backend.study.live_qa import LiveAskError, ask, load_answers

router = APIRouter(tags=["live"])


class Toggle(BaseModel):
    on: bool


class Question(BaseModel):
    question: str = Field(min_length=1, max_length=1000)


class LiveSettings(BaseModel):
    enabled: bool


def apply_saved_settings(settings) -> None:  # type: ignore[no-untyped-def]
    saved = load_state().get("live") or {}
    if "enabled" in saved:
        settings.live.enabled = saved["enabled"]


@router.get("/api/live")
def live() -> dict[str, Any]:
    rec = get_recorder()
    state = rec.live_state()
    state["enabled_default"] = get_settings().live.enabled
    a = rec._active
    state["answers"] = load_answers(a.folder) if a is not None else []
    return state


@router.post("/api/live/toggle")
def toggle(req: Toggle) -> dict[str, Any]:
    try:
        get_recorder().set_live(req.on)
    except RecorderError as e:
        raise HTTPException(409, str(e)) from e
    return live()


@router.post("/api/live/ask")
def ask_question(req: Question) -> dict[str, Any]:
    try:
        return ask(get_recorder(), req.question)
    except (LiveAskError, LLMError) as e:
        raise HTTPException(422, str(e)) from e


@router.get("/api/settings/live")
def live_settings() -> dict[str, bool]:
    return {"enabled": get_settings().live.enabled}


@router.put("/api/settings/live")
def put_live_settings(req: LiveSettings) -> dict[str, bool]:
    get_settings().live.enabled = req.enabled
    update_state(live={"enabled": req.enabled})
    return live_settings()
