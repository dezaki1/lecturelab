"""Slides: list/serve/delete a lecture's slides, re-run OCR, capture settings and a test grab."""

from __future__ import annotations

import base64
import io
import sys
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlmodel import Session, col, select

from backend.core.config import Settings, get_settings
from backend.core.db import get_session
from backend.core.library import abs_path
from backend.core.models import Lecture, Slide
from backend.core.state import load_state, update_state
from backend.pipeline.slides import queue_ocr

router = APIRouter(prefix="/api", tags=["slides"])

# Settings the UI can change; stored in state.json and applied over config.toml at startup.
EDITABLE = ("enabled", "fallback_monitor", "window_titles")


class SlideRead(BaseModel):
    id: int
    t_s: float
    source: str
    ocr_text: str
    page: int | None = None


class SlideSettings(BaseModel):
    enabled: bool | None = None
    fallback_monitor: int | None = Field(default=None, ge=0)  # 0 = none
    window_titles: list[str] | None = None


def apply_saved_settings(settings: Settings) -> None:
    saved = load_state().get("slides") or {}
    for k in EDITABLE:
        if k in saved:
            setattr(settings.slides, k, saved[k])


@router.get("/lectures/{lecture_id}/slides", response_model=list[SlideRead])
def list_slides(lecture_id: int, session: Session = Depends(get_session)) -> list[dict]:
    if session.get(Lecture, lecture_id) is None:
        raise HTTPException(404, "Lecture not found")
    slides = session.exec(
        select(Slide).where(Slide.lecture_id == lecture_id).order_by(col(Slide.t_s))
    ).all()
    return [{**s.model_dump(), "ocr_text": s.ocr_text.strip()} for s in slides]


@router.get("/slides/{slide_id}/image")
def slide_image(slide_id: int, session: Session = Depends(get_session)) -> FileResponse:
    slide = session.get(Slide, slide_id)
    if slide is None or not abs_path(slide.image_path).exists():
        raise HTTPException(404, "Slide not found")
    return FileResponse(
        abs_path(slide.image_path),
        media_type="image/jpeg",
        headers={"Cache-Control": "max-age=86400, immutable"},  # files never change
    )


@router.delete("/slides/{slide_id}", status_code=204)
def delete_slide(slide_id: int, session: Session = Depends(get_session)) -> Response:
    slide = session.get(Slide, slide_id)
    if slide is None:
        raise HTTPException(404, "Slide not found")
    abs_path(slide.image_path).unlink(missing_ok=True)
    session.delete(slide)
    session.commit()
    return Response(status_code=204)


@router.post("/lectures/{lecture_id}/slides/ocr", status_code=202)
def rerun_ocr(lecture_id: int, session: Session = Depends(get_session)) -> dict[str, bool]:
    if session.get(Lecture, lecture_id) is None:
        raise HTTPException(404, "Lecture not found")
    for slide in session.exec(select(Slide).where(Slide.lecture_id == lecture_id)).all():
        slide.ocr_text = ""
        session.add(slide)
    session.commit()
    return {"queued": queue_ocr(session, lecture_id)}


@router.get("/settings/slides")
def slide_settings() -> dict[str, Any]:
    cfg = get_settings().slides
    monitors: list[dict[str, Any]] = []
    if sys.platform == "win32":
        from backend.capture import screens

        screens.thread_dpi_aware()
        monitors = [{"index": m.index, "label": m.label} for m in screens.monitors()]
    return {
        **{k: getattr(cfg, k) for k in EDITABLE},
        "interval_s": cfg.interval_s,
        "monitors": monitors,
    }


@router.put("/settings/slides")
def put_slide_settings(req: SlideSettings) -> dict[str, Any]:
    cfg = get_settings().slides
    changes = req.model_dump(exclude_unset=True)
    if "fallback_monitor" in changes and not changes["fallback_monitor"]:
        changes["fallback_monitor"] = None
    if "window_titles" in changes:
        changes["window_titles"] = [t.strip() for t in changes["window_titles"] or [] if t.strip()]
    for k, v in changes.items():
        setattr(cfg, k, v)
    update_state(slides={k: getattr(cfg, k) for k in EDITABLE})
    return slide_settings()


@router.post("/settings/slides/test")
def test_capture() -> dict[str, Any]:
    """Grab once, the way the recorder would, and return a small preview."""
    if sys.platform != "win32":
        raise HTTPException(409, "Slide capture is only available on Windows")
    from backend.capture import screens

    screens.thread_dpi_aware()
    grab = screens.WindowsScreen(get_settings().slides).grab()
    if grab is None:
        return {
            "found": False,
            "message": "No Zoom window found and no fallback monitor chosen.",
            "windows": [w.title for w in screens.windows() if w.title][:15],
        }
    img = grab.image.copy()
    img.thumbnail((640, 400))
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "JPEG", quality=80)
    return {
        "found": True,
        "source": grab.source,
        "window_title": grab.window_title,
        "size": list(grab.image.size),
        "preview": "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode(),
    }
