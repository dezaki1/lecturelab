"""Slides in the pipeline: find slides in imported videos, and OCR every lecture's slides.

OCR runs in a child process (``ocr_worker``) and is queued before transcription, so slide text
is ready for generation and indexing. Slides are a bonus: a failed OCR marks the ``slides``
stage failed but never the lecture.
"""

from __future__ import annotations

import io
import json
import logging
import subprocess
import sys
from pathlib import Path

import numpy as np
from PIL import Image
from sqlmodel import Session, col, select

from backend.capture.slides import GRID, SlideDetector, save_slide
from backend.core import jobs
from backend.core.config import get_settings
from backend.core.db import session_scope
from backend.core.events import bus
from backend.core.jobs import JobContext, JobError, handler
from backend.core.library import abs_path
from backend.core.media import CREATE_NO_WINDOW
from backend.core.models import Lecture, Slide

log = logging.getLogger(__name__)

SLIDES_JOB = "slides"
REPO_ROOT = Path(__file__).resolve().parents[2]
VIDEO_STEP_S = 2.0


def queue_ocr(s: Session, lecture_id: int) -> bool:
    """Queue OCR if the lecture has slides without text. Returns whether a job was queued."""
    if not get_settings().slides.ocr:
        return False
    todo = s.exec(
        select(Slide.id).where(Slide.lecture_id == lecture_id, Slide.ocr_text == "").limit(1)
    ).first()
    if todo is None:
        return False
    lecture = s.get(Lecture, lecture_id)
    if lecture is not None:
        lecture.stages = {**lecture.stages, "slides": "pending"}
        s.add(lecture)
        s.commit()
    jobs.enqueue(s, SLIDES_JOB, lecture_id=lecture_id)
    return True


# Slide text for the LLM ---------------------------------------------------------------------------
SLIDE_TEXT_MAX_CHARS = 600  # per slide
SLIDES_MAX_CHARS = 16_000  # whole section (~4k tokens)


def primary_slides(s: Session, lecture_id: int) -> list[Slide]:
    """The slides that stand for the lecture: pages of an added slide PDF (cleaner text) if
    there are any, else the captured ones."""
    rows = s.exec(
        select(Slide).where(Slide.lecture_id == lecture_id).order_by(col(Slide.t_s))
    ).all()
    pdf = [r for r in rows if r.source == "pdf"]
    return list(pdf or rows)


def slides_text(slides: list[Slide]) -> str:
    """One line per slide with text: "[mm:ss] (PDF p. 4) Title / bullet / bullet"."""
    from backend.core.util import format_hms

    lines: list[str] = []
    last = ""
    total = 0
    for sl in slides:
        text = " / ".join(x.strip() for x in sl.ocr_text.splitlines() if x.strip())
        if not text or text == last:
            continue
        last = text
        if len(text) > SLIDE_TEXT_MAX_CHARS:
            text = text[:SLIDE_TEXT_MAX_CHARS].rsplit(" ", 1)[0] + " …"
        where = f"(PDF p. {sl.page}) " if sl.source == "pdf" and sl.page else ""
        line = f"[{format_hms(sl.t_s)}] {where}{text}"
        total += len(line)
        if total > SLIDES_MAX_CHARS:
            lines.append("(more slides omitted)")
            break
        lines.append(line)
    return "\n".join(lines)


# Video imports -----------------------------------------------------------------------------------
def find_video_slides(
    ctx: JobContext, lecture_id: int, folder: Path, src: Path, duration: float
) -> int:
    """Scan a video at one frame per VIDEO_STEP_S and save a full-size frame per new slide."""
    settings = get_settings()
    w, h = GRID
    cmd = [
        settings.ffmpeg, "-hide_banner", "-loglevel", "error", "-i", str(src), "-an",
        "-vf", f"fps=1/{VIDEO_STEP_S},scale={w}:{h}:flags=area",
        "-pix_fmt", "gray", "-f", "rawvideo", "pipe:1",
    ]  # fmt: skip
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=CREATE_NO_WINDOW
    )
    assert proc.stdout is not None
    detector = SlideDetector(settings.slides.change_threshold)
    times: list[float] = []
    frame = w * h
    k = 0
    total = max(duration / VIDEO_STEP_S, 1)
    while chunk := proc.stdout.read(frame):
        if len(chunk) < frame:
            break
        fp = np.frombuffer(chunk, dtype=np.uint8).reshape(h, w).astype(np.float32)
        if detector.feed(fp):
            times.append(k * VIDEO_STEP_S)
        k += 1
        if k % 30 == 0:
            ctx.progress(min(k / total, 0.99) * 0.8, "Finding slides in the video")
    err = proc.stderr.read().decode(errors="replace") if proc.stderr else ""
    if proc.wait() != 0:
        raise JobError(f"ffmpeg could not read the video: {err.strip()[-300:]}")

    for i, t in enumerate(times):
        img = _frame_at(src, t)
        if img is not None:
            save_slide(settings.slides, lecture_id, folder, img, t, source="video")
        ctx.progress(0.8 + 0.2 * (i + 1) / len(times), "Saving slides")
    return len(times)


def _frame_at(src: Path, t: float) -> Image.Image | None:
    cmd = [
        get_settings().ffmpeg, "-hide_banner", "-loglevel", "error", "-ss", f"{t:.2f}",
        "-i", str(src), "-frames:v", "1", "-f", "image2pipe", "-vcodec", "png", "pipe:1",
    ]  # fmt: skip
    out = subprocess.run(cmd, capture_output=True, creationflags=CREATE_NO_WINDOW, check=False)
    if out.returncode != 0 or not out.stdout:
        return None
    return Image.open(io.BytesIO(out.stdout)).convert("RGB")


# OCR job -----------------------------------------------------------------------------------------
def run_ocr(paths: list[Path], ctx: JobContext) -> list[str]:
    proc = subprocess.Popen(
        [sys.executable, "-m", "backend.pipeline.ocr_worker"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=REPO_ROOT,
        creationflags=CREATE_NO_WINDOW,
    )
    assert proc.stdin and proc.stdout and proc.stderr
    proc.stdin.write(json.dumps({"images": [str(p) for p in paths]}))
    proc.stdin.close()
    texts = [""] * len(paths)
    error = ""
    for line in proc.stdout:
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        if msg.get("type") == "result":
            texts[msg["i"]] = msg["text"]
        elif msg.get("type") == "progress":
            ctx.progress(msg["fraction"], f"Reading slides ({len(paths)})")
        elif msg.get("type") == "error":
            error = msg["message"]
    stderr = proc.stderr.read()
    if proc.wait() != 0:
        tail = stderr.strip().splitlines()[-3:]
        raise JobError(error or " | ".join(tail) or "OCR worker failed")
    return texts


@handler(SLIDES_JOB)
def ocr_slides(ctx: JobContext) -> None:
    assert ctx.lecture_id is not None
    try:
        _ocr(ctx)
    except Exception as e:
        _set_stage(ctx.lecture_id, "failed", f"Reading slides failed: {e}")
        raise


def _ocr(ctx: JobContext) -> None:
    lecture_id = ctx.lecture_id
    assert lecture_id is not None
    with session_scope() as s:
        slides = s.exec(
            select(Slide)
            .where(Slide.lecture_id == lecture_id, Slide.ocr_text == "")
            .order_by(col(Slide.t_s))
        ).all()
        todo = [(sl.id, abs_path(sl.image_path)) for sl in slides]
    _set_stage(lecture_id, "running")
    todo = [(sid, p) for sid, p in todo if p.exists()]
    texts = run_ocr([p for _, p in todo], ctx) if todo else []
    with session_scope() as s:
        for (sid, _), text in zip(todo, texts, strict=True):
            slide = s.get(Slide, sid)
            if slide is not None:
                # A single space marks "OCR ran, no text" so the slide isn't queued again.
                slide.ocr_text = text or " "
                s.add(slide)
        s.commit()
        lecture = s.get(Lecture, lecture_id)
        reindex = lecture is not None and lecture.stages.get("index") == "done"
        if reindex:
            from backend.search.index import queue_index

            queue_index(s, lecture_id)
    _set_stage(lecture_id, "done")


def _set_stage(lecture_id: int, state: str, error: str = "") -> None:
    with session_scope() as s:
        lecture = s.get(Lecture, lecture_id)
        if lecture is None:
            return
        lecture.stages = {**lecture.stages, "slides": state}
        meta = {k: v for k, v in lecture.meta.items() if k != "slides_error"}
        lecture.meta = {**meta, "slides_error": error} if error else meta
        s.add(lecture)
        s.commit()
        subject_id = lecture.subject_id
    bus.publish("lecture", {"id": lecture_id, "subject_id": subject_id})
