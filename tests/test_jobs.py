from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.core.config import Settings
from backend.core.db import session_scope
from backend.core.events import Event, bus
from backend.core.jobs import Worker
from backend.core.models import UsageRecord
from tests.fakes import fake_factory

FIXTURES = Path(__file__).parent / "fixtures"
pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not on PATH")


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(settings, start_worker=False, source_factory=fake_factory())) as c:
        yield c


@pytest.fixture
def notes() -> Iterator[list[dict]]:
    seen: list[dict] = []

    def listen(evt: Event) -> None:
        if evt.type == "notify":
            seen.append(evt.data)

    bus.add_listener(listen)
    yield seen
    bus.remove_listener(listen)


def _import(c: TestClient, subject: str = "ML") -> tuple[int, int]:
    r = c.post("/api/subjects", json={"name": subject})
    sid = r.json()["id"]
    lid = c.post(
        "/api/imports",
        params={"subject_id": sid, "filename": "lecture.m4a"},
        content=(FIXTURES / "lecture.m4a").read_bytes(),
    ).json()["id"]
    return sid, lid


def test_one_ready_notification_per_lecture(client: TestClient, settings: Settings, notes) -> None:
    _, lid = _import(client)
    Worker().run_pending()  # import, transcribe, index (auto-generation off)
    assert [n["title"] for n in notes] == ["Lecture ready"] and notes[0]["lecture_id"] == lid

    notes.clear()
    settings.generation.auto = True
    _, lid2 = _import(client, "NLP")
    Worker().run_pending()  # import, transcribe, generate, index
    assert [n["title"] for n in notes] == ["Lecture ready"] and notes[0]["lecture_id"] == lid2


def test_job_list_retry_and_failure_notice(
    client: TestClient, settings: Settings, notes, monkeypatch
) -> None:
    settings.transcription.device = "cuda"
    monkeypatch.setenv("LECTURELAB_FAKE_FAIL_DEVICE", "cuda")
    _, lid = _import(client)
    Worker().run_pending()
    failed = client.get("/api/jobs", params={"status": "failed"}).json()
    assert len(failed) == 1 and failed[0]["label"] == "Transcription"
    assert failed[0]["lecture_title"] == "lecture" and failed[0]["subject_name"] == "ML"
    assert notes[-1]["title"] == "Transcription failed" and notes[-1]["level"] == "error"
    assert client.get("/api/jobs/counts").json()["failed_recent"] == 1

    monkeypatch.delenv("LECTURELAB_FAKE_FAIL_DEVICE")
    r = client.post(f"/api/jobs/{failed[0]['id']}/retry")
    assert (
        r.status_code == 200 and client.get(f"/api/lectures/{lid}").json()["status"] == "processing"
    )
    assert client.post(f"/api/jobs/{failed[0]['id']}/retry").status_code == 409  # already queued
    Worker().run_pending()
    lec = client.get(f"/api/lectures/{lid}").json()
    assert lec["status"] == "ready" and lec["stages"]["transcribe"] == "done"
    all_jobs = client.get("/api/jobs").json()
    assert {j["status"] for j in all_jobs} == {"done"}
    assert all(j["duration_s"] is not None for j in all_jobs)


def test_cancel_queued_job(client: TestClient) -> None:
    _, lid = _import(client)  # import job queued, not run
    [job] = client.get("/api/jobs", params={"status": "active"}).json()
    assert client.post(f"/api/jobs/{job['id']}/cancel").json()["status"] == "cancelled"
    assert client.post(f"/api/jobs/{job['id']}/cancel").status_code == 409
    assert Worker().run_pending() == 0
    assert client.get(f"/api/lectures/{lid}").json()["status"] == "failed"  # no audio yet


def test_usage_summary(client: TestClient) -> None:
    sid, lid = _import(client)
    with session_scope() as s:
        for task, model, cost, read in [
            ("notes", "claude-sonnet-5-5", 0.02, 20_000),
            ("summary", "claude-sonnet-5-5", 0.05, 0),
            ("verify", "claude-haiku-5-5", 0.001, 2_000),
            ("chat", "claude-sonnet-5-5", 0.01, 0),
        ]:
            s.add(
                UsageRecord(
                    lecture_id=lid if task != "chat" else None,
                    subject_id=sid,
                    task=task,
                    provider="anthropic",
                    model=model,
                    cache_read_tokens=read,
                    cost_usd=cost,
                )
            )
        s.commit()
    u = client.get("/api/usage", params={"days": 7}).json()
    assert u["total_cost"] == pytest.approx(0.081) and u["calls"] == 4
    assert [t["task"] for t in u["by_task"]][:2] == ["summary", "notes"]
    assert u["by_model"][0]["model"] == "claude-sonnet-5-5" and u["by_model"][0]["calls"] == 3
    assert u["by_lecture"][0] == {**u["by_lecture"][0], "lecture_id": lid, "number": 1, "calls": 3}
    assert u["by_lecture"][0]["cost"] == pytest.approx(0.071)
    assert u["by_subject"][0]["name"] == "ML" and len(u["by_day"]) == 7
    # 20k Sonnet cache reads at (2.00 - 0.20) $/M + 2k Haiku at (0.10 - 0.01) $/M
    assert u["cache_savings"] == pytest.approx(0.036 + 0.00018, abs=1e-4)


def test_batch_generation(client: TestClient) -> None:
    _, lid = _import(client)
    Worker().run_pending()
    pending = client.get("/api/generate/pending").json()
    assert pending["count"] == 1 and pending["lectures"][0]["id"] == lid
    assert client.post("/api/generate/pending").json() == {"queued": 1}
    assert client.post("/api/generate/pending").json() == {"queued": 0}  # already queued
    Worker().run_pending()
    assert client.get(f"/api/lectures/{lid}").json()["stages"]["generate"] == "done"
    assert client.get("/api/generate/pending").json()["count"] == 0
