"""Standalone child process: OCR slide images with RapidOCR (CPU, models bundled in the wheel).

Input (stdin, JSON): {"images": ["C:/.../slides/000012345.jpg", ...]}
Output (stdout, JSON lines): {"type": "result", "i": 0, "text": "..."} per image,
{"type": "progress", "fraction": 0.5}, and {"type": "error", "message": "..."} on failure.
Lines are ordered top to bottom, left to right, so the text reads like the slide.
"""

from __future__ import annotations

import json
import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any

MIN_SCORE = 0.5
PARALLEL = 3  # pictures read at once (each engine gets a share of the CPU threads)
COMPARE_SIZE = (640, 360)
CONTENT_LEVEL = 60  # grey levels (0-255) away from the background that count as content
MAX_REMOVED = 0.08  # more of the previous picture's content gone than this: a new slide
FRAME_SHARE = 0.8  # content on this share of all pictures is the screen around the slide


def emit(**msg: Any) -> None:
    sys.stdout.write(json.dumps(msg, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def reading_order(boxes: Any, txts: Any, scores: Any) -> str:
    items = []
    for box, txt, score in zip(boxes, txts, scores, strict=False):
        if score < MIN_SCORE or not txt.strip():
            continue
        ys = [p[1] for p in box]
        xs = [p[0] for p in box]
        items.append((min(ys), max(ys), min(xs), txt.strip()))
    items.sort(key=lambda it: (it[0], it[2]))
    lines: list[list[tuple[float, str]]] = []
    line_bottom = -1.0
    for top, bottom, left, txt in items:
        # Same line if this box starts above the middle of the current line's last box.
        if lines and top < line_bottom - (bottom - top) / 2:
            lines[-1].append((left, txt))
            line_bottom = max(line_bottom, bottom)
        else:
            lines.append([(left, txt)])
            line_bottom = bottom
    return "\n".join(" ".join(t for _, t in sorted(line)) for line in lines)


def _content(path: str) -> Any:
    """Where the slide has something on it (text, drawing, ink): pixels far from the
    background's grey level, on a picture big enough to tell words apart."""
    import numpy as np
    from PIL import Image, ImageOps

    with Image.open(path) as im:
        grey = np.asarray(ImageOps.grayscale(im).resize(COMPARE_SIZE), dtype=np.int16)
    return np.abs(grey - int(np.median(grey))) > CONTENT_LEVEL


def _removed(before: Any, after: Any) -> float:
    """Share of ``before``'s content that is gone in ``after`` (one pixel of slack)."""
    import numpy as np

    if not before.any():
        return 0.0  # an empty picture: anything after it only adds
    near = after.copy()
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            near |= np.roll(np.roll(after, dy, axis=0), dx, axis=1)
    return float((before & ~near).sum() / before.sum())


def runs(images: list[str]) -> list[list[int]]:
    """Group pictures of the same slide: the lecturer writing on it, a bullet appearing. Within
    a group each picture only adds to the one before (a new slide removes the old text), so the
    last, fullest picture holds all the text and one OCR of it reads the whole group."""
    import numpy as np

    masks: list[Any] = []
    for path in images:
        try:
            masks.append(_content(path))
        except Exception:
            masks.append(None)
    known = [m for m in masks if m is not None]
    if len(known) >= 3:
        # What is there on (nearly) every picture is the screen around the slide (Zoom's bars,
        # the dock): it never changes, so it must not water down what a new slide removes.
        frame = np.mean(known, axis=0) > FRAME_SHARE
        masks = [m & ~frame if m is not None else None for m in masks]
    groups: list[list[int]] = []
    prev = None
    for i, cur in enumerate(masks):
        if cur is not None and prev is not None and _removed(prev, cur) < MAX_REMOVED:
            groups[-1].append(i)
        else:
            groups.append([i])
        prev = cur
    return groups


def main() -> None:
    args = json.loads(sys.stdin.read())
    images: list[str] = args["images"]
    workers = max(1, min(PARALLEL, len(images)))
    threads = max(2, (os.cpu_count() or 4) // (workers + 1))
    local = threading.local()

    def engine() -> Any:
        if not hasattr(local, "engine"):
            from rapidocr import RapidOCR

            local.engine = RapidOCR(
                params={
                    "Global.log_level": "error",
                    "EngineConfig.onnxruntime.intra_op_num_threads": threads,
                }
            )
        return local.engine

    try:
        engine()  # fail early and clearly if RapidOCR can't load
    except Exception as e:
        emit(type="error", message=f"Could not load RapidOCR: {e}")
        sys.exit(1)

    def read(group: list[int]) -> tuple[list[int], str]:
        i = group[-1]
        try:
            out = engine()(images[i])
            text = reading_order(out.boxes, out.txts, out.scores) if out.boxes is not None else ""
        except Exception as e:  # one unreadable image must not stop the rest
            emit(type="warning", i=i, message=str(e))
            text = ""
        return group, text

    done = 0
    lock = threading.Lock()
    with ThreadPoolExecutor(workers) as pool:
        for group, text in pool.map(read, runs(images)):
            with lock:
                for i in group:
                    emit(type="result", i=i, text=text)
                done += len(group)
                emit(type="progress", fraction=done / len(images))


if __name__ == "__main__":
    main()
