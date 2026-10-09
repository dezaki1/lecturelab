from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from backend.app import create_app
from backend.core.config import Settings
from backend.core.db import session_scope
from backend.core.jobs import Worker
from backend.core.models import Job, UsageRecord
from backend.pipeline.cleanup import accept
from tests.fakes import fake_factory

FIXTURES = Path(__file__).parent / "fixtures"
pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not on PATH")


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    settings.generation.auto = True
    settings.generation.cleanup = True
    with TestClient(create_app(settings, start_worker=False, source_factory=fake_factory())) as c:
        yield c


def _import(c: TestClient, vocabulary: list[str]) -> int:
    sid = c.post("/api/subjects", json={"name": "ML", "vocabulary": vocabulary}).json()["id"]
    r = c.post(
        "/api/imports",
        params={"subject_id": sid, "filename": "lecture.m4a"},
        content=(FIXTURES / "lecture.m4a").read_bytes(),
    )
    return r.json()["id"]


def test_accept_only_spelling_fixes() -> None:
    assert accept("the eigen vectors of C", "the eigenvectors of C")
    assert accept("we use numb pie arrays", "we use NumPy arrays")
    assert not accept("the eigen vectors of C", "the eigen vectors of C")  # unchanged
    assert not accept("so yeah let's go on", "We now proceed to the next topic, PCA.")  # rewrite
    assert not accept("a short line", "")
    assert not accept("PCA", "principal component analysis, which reduces dimensionality")


def test_cleanup_runs_before_generation_and_can_be_reverted(client: TestClient) -> None:
    lid = _import(client, ["PlaceHolder"])
    # import -> transcribe -> cleanup -> generate -> index
    assert Worker().run_pending() == 5
    with session_scope() as s:
        kinds = [j.kind for j in s.exec(select(Job).order_by(Job.id)).all()]  # type: ignore[arg-type]
        assert kinds == ["import_media", "transcribe", "cleanup", "generate", "index"]
        assert s.exec(select(UsageRecord).where(UsageRecord.task == "cleanup")).first()

    lec = client.get(f"/api/lectures/{lid}").json()
    assert lec["status"] == "ready"
    assert lec["stages"]["cleanup"] == "done" and lec["stages"]["generate"] == "done"
    assert lec["cleanup"]["changed"] == 23 and lec["cleanup"]["prompt_version"] == "cleanup@v1"

    segs = client.get(f"/api/lectures/{lid}/transcript").json()["segments"]
    # Every placeholder line was respelled; the bogus line and the full rewrite were rejected.
    assert segs[0]["text"] == "This is PlaceHolder segment 1."
    assert segs[0]["raw_text"] == "This is placeholder segment 1."
    assert all(s["raw_text"] for s in segs)

    # Revert one line, then run cleanup again: Whisper's original stays the reference.
    r = client.post(f"/api/lectures/{lid}/cleanup/revert", json={"segment_ids": [segs[1]["id"]]})
    assert r.json() == {"reverted": 1}
    segs = client.get(f"/api/lectures/{lid}/transcript").json()["segments"]
    assert segs[1]["text"] == "This is placeholder segment 2." and segs[1]["raw_text"] is None

    assert client.post(f"/api/lectures/{lid}/cleanup").status_code == 202
    assert client.post(f"/api/lectures/{lid}/cleanup").status_code == 409  # already queued
    assert Worker().run_pending() == 3  # reindex after the revert, cleanup, reindex (no regen)
    segs = client.get(f"/api/lectures/{lid}/transcript").json()["segments"]
    assert segs[1]["text"] == "This is PlaceHolder segment 2."
    assert segs[0]["raw_text"] == "This is placeholder segment 1."

    assert client.post(f"/api/lectures/{lid}/cleanup/revert", json={}).json() == {"reverted": 23}
    segs = client.get(f"/api/lectures/{lid}/transcript").json()["segments"]
    assert all(s["raw_text"] is None and "placeholder" in s["text"] for s in segs)


def test_cleanup_failure_does_not_block_generation(client: TestClient, monkeypatch) -> None:
    from backend.ai.providers.mock_provider import MockProvider

    def boom(self, lines, req):  # type: ignore[no-untyped-def]
        raise RuntimeError("model overloaded")

    monkeypatch.setattr(MockProvider, "_CleanupOut", boom)
    lid = _import(client, [])
    Worker().run_pending()
    lec = client.get(f"/api/lectures/{lid}").json()
    assert lec["stages"]["cleanup"] == "failed" and "overloaded" in lec["cleanup_error"]
    assert lec["stages"]["generate"] == "done" and lec["status"] == "ready"


def test_no_cleanup_with_local_models_by_default(settings: Settings) -> None:
    from backend.pipeline.cleanup import cleanup_enabled

    settings.generation.cleanup = True
    from backend.core.config import set_settings

    set_settings(settings)
    assert cleanup_enabled("anthropic") and not cleanup_enabled("ollama")
    settings.generation.cleanup_local = True
    assert cleanup_enabled("ollama")
