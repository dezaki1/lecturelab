from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.core.config import Settings
from backend.study import speak
from backend.voice.stt import get_transcriber
from tests.test_weak import _setup


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(settings, start_worker=False)) as c:
        yield c
    get_transcriber().shutdown()


def test_teach_back(client: TestClient) -> None:
    ids = _setup()
    topics = client.get("/api/speak/topics", params={"subject_id": ids["subject"]}).json()
    assert "Bellman equation" in [t["name"] for t in topics]
    bellman = next(t for t in topics if t["name"] == "Bellman equation")
    assert bellman["lecture_ids"] == [ids["lecture"]] and bellman["status"] == "weak"

    bad = {"subject_id": ids["subject"], "mode": "teach", "topics": []}
    assert client.post("/api/speak/sessions", json=bad).status_code == 422
    r = client.post(
        "/api/speak/sessions",
        json={"subject_id": ids["subject"], "mode": "teach", "topics": ["Bellman equation"]},
    )
    assert r.status_code == 201, r.text
    sess = r.json()
    assert sess["turns"][0]["text"].startswith("Explain Bellman equation in your own words")
    assert sess["config"]["lecture_ids"] == [ids["lecture"]]

    sess = client.post(
        f"/api/speak/sessions/{sess['id']}/answer",
        json={"text": "V of s is r plus gamma V of s prime.", "spoken": True},
    ).json()
    assert [t["role"] for t in sess["turns"]] == ["examiner", "student", "coach", "examiner"]
    assert sess["turns"][1]["spoken"] and sess["status"] == "active"
    fb = sess["result"]
    assert fb["score"] == 75 and fb["missing"][0]["point"] == "Why it matters"
    ref = fb["missing"][0]["refs"][0]
    assert (
        ref["lecture_id"] == ids["lecture"] and ref["start_s"] == 10 and ref["label"] == "L1 00:10"
    )
    assert sess["turns"][-1]["kind"] == "follow_up"

    sess = client.post(
        f"/api/speak/sessions/{sess['id']}/answer", json={"text": "Because it is recursive."}
    ).json()
    assert sess["status"] == "done" and sess["score"] == 90 and sess["finished_at"]
    r = client.post(f"/api/speak/sessions/{sess['id']}/answer", json={"text": "more"})
    assert r.status_code == 422

    audio = client.get(f"/api/speak/sessions/{sess['id']}/turns/0/audio")
    assert audio.status_code == 200 and audio.headers["content-type"] == "audio/wav"
    assert audio.content[:4] == b"RIFF"
    assert client.get(f"/api/speak/sessions/{sess['id']}/turns/99/audio").status_code == 404


def test_oral_exam_with_follow_up(client: TestClient) -> None:
    ids = _setup()
    r = client.post(
        "/api/speak/sessions",
        json={"subject_id": ids["subject"], "mode": "oral", "questions": 2},
    )
    assert r.status_code == 201, r.text
    sess = r.json()
    sid = sess["id"]
    assert sess["turns"] == [
        {
            "role": "examiner",
            "kind": "question",
            "text": "Question 1: what is mock topic 1?",
            "topic": "Topic 1",
        }
    ]
    sess = client.post(f"/api/speak/sessions/{sid}/answer", json={"text": "A good answer"}).json()
    assert sess["turns"][-1]["text"].startswith("Question 2") and sess["turns"][1]["rating"] == 6
    sess = client.post(
        f"/api/speak/sessions/{sid}/answer", json={"text": "I think it's wrong"}
    ).json()
    assert sess["turns"][-1]["kind"] == "follow_up"
    sess = client.post(f"/api/speak/sessions/{sid}/answer", json={"text": "Because of X"}).json()
    assert sess["turns"][-1]["kind"] == "end" and sess["status"] == "done"
    res = sess["result"]
    assert res["grade"] == 3 and sess["score"] == 72 and len(res["questions"]) == 2
    assert res["gaps"][0]["refs"][0]["lecture_id"] == ids["lecture"]

    listed = client.get("/api/speak/sessions", params={"subject_id": ids["subject"]}).json()
    assert listed[0]["id"] == sid and listed[0]["grade"] == 3 and listed[0]["subject_name"] == "RL"
    assert client.delete(f"/api/speak/sessions/{sid}").status_code == 204
    assert client.get(f"/api/speak/sessions/{sid}").status_code == 404


def test_finish_early_and_errors(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    ids = _setup()
    start = {"subject_id": ids["subject"], "mode": "oral", "questions": 5}
    sid = client.post("/api/speak/sessions", json=start).json()["id"]
    client.post(f"/api/speak/sessions/{sid}/answer", json={"text": "An answer"})
    sess = client.post(f"/api/speak/sessions/{sid}/finish").json()
    assert sess["status"] == "done" and sess["result"]["grade"] == 3

    # Abandoned before answering: no evaluation.
    sid = client.post("/api/speak/sessions", json=start).json()["id"]
    sess = client.post(f"/api/speak/sessions/{sid}/finish").json()
    assert sess["status"] == "done" and sess["result"] == {}

    monkeypatch.setattr(speak, "is_configured", lambda: False)
    r = client.post("/api/speak/sessions", json=start)
    assert r.status_code == 422 and "AI provider" in r.json()["detail"]
    empty = client.post("/api/subjects", json={"name": "Empty"}).json()["id"]
    monkeypatch.setattr(speak, "is_configured", lambda: True)
    r = client.post("/api/speak/sessions", json={**start, "subject_id": empty})
    assert r.status_code == 422 and "no study notes" in r.json()["detail"]


def test_voice_answer_and_settings(client: TestClient) -> None:
    ids = _setup()
    assert client.post("/api/voice/transcribe", content=b"").status_code == 422
    for _ in range(2):  # the second clip reuses the warm worker
        r = client.post(
            "/api/voice/transcribe",
            params={"subject_id": ids["subject"]},
            content=b"\x1aE\xdf\xa3 fake webm",
            headers={"Content-Type": "audio/webm;codecs=opus"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["text"] == "This is a placeholder spoken answer."
    assert get_transcriber().loaded

    v = client.get("/api/settings/voice").json()
    assert v["voice"] == "en_US-lessac-medium" and v["tts_ready"]  # fake engine
    assert {x["id"] for x in v["voices"]} >= {"en_US-lessac-medium", "de_DE-thorsten-medium"}
    assert v["stt"]["loaded"]
    assert client.put("/api/settings/voice", json={"voice": "nope"}).status_code == 422
    v = client.put("/api/settings/voice", json={"rate": 1.2, "voice": "en_GB-alan-medium"}).json()
    assert v["rate"] == 1.2 and v["voice"] == "en_GB-alan-medium"
    assert client.put("/api/settings/voice", json={"rate": 3}).status_code == 422
    sample = client.get("/api/voice/sample")
    assert sample.status_code == 200 and sample.content[:4] == b"RIFF"
    assert client.post("/api/settings/voice/download", json={"voice": "x"}).status_code == 422

    get_transcriber().shutdown()
    assert not get_transcriber().loaded
