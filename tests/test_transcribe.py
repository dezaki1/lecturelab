from __future__ import annotations

import json
import math
import os
import re
import shutil
from collections.abc import Iterator
from itertools import pairwise
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.core.config import Settings, TranscriptionConfig, load_settings
from backend.core.jobs import Worker
from backend.pipeline.transcribe import attempts, hotwords_for, resegment
from tests.fakes import fake_factory

FIXTURES = Path(__file__).parent / "fixtures"
pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not on PATH")


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(settings, start_worker=False, source_factory=fake_factory())) as c:
        yield c


def _import_fixture(
    c: TestClient, vocabulary: list[str] | None = None, language: str = "auto"
) -> int:
    sid = c.post(
        "/api/subjects",
        json={"name": "ML", "vocabulary": vocabulary or [], "language": language},
    ).json()["id"]
    r = c.post(
        "/api/imports",
        params={"subject_id": sid, "filename": "lecture.m4a"},
        content=(FIXTURES / "lecture.m4a").read_bytes(),
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_pipeline_end_to_end_on_fixture(client: TestClient, settings: Settings) -> None:
    lid = _import_fixture(client, vocabulary=["PCA", "k-means"])
    assert Worker().run_pending() == 2  # import_media -> transcribe
    lec = client.get(f"/api/lectures/{lid}").json()
    assert lec["status"] == "ready" and lec["stages"]["transcribe"] == "done"
    assert lec["language"] == "en"
    stats = lec["transcription"]
    assert stats["device"] == "cuda" and stats["gpu_fallback_reason"] is None

    tr = client.get(f"/api/lectures/{lid}/transcript").json()
    segs = tr["segments"]
    assert len(segs) == 23  # 113.8 s in 5 s fake segments
    assert segs[0]["start"] == 0 and abs(segs[-1]["end"] - 113.8) < 0.1
    assert all(a["end"] <= b["start"] for a, b in pairwise(segs))

    folder = next((settings.subjects_dir / "ml").iterdir())
    data = json.loads((folder / "transcript.json").read_text(encoding="utf-8"))
    assert data["segments"][0]["words"][0]["word"].strip() == "This"
    assert data["hotwords"] == "PCA, k-means"


def test_rerun_replaces_segments_and_forces_language(client: TestClient) -> None:
    lid = _import_fixture(client, language="de")
    Worker().run_pending()
    assert client.get(f"/api/lectures/{lid}").json()["language"] == "de"  # subject override
    r = client.post(f"/api/lectures/{lid}/transcribe", json={"language": "en"})
    assert r.status_code == 202 and r.json()["status"] == "processing"
    assert client.post(f"/api/lectures/{lid}/transcribe", json={}).status_code == 409  # queued
    Worker().run_pending()
    assert client.get(f"/api/lectures/{lid}").json()["language"] == "en"
    assert len(client.get(f"/api/lectures/{lid}/transcript").json()["segments"]) == 23


def test_gpu_failure_falls_back_to_cpu(client: TestClient, monkeypatch) -> None:
    monkeypatch.setenv("LECTURELAB_FAKE_FAIL_DEVICE", "cuda")
    lid = _import_fixture(client)
    Worker().run_pending()
    lec = client.get(f"/api/lectures/{lid}").json()
    assert lec["status"] == "ready"
    assert lec["transcription"]["device"] == "cpu"
    assert "simulated cuda failure" in lec["transcription"]["gpu_fallback_reason"]


def test_total_failure_marks_stage_failed_but_keeps_audio(
    client: TestClient, settings, monkeypatch
) -> None:
    settings.transcription.device = "cuda"  # no CPU fallback configured
    monkeypatch.setenv("LECTURELAB_FAKE_FAIL_DEVICE", "cuda")
    lid = _import_fixture(client)
    Worker().run_pending()
    lec = client.get(f"/api/lectures/{lid}").json()
    assert lec["status"] == "failed" and lec["stages"]["transcribe"] == "failed"
    assert "simulated cuda failure" in lec["error"]
    assert lec["has_audio"]  # the recording itself is untouched


def test_auto_transcribe_can_be_disabled(settings: Settings) -> None:
    settings.transcription.auto = False
    with TestClient(create_app(settings, start_worker=False)) as c:
        lid = _import_fixture(c)
        assert Worker().run_pending() == 1
        assert c.get(f"/api/lectures/{lid}").json()["status"] == "ready"
        assert "transcribe" not in c.get(f"/api/lectures/{lid}").json()["stages"]


def test_attempt_order_and_hotwords() -> None:
    cfg = TranscriptionConfig()
    assert [a["device"] for a in attempts(cfg)] == ["cuda", "cpu"]
    cfg.device = "cpu"
    assert [a["device"] for a in attempts(cfg)] == ["cpu"]
    words = [f"term{i:03d}" for i in range(200)]
    hw = hotwords_for(words)
    assert len(hw) <= 600 and hw.startswith("term000, term001")


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower().replace("-", " "))


@pytest.mark.skipif(
    os.environ.get("LECTURELAB_TEST_WHISPER") != "1",
    reason="real Whisper test; set LECTURELAB_TEST_WHISPER=1 (needs the model downloaded)",
)
def test_real_whisper_on_fixture(tmp_path: Path) -> None:
    """Opt-in: transcribes the TTS fixture with the real model and checks word accuracy."""
    import difflib

    data_dir = Path(os.environ.get("LECTURELAB_DATA_DIR", Path.home() / "LectureLab"))
    settings = load_settings(
        tmp_path / "data",
        open_browser=False,
        tray=False,
        transcription={"models_dir": data_dir / "models"},
    )
    with TestClient(create_app(settings, start_worker=False)) as c:
        lid = _import_fixture(c)
        Worker().run_pending()
        lec = c.get(f"/api/lectures/{lid}").json()
        assert lec["status"] == "ready", lec
        text = " ".join(
            s["text"] for s in c.get(f"/api/lectures/{lid}/transcript").json()["segments"]
        )
    ref = _words((FIXTURES / "lecture.txt").read_text(encoding="utf-8"))
    hyp = _words(text)
    accuracy = difflib.SequenceMatcher(a=ref, b=hyp).ratio()
    print(f"\nword match {accuracy:.3f}; stats {lec['transcription']}")
    assert accuracy > 0.9


def _w(word: str, start: float, end: float) -> dict:
    return {"word": word, "start": start, "end": end, "p": 0.9}


def test_resegment_into_sentences() -> None:
    segs = [
        {
            "start": 0,
            "end": 3,
            "text": "",
            "words": [_w(" PCA", 0, 0.5), _w(" is", 0.5, 1), _w(" great,", 1, 1.5)],
        },
        {
            "start": 3,
            "end": 6,
            "text": "",
            "words": [_w(" really.", 1.6, 2.0), _w(" Next", 2.1, 2.5), _w(" topic", 2.5, 3)],
        },
        # long pause before the next word starts a new line
        {"start": 6, "end": 9, "text": "", "words": [_w(" k-means", 6, 6.5), _w(" now?", 6.5, 7)]},
        {"start": 9, "end": 12, "text": "Applause", "avg_logprob": -0.5, "words": []},
    ]
    lines = resegment(segs)
    assert [ln["text"] for ln in lines] == [
        "PCA is great, really.",
        "Next topic",
        "k-means now?",
        "Applause",
    ]
    assert lines[0]["start"] == 0 and lines[0]["end"] == 2.0 and lines[0]["confidence"] == 0.9
    assert lines[3]["confidence"] == round(math.exp(-0.5), 3)


def test_resegment_caps_line_length() -> None:
    words = [_w(f" w{i}", i * 0.5, i * 0.5 + 0.4) for i in range(120)]  # 60 s, no punctuation
    lines = resegment([{"start": 0, "end": 60, "text": "", "words": words}])
    assert len(lines) == 3 and all(ln["end"] - ln["start"] <= 25 for ln in lines)
