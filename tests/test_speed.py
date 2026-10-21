"""Faster processing: OCR lane next to the GPU, grouped slide pictures, batched Whisper."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

from backend.core import jobs
from backend.core.db import session_scope
from backend.core.models import Job, JobStatus
from backend.pipeline import ocr_worker, whisper_worker


def _job(kind: str, lecture_id: int | None, status: JobStatus = JobStatus.queued) -> int:
    with session_scope() as s:
        job = Job(kind=kind, lecture_id=lecture_id, status=status)
        s.add(job)
        s.commit()
        return job.id  # type: ignore[return-value]


def _lectures(c: TestClient) -> tuple[int, int]:
    from backend.core.library import create_lecture
    from backend.core.models import LectureSource, LectureStatus, Subject

    sid = c.post("/api/subjects", json={"name": "Maths"}).json()["id"]
    with session_scope() as s:
        subject = s.get(Subject, sid)
        a, b = (
            create_lecture(
                s, subject, source=LectureSource.import_, status=LectureStatus.processing
            )
            for _ in range(2)
        )
        s.commit()
        return a.id, b.id  # type: ignore[return-value]


def _next(lane: str) -> tuple[str, int | None] | None:
    with session_scope() as s:
        job = jobs.Worker(lane=lane)._next(s)
        return (job.kind, job.lecture_id) if job else None


def test_ocr_runs_beside_transcription_of_another_lecture(client: TestClient) -> None:
    one, two = _lectures(client)
    _job("transcribe", one, JobStatus.running)  # the GPU is on lecture 1
    _job("slides", one)
    _job("generate", one)
    _job("slides", two)
    _job("transcribe", two)
    _job("cleanup", two)
    # The side lane skips lecture 1 (busy) and reads lecture 2's slides.
    assert _next("side") == ("slides", two)
    # The main lane may transcribe lecture 2 before its slides are read, but nothing that
    # uses slide text (generate for 1, cleanup for 2) goes ahead of OCR.
    assert _next("main") == ("transcribe", two)
    with session_scope() as s:
        for job in s.exec(__import__("sqlmodel").select(Job)).all():
            if job.kind == "transcribe" and job.lecture_id == two:
                job.status = JobStatus.running
                s.add(job)
        s.commit()
    assert _next("side") is None  # both lectures are busy
    assert _next("main") is None  # generate 1 and cleanup 2 wait for their slide text
    # Jobs without a lecture never wait.
    _job("subject_hub", None)
    assert _next("main") == ("subject_hub", None)


def test_single_lane_keeps_the_old_order(client: TestClient) -> None:
    one, _ = _lectures(client)
    _job("slides", one)
    _job("transcribe", one)
    with session_scope() as s:
        job = jobs.Worker()._next(s)
        assert job is not None and job.kind == "slides"


def _slide(path: Path, title: str, ink: int = 0) -> str:
    im = Image.new("RGB", (1280, 720), "white")
    d = ImageDraw.Draw(im)
    d.rectangle((80, 60, 1200, 160), fill="navy")  # the title bar
    d.text((100, 90), title, fill="white")
    for k in range(ink):  # handwriting added during the lecture
        d.line((200 + k * 40, 400, 230 + k * 40, 440), fill="black", width=6)
    im.save(path)
    return str(path)


def test_slide_pictures_are_grouped(tmp_path: Path) -> None:
    a = _slide(tmp_path / "1.jpg", "Sets")
    a2 = _slide(tmp_path / "2.jpg", "Sets", ink=3)
    a3 = _slide(tmp_path / "3.jpg", "Sets", ink=6)
    other = tmp_path / "4.jpg"
    im = Image.new("RGB", (1280, 720), "black")
    ImageDraw.Draw(im).rectangle((100, 100, 1100, 600), fill="yellow")
    im.save(other)
    assert ocr_worker.runs([a, a2, a3, str(other), a]) == [[0, 1, 2], [3], [4]]


def test_batched_whisper_halves_the_batch_when_the_gpu_is_full(monkeypatch) -> None:
    tried: list[int] = []

    def fake(model, audio, options, batch):  # type: ignore[no-untyped-def]
        tried.append(batch)
        if batch > 2:
            raise RuntimeError("CUDA failed with error out of memory")

        class Info:
            language, language_probability, duration = "en", 0.99, 10.0

        return [], Info()

    monkeypatch.setattr(whisper_worker, "load_model", lambda args: object())
    monkeypatch.setattr(whisper_worker, "load_audio", lambda *a: [])
    monkeypatch.setattr(whisper_worker, "_transcribe", fake)
    out = whisper_worker.run_faster_whisper(
        {"device": "cuda", "audio": "x", "batch_size": 8, "model": "m", "compute_type": "x"}
    )
    assert tried == [8, 4, 2] and out["batch_size"] == 2
    tried.clear()
    out = whisper_worker.run_faster_whisper({"device": "cpu", "audio": "x", "batch_size": 8})
    assert tried == [0] and out["batch_size"] == 1  # batching is for the GPU only


@pytest.fixture(autouse=True)
def _no_running_workers() -> None:
    assert jobs.worker is None  # tests drive Worker() themselves


def test_claude_lane_runs_courses_side_by_side_but_not_one_course_twice(
    client: TestClient, monkeypatch
) -> None:
    """2026-10-21: one slow overview batch held up the next lecture's notes for 20+ minutes."""
    monkeypatch.setattr(jobs, "_remote_llm", lambda: True)
    one, _ = _lectures(client)
    with session_scope() as s:
        for kind, lecture, payload, status in (
            ("subject_hub", None, {"subject_id": 7}, JobStatus.running),  # waiting for a batch
            ("subject_hub", None, {"subject_id": 7}, JobStatus.queued),
            ("subject_hub", None, {"subject_id": 10}, JobStatus.queued),
            ("generate", one, {}, JobStatus.queued),
        ):
            s.add(Job(kind=kind, lecture_id=lecture, payload=payload, status=status))
        s.commit()
    with session_scope() as s:
        job = jobs.Worker(lane="llm")._next(s)
        assert job is not None and (job.kind, job.payload) == ("subject_hub", {"subject_id": 10})
        job.status = JobStatus.running
        s.add(job)
        s.commit()
    assert _next("llm") == ("generate", one)
