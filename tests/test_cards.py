from __future__ import annotations

import csv
import io
import sqlite3
import zipfile
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from backend.app import create_app
from backend.core.config import Settings
from backend.core.db import session_scope
from backend.core.models import Flashcard, ReviewLog, Subject
from backend.exports.cards import md_to_html
from backend.study import review


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(settings, start_worker=False)) as c:
        yield c


def _subject(name: str = "Machine Learning", exam: date | None = None) -> int:
    with session_scope() as s:
        sub = Subject(name=name, slug=name.lower().replace(" ", "-"), exam_date=exam)
        s.add(sub)
        s.commit()
        return sub.id  # type: ignore[return-value]


def _card(sid: int, front: str, back: str, **kw: object) -> int:
    with session_scope() as s:
        c = Flashcard(subject_id=sid, front=front, back=back, **kw)  # type: ignore[arg-type]
        s.add(c)
        s.commit()
        return c.id  # type: ignore[return-value]


def test_browse_filters_states_and_counts(client: TestClient) -> None:
    sid = _subject()
    a = _card(sid, "What is PCA?", "A projection onto directions of maximum variance.")
    b = _card(sid, "What does k-means minimise?", "Within-cluster squared distances.")
    _card(sid, "Old", "retired", is_current=False)
    c = _card(sid, "Suspended one", "x", suspended=True)
    now = datetime.now(UTC)
    with session_scope() as s:
        review.review(s, a, 1, now=now - timedelta(hours=2))  # Again, then learning
        review.review(s, a, 3, now=now - timedelta(hours=1))

    r = client.get("/api/cards").json()
    assert r["total"] == 3  # retired card hidden
    by_id = {x["id"]: x for x in r["items"]}
    assert by_id[a]["state"] in ("learning", "review") and by_id[a]["reps"] == 2
    assert by_id[a]["lapses"] == 1 and by_id[b]["state"] == "new"
    assert by_id[c]["state"] == "suspended" and "fsrs" not in by_id[a]
    assert by_id[a]["subject_name"] == "Machine Learning"

    assert [x["id"] for x in client.get("/api/cards?state=new").json()["items"]] == [b]
    assert [x["id"] for x in client.get("/api/cards?state=suspended").json()["items"]] == [c]
    assert [x["id"] for x in client.get("/api/cards?q=k-means").json()["items"]] == [b]
    assert client.get("/api/cards?sort=lapses").json()["items"][0]["id"] == a
    assert client.get("/api/cards?state=bogus").status_code == 422


def test_bulk_actions_and_edit_tags(client: TestClient) -> None:
    sid = _subject()
    ids = [_card(sid, f"Q{i}", f"A{i}") for i in range(3)]
    assert client.post("/api/cards/bulk", json={"ids": ids[:2], "action": "suspend"}).json() == {
        "changed": 2
    }
    assert client.get("/api/cards?state=suspended").json()["total"] == 2
    client.post("/api/cards/bulk", json={"ids": ids[:1], "action": "unsuspend"})
    client.post("/api/cards/bulk", json={"ids": ids[2:], "action": "delete"})
    assert client.get("/api/cards").json()["total"] == 2

    r = client.patch(f"/api/flashcards/{ids[0]}", json={"tags": [" pca ", "exam", "pca"]})
    assert r.json()["tags"] == ["exam", "pca"] and r.json()["edited_by_me"]


def test_duplicates_are_found_and_merged(client: TestClient) -> None:
    sid = _subject()
    other = _subject("NLP")
    q, ans = "What are the principal components?", "Eigenvectors of the covariance matrix"
    ref1 = {"lecture_id": 1, "start_s": 10, "end_s": 12}
    ref2 = {"lecture_id": 2, "start_s": 50, "end_s": 55}
    a = _card(sid, q, ans, tags=["pca"], source_refs=[ref1])
    b = _card(sid, q.replace("?", " ?"), f"The {ans.lower()}.", tags=["exam"], source_refs=[ref2])
    _card(sid, "What is a kernel?", "A similarity function")
    _card(other, "What are the principal components?", "Eigenvectors of the covariance matrix")
    now = datetime.now(UTC)
    with session_scope() as s:  # b has the review history, so its schedule should win
        review.review(s, b, 4, now=now)
        b_due = s.get(Flashcard, b).due  # type: ignore[union-attr]

    groups = client.get("/api/cards/duplicates").json()
    assert [[c["id"] for c in g] for g in groups] == [[a, b]]  # other subject not mixed in
    assert client.get(f"/api/cards/duplicates?subject_id={sid}").json() == groups

    merged = client.post("/api/cards/merge", json={"keep_id": a, "merge_ids": [b]}).json()
    assert merged["id"] == a and sorted(merged["tags"]) == ["exam", "pca"]
    assert len(merged["source_refs"]) == 2 and merged["reps"] == 1
    with session_scope() as s:
        card = s.get(Flashcard, a)
        assert card.due == b_due and s.get(Flashcard, b) is None  # type: ignore[union-attr]
        assert s.exec(select(ReviewLog).where(ReviewLog.card_id == a)).first() is not None
    assert client.get("/api/cards/duplicates").json() == []


def test_md_to_html_for_anki() -> None:
    html = md_to_html("**Bold** and *it* with $x^2 < y$ and `a<b`\nline")
    assert html == r"<b>Bold</b> and <i>it</i> with \(x^2 &lt; y\) and <code>a&lt;b</code><br>line"
    assert md_to_html("$$\\sum_i x_i$$") == r"\[\sum_i x_i\]"
    assert md_to_html("{{c1::eigenvectors}} of C") == "{{c1::eigenvectors}} of C"


def test_export_apkg_and_csv(client: TestClient, tmp_path) -> None:  # type: ignore[no-untyped-def]
    sid = _subject()
    _card(sid, "What is PCA?", "Maximum *variance*", tags=["unsupervised learning"])
    _card(sid, "{{c1::Eigenvectors}} are the principal components", "", type="cloze")
    _card(sid, "Bellman", "Equation", type="reversed")
    _card(sid, "Hidden", "x", suspended=True)

    r = client.get("/api/cards/export?format=csv")
    assert r.status_code == 200 and r.headers["x-card-count"] == "3"
    rows = list(csv.DictReader(io.StringIO(r.content.decode("utf-8-sig"))))
    assert rows[0]["front"] == "What is PCA?" and rows[0]["tags"] == "unsupervised_learning"

    r = client.get("/api/cards/export?format=apkg&include_suspended=true")
    assert r.status_code == 200 and r.headers["x-card-count"] == "4"
    assert "attachment" in r.headers["content-disposition"]
    apkg = tmp_path / "cards.apkg"
    apkg.write_bytes(r.content)
    with zipfile.ZipFile(apkg) as z:
        z.extract("collection.anki2", tmp_path)
    db = sqlite3.connect(tmp_path / "collection.anki2")
    notes = db.execute("select flds, tags from notes").fetchall()
    cards = db.execute("select count(*) from cards").fetchone()[0]
    db.close()
    assert len(notes) == 4 and cards == 5  # the reversed note makes two cards
    assert any("Maximum <i>variance</i>" in f for f, _ in notes)
    assert any("unsupervised_learning" in t for _, t in notes)

    r = client.get("/api/cards/export?format=csv&state=new&q=nothing-matches")
    assert r.status_code == 404


def test_dashboard(client: TestClient) -> None:
    soon = date.today() + timedelta(days=12)
    ml = _subject("Machine Learning", exam=soon)
    _subject("NLP")
    a = _card(ml, "Q1", "A1")
    _card(ml, "Q2", "A2")
    now = datetime.now(UTC)
    with session_scope() as s:
        review.review(s, a, 4, now=now - timedelta(days=60))  # graduates to review
        review.review(s, a, 3, now=now)  # overdue by now: a review of a mature-state card

    d = client.get("/api/dashboard").json()
    assert d["streak"] >= 1 and len(d["activity"]) == 30 and len(d["forecast"]) == 7
    assert d["retention"]["reviews"] == 1 and d["retention"]["rate"] == 1.0
    ml_row = d["subjects"][0]
    assert ml_row["name"] == "Machine Learning" and ml_row["days_left"] == 12
    assert ml_row["cards"] == 2 and ml_row["new"] == 1
    assert 0 < ml_row["mastery"] <= 0.5  # one card well known, one never seen
    assert d["subjects"][1]["mastery"] is None  # NLP: no cards
