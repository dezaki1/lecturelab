from __future__ import annotations

import shutil
import subprocess
import threading
import time
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw, ImageFont

from backend.app import create_app
from backend.capture.slides import Grab, SlideDetector, dhash, fingerprint
from backend.core.config import Settings
from backend.core.jobs import Worker
from backend.pipeline.ocr_worker import reading_order
from tests.fakes import fake_factory

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not on PATH")


def slide(title: str, *lines: str, size: tuple[int, int] = (1280, 720)) -> Image.Image:
    img = Image.new("RGB", size, "white")
    d = ImageDraw.Draw(img)
    # Pillow's bundled font, so the slides look the same on every machine
    big, small = ImageFont.load_default(56), ImageFont.load_default(34)
    d.text((60, 50), title, fill="black", font=big)
    for i, line in enumerate(lines):
        d.text((60, 180 + 60 * i), line, fill="black", font=small)
    return img


def with_video(img: Image.Image, seed: int) -> Image.Image:
    """Paste a changing 'speaker video' tile into the right edge, like Zoom's participant strip."""
    rng = np.random.default_rng(seed)
    face = tuple(int(c) for c in rng.integers(0, 255, size=3))  # video changes in large blocks
    out = img.copy()
    out.paste(Image.new("RGB", (240, 200), face), (img.width - 250, 20))  # type: ignore[arg-type]
    return out


SLIDE_A = slide("Principal Component Analysis", "Eigenvectors of the covariance matrix")
SLIDE_B = slide("Kernel PCA", "Map the data into a feature space first", "Then do PCA there")


# Change detection --------------------------------------------------------------------------------
def test_detector_keeps_each_slide_once_and_ignores_video() -> None:
    det = SlideDetector()
    frames = (
        [("A", with_video(SLIDE_A, i)) for i in range(8)]
        + [("-", Image.blend(SLIDE_A, SLIDE_B, 0.5))]  # mid-transition
        + [("B", with_video(SLIDE_B, 10 + i)) for i in range(5)]
        + [("-", Image.new("RGB", (1280, 720), "black"))] * 3  # screen share paused
        + [("A", with_video(SLIDE_A, 20 + i)) for i in range(4)]  # back to slide A
    )
    kept = [(i, name) for i, (name, f) in enumerate(frames) if det.feed(fingerprint(f))]
    # The speaker tile is learned as video within a few frames; transitions are skipped;
    # each slide is kept once per appearance.
    assert [name for _, name in kept] == ["A", "B", "A"], kept
    assert kept[0][0] <= 6 and kept[1][0] == 10, kept  # ~6 frames to learn the speaker tile


def test_detector_small_changes_are_not_new_slides() -> None:
    det = SlideDetector()
    cursor = SLIDE_A.copy()
    ImageDraw.Draw(cursor).ellipse((600, 400, 630, 430), fill="red")  # laser pointer
    kept = [det.feed(fingerprint(f)) for f in (SLIDE_A, SLIDE_A, cursor, cursor, SLIDE_A)]
    assert kept == [False, True, False, False, False]


def test_dhash_is_stable_under_resizing() -> None:
    def dist(a: str, b: str) -> int:
        return (int(a, 16) ^ int(b, 16)).bit_count()

    assert dist(dhash(SLIDE_A), dhash(SLIDE_A.resize((640, 360)))) <= 3
    assert dist(dhash(SLIDE_A), dhash(SLIDE_B)) > 6


def test_reading_order_groups_boxes_into_lines() -> None:
    def box(x: float, y: float, w: float = 100, h: float = 30) -> list[list[float]]:
        return [[x, y], [x + w, y], [x + w, y + h], [x, y + h]]

    boxes = [box(300, 102), box(50, 100), box(50, 200)]
    text = reading_order(boxes, ["world", "hello", "next line"], [0.9, 0.95, 0.4])
    assert text == "hello world"  # low-score box dropped, same-line boxes joined left to right


# Recording with slide capture --------------------------------------------------------------------
class FakeScreen:
    """Shows slide A for `switch_at` grabs, then slide B, with a live speaker tile."""

    def __init__(self, switch_at: int) -> None:
        self.switch_at = switch_at
        self.n = 0
        self.lock = threading.Lock()

    def grab(self) -> Grab | None:
        with self.lock:
            self.n += 1
            base = SLIDE_A if self.n <= self.switch_at else SLIDE_B
            return Grab(with_video(base, self.n), "Zoom window", "Zoom Meeting")


@pytest.fixture
def rec(settings: Settings) -> TestClient:
    settings.slides.interval_s = 0.1
    screen = FakeScreen(switch_at=12)  # 1.2 s at 0.1 s per grab
    app = create_app(
        settings,
        start_worker=False,
        source_factory=fake_factory(),
        screen_factory=lambda s: screen,
    )
    with TestClient(app) as c:
        yield c  # type: ignore[misc]


def test_recording_captures_ocrs_and_indexes_slides(rec: TestClient) -> None:
    sid = rec.post("/api/subjects", json={"name": "ML"}).json()["id"]
    st = rec.post("/api/recorder/start", json={"subject_id": sid, "slides": True}).json()
    assert st["slides"] is not None
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        status = rec.get("/api/recorder").json()
        if status["slides"]["count"] >= 2:
            break
        time.sleep(0.1)
    time.sleep(0.5)  # no further slides while B stays on screen
    assert rec.get("/api/recorder").json()["slides"] == {"count": 2, "source": "Zoom window"}
    lid = rec.post("/api/recorder/stop").json()["lecture_id"]

    slides = rec.get(f"/api/lectures/{lid}/slides").json()
    assert len(slides) == 2 and slides[0]["t_s"] < slides[1]["t_s"]
    img = rec.get(f"/api/slides/{slides[0]['id']}/image")
    assert img.status_code == 200 and img.headers["content-type"] == "image/jpeg"

    # encode -> slides (OCR, queued before transcription) -> transcribe -> index
    assert Worker().run_pending() == 4
    lec = rec.get(f"/api/lectures/{lid}").json()
    assert lec["stages"]["slides"] == "done" and lec["slide_count"] == 2
    slides = rec.get(f"/api/lectures/{lid}/slides").json()
    assert "Principal Component Analysis" in slides[0]["ocr_text"]
    assert "Kernel PCA" in slides[1]["ocr_text"]

    hits = rec.get("/api/search", params={"q": "feature space"}).json()
    slide_hits = [h for h in hits if h["source"] == "slide"]
    assert slide_hits and slide_hits[0]["lecture_id"] == lid
    assert slide_hits[0]["start_s"] == slides[1]["t_s"]

    assert rec.delete(f"/api/slides/{slides[0]['id']}").status_code == 204
    assert len(rec.get(f"/api/lectures/{lid}/slides").json()) == 1


def test_slides_can_be_turned_off_per_recording(rec: TestClient) -> None:
    sid = rec.post("/api/subjects", json={"name": "ML"}).json()["id"]
    st = rec.post("/api/recorder/start", json={"subject_id": sid, "slides": False}).json()
    assert st["slides"] is None
    time.sleep(0.3)
    lid = rec.post("/api/recorder/stop").json()["lecture_id"]
    assert rec.get(f"/api/lectures/{lid}/slides").json() == []


def test_slide_settings_persist(client: TestClient, settings: Settings) -> None:
    r = client.put(
        "/api/settings/slides", json={"fallback_monitor": 2, "window_titles": [" Webex ", ""]}
    ).json()
    assert r["fallback_monitor"] == 2 and r["window_titles"] == ["Webex"]
    settings.slides.fallback_monitor = None
    from backend.api.slides import apply_saved_settings

    apply_saved_settings(settings)  # what the next start does
    assert settings.slides.fallback_monitor == 2
    r = client.put("/api/settings/slides", json={"fallback_monitor": 0}).json()
    assert r["fallback_monitor"] is None


# Video import ------------------------------------------------------------------------------------
def test_imported_video_yields_slides(
    client: TestClient, settings: Settings, tmp_path: Path
) -> None:
    settings.slides.from_video = True
    a, b, video = tmp_path / "a.png", tmp_path / "b.png", tmp_path / "lecture.mp4"
    SLIDE_A.save(a)
    SLIDE_B.save(b)
    subprocess.run(
        [
            "ffmpeg", "-v", "error", "-y",
            "-loop", "1", "-t", "8", "-i", str(a),
            "-loop", "1", "-t", "8", "-i", str(b),
            "-f", "lavfi", "-t", "16", "-i", "sine=frequency=440",
            "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0,format=yuv420p[v]",
            "-map", "[v]", "-map", "2:a", "-r", "5", "-c:v", "libx264", "-c:a", "aac",
            str(video),
        ],
        check=True,
    )  # fmt: skip
    sid = client.post("/api/subjects", json={"name": "ML"}).json()["id"]
    r = client.post(
        "/api/imports",
        params={"subject_id": sid, "filename": "lecture.mp4"},
        content=video.read_bytes(),
    )
    lid = r.json()["id"]
    assert Worker().run_pending() == 4  # import (+ slide scan), slides OCR, transcribe, index
    slides = client.get(f"/api/lectures/{lid}/slides").json()
    assert [s["source"] for s in slides] == ["video", "video"]
    assert slides[0]["t_s"] <= 4 and 8 <= slides[1]["t_s"] <= 12
    assert "Kernel PCA" in slides[1]["ocr_text"]
