from __future__ import annotations

import shutil
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from backend.app import create_app
from backend.core.config import Settings
from backend.core.db import session_scope
from backend.core.jobs import Worker
from backend.core.models import Flashcard, Lecture, Note, Subject
from backend.study import review
from backend.study.topics import topics_with_mastery
from tests.fakes import fake_factory
from tests.pdfgen import make_pdf

FIXTURES = Path(__file__).parent / "fixtures"
pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not on PATH")


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    settings.generation.auto = True
    settings.generation.subject_hub = True
    with TestClient(create_app(settings, start_worker=False, source_factory=fake_factory())) as c:
        yield c


def _import(c: TestClient, sid: int, name: str) -> int:
    r = c.post(
        "/api/imports",
        params={"subject_id": sid, "filename": name},
        content=(FIXTURES / "lecture.m4a").read_bytes(),
    )
    return r.json()["id"]


def test_hub_updates_after_each_lecture(client: TestClient, tmp_path: Path) -> None:
    sid = client.post("/api/subjects", json={"name": "ML"}).json()["id"]
    _import(client, sid, "PCA.m4a")
    # import, transcribe, generate, index, course overview
    assert Worker().run_pending() == 5
    hub = client.get(f"/api/subjects/{sid}/hub").json()
    assert hub["overview"]["lectures_covered"] == 1 and not hub["outdated"]

    _import(client, sid, "Kernels.m4a")
    Worker().run_pending()
    hub = client.get(f"/api/subjects/{sid}/hub").json()
    ov = hub["overview"]
    assert ov["version"] == 2 and ov["lectures_covered"] == 2
    assert "Mock overview of 2 lecture(s)" in ov["content_md"] and "**L2" in ov["content_md"]

    topics = hub["topics"]
    assert len(topics) == 6 and topics[0]["exam_relevant"]
    first = topics[0]["places"][0]
    assert first["number"] == 1 and first["sections"] == ["Topic 1"] and first["start_s"] >= 0
    assert all(t["cards"] <= 5 for t in topics)

    cmap = hub["concept_map"]
    assert len(cmap["nodes"]) == 2 and len(cmap["edges"]) == 1
    assert all(n["lecture_id"] for n in cmap["nodes"])

    [term] = hub["glossary"]  # same term in both lectures, merged
    assert term["german"] == "Hauptkomponente" and [x["number"] for x in term["lectures"]] == [1, 2]

    assert client.post(f"/api/subjects/{sid}/hub").json() == {"queued": True}
    assert client.post(f"/api/subjects/{sid}/hub").json() == {"queued": False}  # one waiting
    assert client.get(f"/api/subjects/{sid}/hub").json()["updating"] is True
    jobs = client.get("/api/jobs?status=active").json()
    assert jobs[0]["label"] == "Course overview" and jobs[0]["subject_name"] == "ML"
    Worker().run_pending()

    # Subject-level PDF: text extracted, listed with the subject's materials.
    pdf = make_pdf(tmp_path / "script.pdf", [["Script", "Chapter 1"], ["Chapter 2"]])
    r = client.post(
        f"/api/subjects/{sid}/documents",
        params={"filename": "Script.pdf"},
        content=pdf.read_bytes(),
    )
    assert r.status_code == 201
    Worker().run_pending()
    [doc] = client.get(f"/api/subjects/{sid}/documents").json()
    assert doc["pages"] == 2 and doc["lecture_id"] is None and doc["placed"] == 0


def test_topic_mastery_maps_cards_by_section_time(client: TestClient) -> None:
    with session_scope() as s:
        sub = Subject(name="RL", slug="rl")
        s.add(sub)
        s.commit()
        lec = Lecture(
            subject_id=sub.id,  # type: ignore[arg-type]
            title="Bellman",
            started_at=datetime(2026, 10, 1, tzinfo=UTC),
            folder="subjects/rl/x",
        )
        s.add(lec)
        s.commit()
        sections = [
            {"heading": "Value functions", "refs": [{"start": "01:00", "end": "05:00"}]},
            {"heading": "Bellman equation", "refs": [{"start": "10:00", "end": "20:00"}]},
        ]
        s.add(Note(subject_id=sub.id, lecture_id=lec.id, kind="notes", data={"sections": sections}))  # type: ignore[arg-type]

        def card(start: float) -> Flashcard:
            c = Flashcard(
                subject_id=sub.id,  # type: ignore[arg-type]
                lecture_id=lec.id,
                front=f"Q at {start}",
                source_refs=[{"lecture_id": lec.id, "start_s": start, "end_s": start}],
            )
            s.add(c)
            return c

        inside_a, inside_b, outside = card(120), card(700), card(1500)
        hub = Note(
            subject_id=sub.id,  # type: ignore[arg-type]
            kind="course",
            data={
                "lecture_ids": {"1": lec.id},
                "topics": [
                    {"name": "Values", "places": [{"lecture": 1, "sections": ["Value functions"]}]},
                    # A slightly different heading still matches; unknown lectures are ignored.
                    {
                        "name": "Bellman",
                        "places": [
                            {"lecture": 1, "sections": ["The Bellman equation"]},
                            {"lecture": 9, "sections": ["x"]},
                        ],
                    },
                ],
            },
        )
        s.add(hub)
        s.commit()
        review.review(s, inside_b.id, 4)  # type: ignore[arg-type]
        topics = topics_with_mastery(s, sub.id, hub)  # type: ignore[arg-type]
        ids = (inside_a.id, inside_b.id, outside.id)

    values, bellman = topics
    assert values["card_ids"] == [ids[0]] and values["mastery"] == 0.0 and values["new"] == 1
    assert bellman["card_ids"] == [ids[1]] and bellman["mastery"] > 0.9
    assert len(bellman["places"]) == 1 and bellman["places"][0]["start_s"] == 600
    with session_scope() as s:
        assert s.exec(select(Flashcard).where(Flashcard.id == ids[2])).first() is not None
