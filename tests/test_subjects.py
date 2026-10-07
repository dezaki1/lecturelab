from __future__ import annotations

from fastapi.testclient import TestClient


def test_subject_crud(client: TestClient) -> None:
    r = client.post(
        "/api/subjects",
        json={"name": "Reinforcement Learning", "vocabulary": ["MDP", " MDP ", "", "Bellman"]},
    )
    assert r.status_code == 201, r.text
    subj = r.json()
    assert subj["slug"] == "reinforcement-learning"
    assert subj["vocabulary"] == ["MDP", "Bellman"]
    assert subj["language"] == "auto" and subj["output_language"] == "en"

    assert client.post("/api/subjects", json={"name": "reinforcement learning"}).status_code == 409

    r = client.patch(
        f"/api/subjects/{subj['id']}",
        json={"name": "RL", "language": "de", "exam_date": "2027-01-28"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "RL"
    assert r.json()["slug"] == "reinforcement-learning"  # folder name stays stable
    assert r.json()["exam_date"] == "2027-01-28"

    assert client.patch(f"/api/subjects/{subj['id']}", json={"language": "fr"}).status_code == 422

    names = [s["name"] for s in client.get("/api/subjects").json()]
    assert names == ["RL"]

    assert client.delete(f"/api/subjects/{subj['id']}").status_code == 204
    assert client.get(f"/api/subjects/{subj['id']}").status_code == 404


def test_slug_collision(client: TestClient) -> None:
    a = client.post("/api/subjects", json={"name": "Math for AI"}).json()
    b = client.post("/api/subjects", json={"name": "Math for AI!"}).json()
    assert a["slug"] == "math-for-ai" and b["slug"] == "math-for-ai-2"


def test_health_and_system(client: TestClient) -> None:
    assert client.get("/api/health").json()["app"] == "lecturelab"
    info = client.get("/api/system").json()
    assert info["hotkeys"]["start_stop"]
