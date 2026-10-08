from __future__ import annotations

import json
import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlmodel import select

from backend.app import create_app
from backend.core.config import Settings
from backend.core.db import session_scope
from backend.core.jobs import Worker
from backend.core.models import Chunk, Flashcard, Note, TranscriptSegment
from backend.search import index as idx
from backend.search.chat import Prepared, extract_citations
from backend.search.embeddings import EmbeddingError
from backend.search.retrieve import fts_query, short_name
from tests.fakes import fake_factory

FIXTURES = Path(__file__).parent / "fixtures"
pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not on PATH")


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(settings, start_worker=False, source_factory=fake_factory())) as c:
        yield c


def _lecture(c: TestClient, subject: str = "Machine Learning") -> tuple[int, int]:
    r = c.post("/api/subjects", json={"name": subject})
    sid = (
        r.json()["id"]
        if r.status_code == 201
        else next(s["id"] for s in c.get("/api/subjects").json() if s["name"] == subject)
    )
    lid = c.post(
        "/api/imports",
        params={"subject_id": sid, "filename": "lecture.m4a"},
        content=(FIXTURES / "lecture.m4a").read_bytes(),
    ).json()["id"]
    assert Worker().run_pending() == 3  # import, transcribe (fake engine), index
    return sid, lid


def _sse(body: str) -> list[tuple[str, dict]]:
    events = []
    for block in body.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines() if ": " in line)
        events.append((lines["event"], json.loads(lines["data"])))
    return events


# Chunking -------------------------------------------------------------------------------------
def _seg(start: float, end: float, t: str = "x") -> TranscriptSegment:
    return TranscriptSegment(lecture_id=1, idx=0, start=start, end=end, text=t)


def test_transcript_chunks_are_60_to_90_seconds() -> None:
    segs = [_seg(i * 7.0, i * 7.0 + 6.5, f"s{i}") for i in range(40)]  # 280 s of speech
    chunks = idx.transcript_chunks(segs, 60, 90)
    lengths = [c["end_s"] - c["start_s"] for c in chunks]
    assert all(55 <= n <= 90 for n in lengths[:-1]), lengths
    assert chunks[0]["start_s"] == 0 and chunks[-1]["end_s"] == segs[-1].end
    assert " ".join(c["text"] for c in chunks).split() == [s.text for s in segs]


def test_short_tail_joins_previous_chunk() -> None:
    segs = [_seg(i * 10.0, i * 10.0 + 9, f"s{i}") for i in range(7)] + [_seg(70, 75, "tail")]
    chunks = idx.transcript_chunks(segs, 60, 90)
    assert len(chunks) == 1 and chunks[0]["text"].endswith("tail")


def test_note_chunks_split_sections_with_timestamps() -> None:
    note = Note(
        subject_id=1,
        lecture_id=1,
        kind="notes",
        content_md=(
            "## ★ PCA steps [00:30]\n\nCenter the data.\n\n## Example [01:14-01:30]\n\nHeight."
        ),
    )
    chunks = idx.note_chunks(note)
    assert [(c["heading"], c["start_s"], c["text"]) for c in chunks] == [
        ("PCA steps", 30, "Center the data."),
        ("Example", 74, "Height."),
    ]


def test_helpers() -> None:
    assert fts_query('eigen-vectors "PCA"') == '"eigen" OR "vectors" OR "pca"*'
    assert fts_query("?!") is None
    assert short_name("Natural Language Processing") == "NLP"
    assert short_name("Mathematics for AI III") == "MfAI III"


# Indexing & search ----------------------------------------------------------------------------
def test_index_and_hybrid_search(client: TestClient) -> None:
    sid, lid = _lecture(client)
    other_sid, _ = _lecture(client, "Formal Models in AI")
    with session_scope() as s:
        chunks = s.exec(select(Chunk).where(Chunk.lecture_id == lid)).all()
        assert chunks and all(c.embedded for c in chunks)
        fts_rows = s.connection().execute(text("SELECT count(*) FROM chunk_fts")).scalar()
        vec_rows = s.connection().execute(text("SELECT count(*) FROM chunk_vec")).scalar()
    assert fts_rows == vec_rows == 2 * len(chunks)
    lec = client.get(f"/api/lectures/{lid}").json()
    assert lec["stages"]["index"] == "done"

    hits = client.get("/api/search", params={"q": "placeholder segment 7"}).json()
    assert hits and {h["subject_id"] for h in hits} == {sid, other_sid}
    top = hits[0]
    assert top["keyword_rank"] and top["semantic_rank"]  # found by both rankers
    assert "<<" in top["snippet"] and top["lecture_number"] == 1
    scoped = client.get("/api/search", params={"q": "placeholder", "subject_id": sid}).json()
    assert scoped and {h["subject_id"] for h in scoped} == {sid}


def test_search_falls_back_to_keywords(client: TestClient, monkeypatch) -> None:
    _lecture(client)

    def down(texts: list[str]) -> list[list[float]]:
        raise EmbeddingError("Ollama is not reachable")

    monkeypatch.setattr("backend.search.retrieve.embed", down)
    hits = client.get("/api/search", params={"q": "placeholder"}).json()
    assert hits and all(h["semantic_rank"] is None for h in hits)


def test_reindex_is_idempotent_and_delete_cleans_up(client: TestClient) -> None:
    _, lid = _lecture(client)
    before = idx.index_lecture(lid)
    again = idx.index_lecture(lid)
    assert before["chunks"] == again["chunks"] and again["semantic"]
    with session_scope() as s:
        assert (
            s.connection().execute(text("SELECT count(*) FROM chunk_fts")).scalar()
            == again["chunks"]
        )
    assert client.delete(f"/api/lectures/{lid}").status_code == 204
    with session_scope() as s:
        conn = s.connection()
        assert conn.execute(text("SELECT count(*) FROM chunk_fts")).scalar() == 0
        assert conn.execute(text("SELECT count(*) FROM chunk_vec")).scalar() == 0


# Chat -----------------------------------------------------------------------------------------
def test_subject_chat_streams_and_cites(client: TestClient) -> None:
    sid, lid = _lecture(client)
    t = client.post("/api/chat/threads", json={"scope": "subject", "subject_id": sid}).json()
    r = client.post(f"/api/chat/threads/{t['id']}/messages", json={"content": "What is segment 3?"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    events = _sse(r.text)
    kinds = [e for e, _ in events]
    assert kinds[0] == "user" and kinds[1] == "sources" and kinds[-1] == "done"
    assert kinds.count("delta") > 3
    sources = events[1][1]
    assert sources and sources[0]["label"].startswith("L1 · ")
    done = events[-1][1]
    assert done["citations"] and done["citations"][0]["lecture_id"] == lid
    assert done["citations"][0]["label"] == sources[0]["label"]

    # follow-up goes through query rewriting and keeps history
    r2 = client.post(f"/api/chat/threads/{t['id']}/messages", json={"content": "And segment 4?"})
    assert _sse(r2.text)[-1][0] == "done"
    thread = client.get(f"/api/chat/threads/{t['id']}").json()
    assert [m["role"] for m in thread["messages"]] == ["user", "assistant"] * 2
    assert thread["title"] == "What is segment 3?"
    assert any(x["subject_id"] == sid for x in client.get("/api/chat/threads").json())


def test_lecture_chat_uses_full_transcript(client: TestClient) -> None:
    _, lid = _lecture(client)
    t = client.post("/api/chat/threads", json={"scope": "lecture", "lecture_id": lid}).json()
    assert t["scope_label"].startswith("Lecture:")
    events = _sse(
        client.post(f"/api/chat/threads/{t['id']}/messages", json={"content": "Summarise"}).text
    )
    done = events[-1][1]
    assert done["citations"] == [{"label": "L1 · 00:05", "lecture_id": lid, "start_s": 5}]


def test_grounded_chat_says_when_not_covered(client: TestClient) -> None:
    sid = client.post("/api/subjects", json={"name": "Empty"}).json()["id"]
    t = client.post("/api/chat/threads", json={"scope": "subject", "subject_id": sid}).json()
    events = _sse(client.post(f"/api/chat/threads/{t['id']}/messages", json={"content": "Q?"}).text)
    assert events[1] == ("sources", [])
    assert "don't cover" in events[-1][1]["content"] and events[-1][1]["citations"] == []


def test_make_flashcards_from_answer(client: TestClient) -> None:
    sid, lid = _lecture(client)
    t = client.post("/api/chat/threads", json={"scope": "subject", "subject_id": sid}).json()
    done = _sse(
        client.post(f"/api/chat/threads/{t['id']}/messages", json={"content": "segment 2"}).text
    )[-1][1]
    r = client.post(f"/api/chat/messages/{done['id']}/flashcards")
    assert r.status_code == 200 and r.json()["created"] > 0
    with session_scope() as s:
        cards = s.exec(select(Flashcard).where(Flashcard.lecture_id == lid)).all()
    assert cards and all("from-chat" in c.tags for c in cards)


def test_llm_error_is_reported_not_raised(client: TestClient, monkeypatch) -> None:
    sid, _ = _lecture(client)
    t = client.post("/api/chat/threads", json={"scope": "subject", "subject_id": sid}).json()
    client.put("/api/settings/llm/provider", json={"provider": "anthropic"})
    monkeypatch.setattr("backend.ai.providers.get_key", lambda _: None)
    events = _sse(client.post(f"/api/chat/threads/{t['id']}/messages", json={"content": "Hi"}).text)
    assert events[-1][0] == "error" and "API key" in events[-1][1]["message"]


def test_extract_citations_ignores_unknown_labels() -> None:
    prep = Prepared(request=None, labels={"NLP L2 · 01:05": (9, 65.0)})  # type: ignore[arg-type]
    answer = "Yes [NLP L2 · 01:05], see also [NLP L2 · 01:05] and [L7 · 00:01] and [link]."
    assert extract_citations(answer, prep) == [
        {"label": "NLP L2 · 01:05", "lecture_id": 9, "start_s": 65.0}
    ]


def test_precise_citations_inside_excerpts() -> None:
    prep = Prepared(
        request=None,  # type: ignore[arg-type]
        labels={"NLP L2 · 01:05": (9, 65.0)},
        prefixes={"NLP L2": 9},
    )
    answer = "Defined at [NLP L2 · 03:10] and summarised in [NLP L2]; not [NLP L3 · 00:01]."
    assert extract_citations(answer, prep) == [
        {"label": "NLP L2 · 03:10", "lecture_id": 9, "start_s": 190},
        {"label": "NLP L2", "lecture_id": 9, "start_s": None},
    ]
