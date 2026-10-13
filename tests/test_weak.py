from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from backend.ai.providers.mock_provider import MockProvider
from backend.app import create_app
from backend.core.config import Settings
from backend.core.db import session_scope
from backend.core.jobs import Worker
from backend.core.models import Attempt, Flashcard, Lecture, Note, Question, Subject
from backend.study import review
from backend.study.weak import topic_stats

NOW = datetime.now(UTC)


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(settings, start_worker=False)) as c:
        yield c


def _setup(with_hub: bool = True) -> dict[str, int]:
    """A lecture with three note sections; cards and questions in the first two."""
    with session_scope() as s:
        sub = Subject(name="RL", slug="rl")
        s.add(sub)
        s.commit()
        lec = Lecture(
            subject_id=sub.id,  # type: ignore[arg-type]
            title="Bellman",
            started_at=datetime(2026, 10, 1, tzinfo=UTC),
            duration_s=3600,
            folder="subjects/rl/x",
        )
        s.add(lec)
        s.commit()
        sections = [
            {"heading": "Value functions", "refs": [{"start": "01:00", "end": "05:00"}]},
            {"heading": "Bellman equation", "refs": [{"start": "10:00", "end": "20:00"}]},
            {"heading": "Policy iteration", "refs": [{"start": "30:00", "end": "40:00"}]},
        ]
        md = "## Value functions [01:00]\nV(s)...\n## Bellman equation [10:00]\nV = r + γV'"
        s.add(
            Note(
                subject_id=sub.id,
                lecture_id=lec.id,
                kind="notes",
                content_md=md,
                data={"sections": sections},
            )
        )  # type: ignore[arg-type]

        def card(t: float, front: str) -> int:
            c = Flashcard(
                subject_id=sub.id,  # type: ignore[arg-type]
                lecture_id=lec.id,
                front=front,
                back="answer",
                source_refs=[{"lecture_id": lec.id, "start_s": t, "end_s": t}],
            )
            s.add(c)
            s.commit()
            return c.id  # type: ignore[return-value]

        def question(t: float) -> int:
            q = Question(
                subject_id=sub.id,  # type: ignore[arg-type]
                lecture_id=lec.id,
                prompt=f"Q at {t}",
                points=4,
                source_refs=[{"lecture_id": lec.id, "start_s": t, "end_s": t}],
            )
            s.add(q)
            s.commit()
            return q.id  # type: ignore[return-value]

        ids = {
            "subject": sub.id,
            "lecture": lec.id,
            "weak_card": card(700, "What is the Bellman equation?"),
            "good_card": card(120, "What is V(s)?"),
            "weak_q": question(800),
            "good_q": question(200),
        }
        if with_hub:
            topics = [
                {
                    "name": name,
                    "places": [{"lecture": 1, "sections": [name]}],
                    "exam_relevant": name == "Bellman equation",
                }
                for name in ("Value functions", "Bellman equation", "Policy iteration")
            ]
            s.add(
                Note(
                    subject_id=sub.id,
                    kind="course",
                    data={"lecture_ids": {"1": lec.id}, "topics": topics},
                )
            )  # type: ignore[arg-type]
        s.commit()
        # Bellman: forgotten 3 of 4 times, answer 1/4. Value functions: always good, 4/4.
        t = NOW - timedelta(days=10)
        for rating in (1, 1, 1, 3):
            review.review(s, ids["weak_card"], rating, now=t)
            t += timedelta(days=1)
        t = NOW - timedelta(days=10)
        for rating in (3, 3, 4):
            review.review(s, ids["good_card"], rating, now=t)
            t += timedelta(days=2)
        s.add(
            Attempt(
                question_id=ids["weak_q"],
                answer_text="V = r",
                score=1,
                max_points=4,
                feedback="Missing γ",
            )
        )
        s.add(Attempt(question_id=ids["good_q"], score=4, max_points=4))
        s.commit()
    return ids  # type: ignore[return-value]


def test_topic_strength_from_reviews_and_answers(client: TestClient) -> None:
    ids = _setup()
    with session_scope() as s:
        rows = topic_stats(s, ids["subject"])
    assert [(r["name"], r["status"]) for r in rows] == [
        ("Bellman equation", "weak"),
        ("Value functions", "ok"),
        ("Policy iteration", "untested"),
    ]
    weak, good, untested = rows
    assert weak["parts"]["recall"] == 0.25 and weak["parts"]["answers"] == 0.25
    assert weak["parts"]["memory"] < good["parts"]["memory"]  # lapses lower stability
    assert weak["strength"] < 0.6 < good["strength"]
    assert weak["forgotten"] == 3 and weak["reviews"] == 4
    assert weak["forgotten_cards"][0]["front"] == "What is the Bellman equation?"
    assert weak["wrong_answers"][0]["answer_text"] == "V = r"
    assert untested["strength"] is None and untested["parts"] == {}


def test_without_a_hub_each_lecture_is_a_topic(client: TestClient) -> None:
    ids = _setup(with_hub=False)
    with session_scope() as s:
        [row] = topic_stats(s, ids["subject"])
    assert row["name"] == "L1 Bellman" and row["reviews"] == 7 and row["answers"] == 2


def test_practice_my_weak_topics(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    ids = _setup()
    seen: dict[str, str] = {}
    original = MockProvider.complete

    def spy(self, req):  # type: ignore[no-untyped-def]
        seen[req.task] = req.instruction
        return original(self, req)

    monkeypatch.setattr(MockProvider, "complete", spy)
    overview = client.get("/api/weak-spots").json()
    assert [o["name"] for o in overview] == ["Bellman equation"]

    r = client.post(f"/api/subjects/{ids['subject']}/practice-sets", json={"n": 3})
    assert r.status_code == 201, r.text
    ps = r.json()
    assert ps["topics"] == ["Bellman equation"] and ps["status"] == "building"
    assert Worker().run_pending() == 1
    ps = client.get(f"/api/practice-sets/{ps['id']}").json()
    assert ps["status"] == "ready" and len(ps["question_ids"]) == 3

    prompt = seen["weak_practice"]
    assert "What is the Bellman equation? -> answer" in prompt  # forgotten card
    assert "Student: V = r (1/4)" in prompt  # wrong answer
    assert "Policy iteration" not in prompt.split("Don't repeat")[0]  # only the weak topic

    params = [("ids", i) for i in ps["question_ids"]]
    items = client.get("/api/practice/questions", params=params).json()
    assert [x["id"] for x in items] == ps["question_ids"]


def test_no_weak_topics_yet(client: TestClient) -> None:
    with session_scope() as s:
        sub = Subject(name="Empty", slug="empty")
        s.add(sub)
        s.commit()
        sid = sub.id
    r = client.post(f"/api/subjects/{sid}/practice-sets", json={})
    assert r.status_code == 409 and "No weak topics" in r.json()["detail"]
    assert client.get(f"/api/subjects/{sid}/weak-spots").json()["topics"] == []
