"""Slide capture: keep one frame per distinct slide, from screen grabs or video frames.

Change detection works on a 64x36 grayscale thumbnail. Cells that keep changing between frames
(speaker videos, the participant strip, a playing video) are learned as "volatile" and ignored,
so only the slide area decides. A frame becomes a new slide when enough stable cells differ from
the last kept slide *and* the frame has settled (it matches the previous frame), which skips
transitions and half-drawn slides.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import numpy as np
from PIL import Image

from backend.core.config import Settings, SlidesConfig
from backend.core.db import session_scope
from backend.core.events import bus
from backend.core.library import rel_path
from backend.core.models import Slide

log = logging.getLogger(__name__)

GRID = (64, 36)  # thumbnail width, height
SLIDES_DIR = "slides"
PIXEL_DELTA = 14.0  # gray levels; below this a cell counts as unchanged (compression noise)
VOLATILE = 0.5  # EMA of "cell changed" above which a cell is treated as video
BLANK_STD = 4.0  # near-uniform frames (black screen, loading) are never slides


def fingerprint(img: Image.Image) -> np.ndarray:
    return np.asarray(img.convert("L").resize(GRID, Image.Resampling.BOX), dtype=np.float32)


def dhash(img: Image.Image) -> str:
    """64-bit difference hash, for spotting the same slide shown twice."""
    g = np.asarray(img.convert("L").resize((9, 8), Image.Resampling.BOX), dtype=np.int16)
    bits = (g[:, 1:] > g[:, :-1]).flatten()
    return f"{int(''.join('1' if b else '0' for b in bits), 2):016x}"


def _ink(fp: np.ndarray) -> np.ndarray:
    """Cells that differ from the background (the frame's most common gray level)."""
    return np.abs(fp - np.median(fp)) > PIXEL_DELTA


@dataclass
class SlideDetector:
    # Fraction of the slide's content (non-background cells) that must change. Measured against
    # content, not the whole frame, because text slides are mostly empty background.
    change_threshold: float = 0.15

    def __post_init__(self) -> None:
        self._prev: np.ndarray | None = None
        self._kept: np.ndarray | None = None
        self._volatility = np.zeros((GRID[1], GRID[0]), dtype=np.float32)

    def feed(self, fp: np.ndarray) -> bool:
        """Feed one frame's fingerprint; True if it should be kept as a new slide."""
        prev, self._prev = self._prev, fp
        if prev is None:
            return False
        moving = np.abs(fp - prev) > PIXEL_DELTA
        self._volatility = 0.8 * self._volatility + 0.2 * moving
        if fp.std() < BLANK_STD:
            return False
        stable = self._volatility < VOLATILE
        if stable.mean() < 0.3:  # mostly video: judge the whole frame
            stable = np.ones_like(stable)
        floor = 0.03 * stable.sum()  # a nearly empty slide shouldn't make specks count

        def changed(a: np.ndarray, b: np.ndarray) -> float:
            content = ((_ink(a) | _ink(b)) & stable).sum()
            diff = ((np.abs(a - b) > PIXEL_DELTA) & stable).sum()
            return float(diff) / max(float(content), floor, 1.0)

        settled = changed(fp, prev) < self.change_threshold / 2
        if not settled:
            return False
        if self._kept is None or changed(fp, self._kept) >= self.change_threshold:
            self._kept = fp
            return True
        return False


def save_slide(
    cfg: SlidesConfig, lecture_id: int, folder: Path, img: Image.Image, t_s: float, **extra: Any
) -> Slide:
    """Write the slide image (JPEG, capped width) and its DB row."""
    out_dir = folder / SLIDES_DIR
    out_dir.mkdir(exist_ok=True)
    if img.width > cfg.max_width:
        img = img.resize(
            (cfg.max_width, round(img.height * cfg.max_width / img.width)),
            Image.Resampling.LANCZOS,
        )
    ms = round(t_s * 1000)
    path = out_dir / f"{ms:09d}.jpg"
    img.convert("RGB").save(path, "JPEG", quality=cfg.jpeg_quality, optimize=True)
    with session_scope() as s:
        slide = Slide(
            lecture_id=lecture_id,
            t_s=round(t_s, 2),
            image_path=rel_path(path),
            phash=dhash(img),
            **extra,
        )
        s.add(slide)
        s.commit()
        s.refresh(slide)
        s.expunge(slide)
    return slide


# Live capture ------------------------------------------------------------------------------------
@dataclass
class Grab:
    image: Image.Image
    source: str  # what was captured, e.g. "Zoom window" or "Monitor 2"
    window_title: str = ""


class ScreenSource(Protocol):
    def grab(self) -> Grab | None: ...


ScreenFactory = Callable[[Settings], ScreenSource | None]


def default_screen(settings: Settings) -> ScreenSource | None:
    import sys

    if sys.platform != "win32":
        return None
    from backend.capture.screens import WindowsScreen

    return WindowsScreen(settings.slides)


class SlideGrabber:
    """Background thread that grabs the screen every few seconds during a recording."""

    def __init__(
        self,
        cfg: SlidesConfig,
        screen: ScreenSource,
        lecture_id: int,
        folder: Path,
        clock: Callable[[], float],
    ) -> None:
        self.cfg = cfg
        self.screen = screen
        self.lecture_id = lecture_id
        self.folder = folder
        self.clock = clock  # seconds since the recording started
        self.detector = SlideDetector(cfg.change_threshold)
        self.count = 0
        self.source = ""  # "" = nothing to capture right now
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="slides", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=10)

    def status(self) -> dict[str, Any]:
        return {"count": self.count, "source": self.source}

    def _run(self) -> None:
        from backend.capture.screens import thread_dpi_aware

        thread_dpi_aware()
        errors = 0
        while not self._stop.wait(self.cfg.interval_s):
            try:
                self._step()
                errors = 0
            except Exception:
                errors += 1
                if errors in (1, 10, 100):  # don't flood the log with the same failure
                    log.exception("Slide capture failed (%d in a row)", errors)

    def _step(self) -> None:
        t = self.clock()
        grab = self.screen.grab()
        source = grab.source if grab else ""
        if source != self.source:
            self.source = source
            self._publish()
        if grab is None:
            return
        if not self.detector.feed(fingerprint(grab.image)):
            return
        slide = save_slide(
            self.cfg,
            self.lecture_id,
            self.folder,
            grab.image,
            t,
            source="screen",
            window_title=grab.window_title[:200],
        )
        self.count += 1
        log.debug("Slide %d at %.1f s (%s)", slide.id, t, source)
        self._publish()

    def _publish(self) -> None:
        bus.publish("recorder.slides", {"lecture_id": self.lecture_id, **self.status()})


def clock_from(t0: float) -> Callable[[], float]:
    return lambda: time.monotonic() - t0
