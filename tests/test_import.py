from __future__ import annotations

import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.core.config import Settings
from backend.core.jobs import Worker
from backend.core.media import probe_duration
from tests.fakes import fake_factory

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not on PATH")


def _ffmpeg(*args: str) -> None:
    subprocess.run(["ffmpeg", "-v", "error", "-y", *args], check=True)


@pytest.fixture(scope="module")
def media(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    d = tmp_path_factory.mktemp("media")
    tone = ["-f", "lavfi", "-i", "sine=frequency=440:duration=3"]
    video = ["-f", "lavfi", "-i", "testsrc=size=160x120:rate=10:duration=3"]
    files = {
        "mp4": d / "GMT20261001-091500_Recording.mp4",
        "mp3": d / "lecture.mp3",
        "wav": d / "lecture.wav",
        "webm": d / "lecture.webm",
        "m4a": d / "lecture.m4a",
        "silent": d / "screen-only.mp4",
    }
    _ffmpeg(*video, *tone, "-shortest", "-c:v", "libx264", "-c:a", "aac",
            "-metadata", "creation_time=2026-10-01T09:15:00Z", str(files["mp4"]))  # fmt: skip
    _ffmpeg(*tone, str(files["mp3"]))
    _ffmpeg(*tone, str(files["wav"]))
    _ffmpeg(*tone, "-c:a", "libopus", str(files["webm"]))
    _ffmpeg(*tone, "-c:a", "aac", str(files["m4a"]))
    _ffmpeg(*video, "-c:v", "libx264", str(files["silent"]))
    return files


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(settings, start_worker=False, source_factory=fake_factory())) as c:
        yield c


def _upload(c: TestClient, sid: int, path: Path, **params) -> object:
    return c.post(
        "/api/imports",
        params={"subject_id": sid, "filename": path.name, **params},
        content=path.read_bytes(),
    )


@pytest.mark.parametrize("fmt", ["mp4", "mp3", "wav", "webm", "m4a"])
def test_import_formats(client: TestClient, settings: Settings, media, fmt: str) -> None:
    sid = client.post("/api/subjects", json={"name": "NLP"}).json()["id"]
    r = _upload(client, sid, media[fmt], kind="UE")
    assert r.status_code == 201, r.text
    lec = r.json()
    assert lec["source"] == "import" and lec["status"] == "processing" and lec["kind"] == "UE"
    assert Worker().run_pending() == 3  # import, transcription, search index
    lec = client.get(f"/api/lectures/{lec['id']}").json()
    assert lec["status"] == "ready" and lec["has_audio"], lec
    folder = next((settings.subjects_dir / "nlp").iterdir())
    assert not (folder / f"source.{fmt}").exists()  # uploaded copy removed
    assert (folder / "transcript.json").exists()
    assert abs(probe_duration(folder / "audio.m4a") - 3.0) < 0.2
    assert not list(settings.tmp_dir.iterdir())


def test_import_uses_creation_time_and_filename(client: TestClient, media) -> None:
    sid = client.post("/api/subjects", json={"name": "RL"}).json()["id"]
    lec = _upload(client, sid, media["mp4"]).json()
    assert lec["title"] == "GMT20261001-091500 Recording"
    assert lec["started_at"].startswith("2026-10-01T09:15:00")


def test_import_falls_back_to_last_modified(client: TestClient, media) -> None:
    sid = client.post("/api/subjects", json={"name": "RL"}).json()["id"]
    # mtime 2026-10-02 10:00:03 UTC minus 3 s duration
    lec = _upload(
        client, sid, media["mp3"], last_modified_ms=1_790_935_203_000, title="Intro"
    ).json()
    assert lec["title"] == "Intro"
    assert lec["started_at"].startswith("2026-10-02T10:00:00")


def test_import_rejections(client: TestClient, media, tmp_path: Path) -> None:
    sid = client.post("/api/subjects", json={"name": "RL"}).json()["id"]
    r = _upload(client, sid, media["silent"])
    assert r.status_code == 422 and "no audio" in r.json()["detail"]
    junk = tmp_path / "notes.txt"
    junk.write_text("hello")
    assert _upload(client, sid, junk).status_code == 415
    fake = tmp_path / "broken.mp3"
    fake.write_bytes(b"not really audio" * 100)
    assert _upload(client, sid, fake).status_code == 422
    assert _upload(client, 999, media["mp3"]).status_code == 404
    assert client.get("/api/lectures", params={"subject_id": sid}).json() == []


def test_failed_extraction_marks_lecture(client: TestClient, settings: Settings, media) -> None:
    sid = client.post("/api/subjects", json={"name": "RL"}).json()["id"]
    lid = _upload(client, sid, media["wav"]).json()["id"]
    folder = next((settings.subjects_dir / "rl").iterdir())
    (folder / "source.wav").write_bytes(b"corrupted")  # e.g. disk error after upload
    Worker().run_pending()
    lec = client.get(f"/api/lectures/{lid}").json()
    assert lec["status"] == "failed" and lec["error"].startswith("Import failed")
