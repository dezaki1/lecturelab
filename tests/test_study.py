from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from backend.app import create_app
from backend.core.config import Settings
from backend.core.db import session_scope
from backend.core.models import Flashcard, Question, ReviewLog, Subject
from backend.study import practice, review

T0 = datetime(2026, 10, 8, 9, 0, tzinfo=UTC)


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(settings, start_worker=False)) as c:
        yield c


def _cards(n: int, subject: str = "ML") -> list[int]:
    with session_scope() as s:
        sub = s.exec(select(Subject).where(Subject.name == subject)).first()
        if sub is None:
            sub = Subject(name=subject, slug=subject.lower())
            s.add(sub)
            s.commit()
            s.refresh(sub)
        cards = [
            Flashcard(subject_id=sub.id, front=f"Q{i} {subject}", back=f"A{i}") for i in range(n)
        ]
        for c in cards:
            s.add(c)
        s.commit()
        return [c.id for c in cards]  # type: ignore[misc]


def test_new_card_learning_steps_and_due(client: TestClient) -> None:
    a, b = _cards(2)
    with session_scope() as s:
        assert review.next_card(s, now=T0).id == a  # new cards in order
        card = review.review(s, a, 3, now=T0)  # Good on a new card -> learning step
        assert card.fsrs and T0 < card.due <= T0 + timedelta(minutes=11)
        assert review.next_card(s, now=T0).id == b  # a not due yet
        review.review(s, b, 4, now=T0)  # Easy graduates straight to review
        assert review.next_card(s, now=T0 + timedelta(minutes=1)) is None
        assert review.next_card(s, now=T0 + timedelta(minutes=11)).id == a  # learning card back
        c = review.counts(s, now=T0 + timedelta(minutes=11))
        assert c["due"] == 1 and c["new"] == 0


def test_preview_intervals_are_ordered(client: TestClient) -> None:
    [a] = _cards(1)
    with session_scope() as s:
        review.review(s, a, 3, now=T0)
        review.review(s, a, 3, now=T0 + timedelta(minutes=10))  # graduates
        card = s.get(Flashcard, a)
        iv = review.preview_intervals(card, now=card.due)
    assert iv[1] < iv[2] <= iv[3] < iv[4]
    assert iv[4] > 86400  # easy: more than a day


def test_daily_new_limit_and_extra(client: TestClient, settings: Settings) -> None:
    settings.study.new_cards_per_day = 2
    _cards(5)
    now = datetime.now(UTC)
    with session_scope() as s:
        for _ in range(2):
            review.review(s, review.next_card(s, now=now).id, 4, now=now)
        assert review.next_card(s, now=now) is None
        assert review.counts(s, now=now) == {**review.counts(s, now=now), "new": 0, "new_total": 3}
    r = client.post("/api/review/extra-new", json={"count": 2})
    assert r.json()["new"] == 2
    assert client.get("/api/review/next").json()["card"] is not None


def test_suspended_and_retired_cards_are_skipped(client: TestClient) -> None:
    a, b, c = _cards(3)
    with session_scope() as s:
        s.get(Flashcard, a).suspended = True
        s.get(Flashcard, b).is_current = False
        s.commit()
        assert review.next_card(s, now=T0).id == c
    assert client.patch(f"/api/flashcards/{c}", json={"suspended": True}).json()["suspended"]
    assert client.get("/api/review/next").json()["card"] is None


def test_review_api_and_undo(client: TestClient) -> None:
    a, _ = _cards(2)
    nxt = client.get("/api/review/next").json()
    assert nxt["card"]["id"] == a and nxt["is_new"] and nxt["subject_name"] == "ML"
    assert set(nxt["intervals"]) == {"1", "2", "3", "4"}
    assert nxt["remaining"]["new"] == 2
    r = client.post(f"/api/review/{a}", json={"rating": 3, "duration_ms": 4200})
    assert r.status_code == 200 and r.json()["state"] == 1
    assert client.post(f"/api/review/{a}", json={"rating": 7}).status_code == 422
    assert client.get("/api/review/summary").json()["reviewed_today"] == 1
    assert client.post("/api/review/undo").json() == {"card_id": a}  # not captured by /{id}
    with session_scope() as s:
        card = s.get(Flashcard, a)
        assert card.fsrs == {} and card.due is None
        assert s.exec(select(ReviewLog)).all() == []
    assert client.post("/api/review/undo").status_code == 409


def test_streak(client: TestClient) -> None:
    [a] = _cards(1)
    today = review.local_day_start()
    with session_scope() as s:
        for days_ago in (0, 1, 2, 4):
            s.add(
                ReviewLog(
                    card_id=a,
                    rating=3,
                    reviewed_at=today + timedelta(hours=12) - timedelta(days=days_ago),
                )
            )
        s.commit()
        assert review.stats(s)["streak"] == 3


def test_reversed_cards_alternate(client: TestClient) -> None:
    [a] = _cards(1)
    with session_scope() as s:
        s.get(Flashcard, a).type = "reversed"
        s.commit()
    assert client.get("/api/review/next").json()["reverse"] is False
    client.post(f"/api/review/{a}", json={"rating": 1})
    with session_scope() as s:  # make it due again right away
        card = s.get(Flashcard, a)
        card.due = datetime.now(UTC) - timedelta(seconds=1)
        s.commit()
    assert client.get("/api/review/next").json()["reverse"] is True


# Practice -------------------------------------------------------------------------------------
def _question(**kw: object) -> int:
    with session_scope() as s:
        sub = s.exec(select(Subject)).first()
        if sub is None:
            sub = Subject(name="ML", slug="ml")
            s.add(sub)
            s.commit()
            s.refresh(sub)
        q = Question(subject_id=sub.id, prompt="?", **kw)
        s.add(q)
        s.commit()
        return q.id  # type: ignore[return-value]


def test_grading() -> None:
    opts = [
        {"text": t, "correct": c} for t, c in [("a", True), ("b", False), ("c", True), ("d", False)]
    ]
    multi = Question(subject_id=1, prompt="?", type="multi", points=4, options=opts)
    assert practice.grade_choice(multi, [0, 2]) == (True, 4)
    assert practice.grade_choice(multi, [0]) == (False, 2)
    assert practice.grade_choice(multi, [0, 1]) == (False, 0)  # wrong pick cancels a right one
    mcq = Question(subject_id=1, prompt="?", type="mcq", points=2, options=opts[:2])
    assert practice.grade_choice(mcq, [0]) == (True, 2) and practice.grade_choice(mcq, [1]) == (
        False,
        0,
    )


def test_practice_api(client: TestClient) -> None:
    opts = [{"text": "x", "correct": False}, {"text": "y", "correct": True}]
    mcq = _question(type="mcq", points=2, options=opts)
    rubric = [{"criterion": "defines", "points": 1}, {"criterion": "example", "points": 2}]
    open_q = _question(type="long", points=3, rubric=rubric, answer="model answer")
    _question(type="short", points=1, kind="exercise")

    picked = client.get("/api/practice/questions", params={"limit": 10}).json()
    assert {p["id"] for p in picked} == {mcq, open_q}  # exam questions only
    assert picked[0]["question"]["prompt"] == "?"

    r = client.post(f"/api/practice/questions/{mcq}/attempts", json={"selected": [0]}).json()
    assert r["correct"] is False and r["score"] == 0 and r["correct_options"] == [1]
    assert client.post(f"/api/practice/questions/{mcq}/attempts", json={}).status_code == 422
    r = client.post(
        f"/api/practice/questions/{open_q}/attempts",
        json={"answer_text": "my answer", "rubric_checked": [1]},
    ).json()
    assert r == {**r, "score": 2, "max_points": 3, "correct": False, "auto_graded": False}
    r = client.post(f"/api/practice/questions/{open_q}/attempts", json={"self_score": 99}).json()
    assert r["score"] == 3 and r["correct"] is True  # clamped to the question's points

    weakest = client.get("/api/practice/questions", params={"mode": "weakest"}).json()
    assert weakest[0]["id"] == mcq and weakest[0]["last_score"] == 0  # failed one first
    stats = client.get("/api/practice/stats").json()["by_subject"][0]
    assert stats["attempts"] == 3 and stats["score"] == 5
