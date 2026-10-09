"""Standalone child process: OCR slide images with RapidOCR (CPU, models bundled in the wheel).

Input (stdin, JSON): {"images": ["C:/.../slides/000012345.jpg", ...]}
Output (stdout, JSON lines): {"type": "result", "i": 0, "text": "..."} per image,
{"type": "progress", "fraction": 0.5}, and {"type": "error", "message": "..."} on failure.
Lines are ordered top to bottom, left to right, so the text reads like the slide.
"""

from __future__ import annotations

import json
import sys
from typing import Any

MIN_SCORE = 0.5


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


def main() -> None:
    args = json.loads(sys.stdin.read())
    images: list[str] = args["images"]
    try:
        from rapidocr import RapidOCR

        engine = RapidOCR(params={"Global.log_level": "error"})
    except Exception as e:
        emit(type="error", message=f"Could not load RapidOCR: {e}")
        sys.exit(1)
    for i, path in enumerate(images):
        try:
            out = engine(path)
            text = reading_order(out.boxes, out.txts, out.scores) if out.boxes is not None else ""
        except Exception as e:  # one unreadable image must not stop the rest
            emit(type="warning", i=i, message=str(e))
            text = ""
        emit(type="result", i=i, text=text)
        emit(type="progress", fraction=(i + 1) / len(images))


if __name__ == "__main__":
    main()
