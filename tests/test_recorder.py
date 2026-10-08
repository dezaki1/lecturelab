from __future__ import annotations

import shutil
import subprocess
import sys
import time
from collections import namedtuple
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.capture import recorder as recorder_mod
from backend.core.config import Settings
from backend.core.jobs import Worker
from backend.core.media import probe_duration
from tests.conftest import make_settings
from tests.fakes import fake_factory

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not on PATH")


def _client(settings: Settings, gap=None) -> TestClient:
    return TestClient(create_app(settings, start_worker=False, source_factory=fake_factory(gap)))


@pytest.fixture
def rec(settings: Settings) -> Iterator[TestClient]:
    with _client(settings, gap=(0.3, 0.9)) as c:
        yield c


def _subject(c: TestClient, name: str = "Reinforcement Learning") -> int:
    return c.post("/api/subjects", json={"name": name}).json()["id"]


def test_record_bookmark_stop_encode(rec: TestClient, settings: Settings) -> None:
    sid = _subject(rec)
    st = rec.post("/api/recorder/start", json={"subject_id": sid, "mic": True}).json()
    assert st["recording"] and st["subject_id"] == sid
    assert rec.post("/api/recorder/start", json={"subject_id": sid}).status_code == 409
    time.sleep(0.6)
    bm = rec.post("/api/recorder/bookmark", json={"kind": "confused"}).json()
    assert 0.4 < bm["t_s"] < 2.5
    time.sleep(1.0)
    stopped = rec.post("/api/recorder/stop").json()
    # Silent gap (0.3-0.9 s) was padded, so the file length matches wall-clock time.
    assert 1.4 < stopped["duration_s"] < 4.0
    assert rec.get("/api/recorder").json()["recording"] is False

    lid = stopped["lecture_id"]
    lec = rec.get(f"/api/lectures/{lid}").json()
    assert lec["status"] == "processing" and lec["job_progress"] is not None

    assert Worker().run_pending() == 2  # encode, then the auto-queued transcription
    lec = rec.get(f"/api/lectures/{lid}").json()
    assert lec["status"] == "ready", lec
    assert lec["stages"] == {"capture": "done", "transcribe": "done"}
    assert lec["has_audio"] and lec["stages"]["capture"] == "done"
    assert [b["kind"] for b in lec["bookmarks"]] == ["confused"]
    folder = next((settings.subjects_dir / "reinforcement-learning").iterdir())
    assert (folder / "audio.m4a").exists() and not (folder / "recording.pcm").exists()
    assert abs(probe_duration(folder / "audio.m4a") - stopped["duration_s"]) < 0.2

    r = rec.get(f"/api/lectures/{lid}/audio", headers={"Range": "bytes=0-99"})
    assert r.status_code == 206 and len(r.content) == 100

    lst = rec.get("/api/lectures", params={"subject_id": sid}).json()
    assert lst[0]["number"] == 1 and lst[0]["bookmark_count"] == 1


def test_start_without_subject_id_uses_last_subject(rec: TestClient) -> None:
    _subject(rec, "A")
    b = _subject(rec, "B")
    rec.post("/api/recorder/start", json={"subject_id": b})
    rec.post("/api/recorder/stop")
    assert rec.post("/api/recorder/start", json={}).json()["subject_id"] == b
    rec.post("/api/recorder/stop")


def test_errors(rec: TestClient, monkeypatch) -> None:
    assert rec.post("/api/recorder/start", json={}).status_code == 409  # no subjects yet
    assert rec.post("/api/recorder/bookmark", json={"kind": "important"}).status_code == 409
    assert rec.post("/api/recorder/stop").status_code == 409
    sid = _subject(rec)
    Usage = namedtuple("Usage", "total used free")
    monkeypatch.setattr(recorder_mod.shutil, "disk_usage", lambda _: Usage(1, 1, int(0.5e9)))
    r = rec.post("/api/recorder/start", json={"subject_id": sid})
    assert r.status_code == 409 and "GB free" in r.json()["detail"]


def test_move_and_delete_lecture(rec: TestClient, settings: Settings) -> None:
    a, b = _subject(rec, "A"), _subject(rec, "B")
    lid = rec.post("/api/recorder/start", json={"subject_id": a}).json()["lecture_id"]
    time.sleep(0.3)
    assert rec.delete(f"/api/lectures/{lid}").status_code == 409  # still recording
    rec.post("/api/recorder/stop")
    Worker().run_pending()
    r = rec.patch(f"/api/lectures/{lid}", json={"subject_id": b, "title": "Intro", "kind": "UE"})
    assert r.status_code == 200, r.text
    assert r.json()["subject_id"] == b and r.json()["has_audio"]
    assert list((settings.subjects_dir / "b").iterdir())
    assert rec.patch(f"/api/lectures/{lid}", json={"kind": "XX"}).status_code == 422
    assert rec.delete(f"/api/subjects/{b}").status_code == 409  # has lectures
    assert rec.delete(f"/api/lectures/{lid}").status_code == 204
    assert not list((settings.subjects_dir / "b").iterdir())
    assert list(settings.trash_dir.iterdir())  # moved to trash, not deleted


def test_killed_process_leaves_playable_recording(tmp_path: Path) -> None:
    data = tmp_path / "data"
    settings = make_settings(data)
    with _client(settings) as c:
        sid = _subject(c)
    root = Path(__file__).resolve().parent.parent
    proc = subprocess.Popen(
        [sys.executable, str(root / "tests" / "crash_helper.py"), str(data), str(sid)],
        stdout=subprocess.PIPE,
        text=True,
        cwd=root,
    )
    try:
        assert proc.stdout is not None and proc.stdout.readline().strip() == "RECORDING"
        time.sleep(2.0)
    finally:
        proc.kill()  # no clean shutdown: simulates a crash / killed app
        proc.wait()

    settings = make_settings(data)
    with _client(settings) as c:  # startup runs crash recovery
        lec = c.get("/api/lectures", params={"subject_id": sid}).json()[0]
        assert lec["status"] == "processing"
        assert Worker().run_pending() == 2
        lec = c.get(f"/api/lectures/{lec['id']}").json()
        assert lec["status"] == "ready" and lec["has_audio"], lec
        assert 1.0 < lec["duration_s"] < 4.0
        assert len(lec["bookmarks"]) == 1
