from __future__ import annotations

import shutil
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.core.config import Settings
from backend.core.jobs import Worker
from backend.main import main
from tests.fakes import fake_factory
from tests.test_generate import _lecture

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not on PATH")


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    settings.generation.auto = True
    with TestClient(create_app(settings, start_worker=False, source_factory=fake_factory())) as c:
        yield c


def test_candidates_review_and_runs(client: TestClient) -> None:
    lid = _lecture(client)  # imported, transcribed, generated (mock) and indexed
    assert client.post("/api/evals/runs", json={"kind": "retrieval"}).status_code == 202
    Worker().run_pending()
    first = client.get("/api/evals").json()["runs"][0]
    assert first["status"] == "failed" and "No approved" in first["error"]

    made = client.post("/api/evals/cases/make", json={"n": 5}).json()["made"]
    assert made >= 2
    ev = client.get("/api/evals").json()
    assert ev["counts"]["candidate"] == made
    case = ev["cases"][0]
    assert case["question"].startswith("what about ") and case["lecture_id"] == lid
    assert case["passage"] and "-" in case["gold"]
    # Making more doesn't reuse the same sections.
    assert client.post("/api/evals/cases/make", json={"n": 5}).json()["made"] == 0

    ids = [c["id"] for c in ev["cases"]]
    for cid in ids[1:]:
        assert (
            client.patch(f"/api/evals/cases/{cid}", json={"status": "approved"}).status_code == 200
        )
    client.patch(f"/api/evals/cases/{ids[0]}", json={"status": "rejected"})
    client.patch(f"/api/evals/cases/{ids[1]}", json={"question": "  a fixed question  "})
    ev = client.get("/api/evals").json()
    assert ev["counts"] == {"candidate": 0, "approved": made - 1, "rejected": 1}
    assert ids[0] not in [c["id"] for c in ev["cases"]]
    assert next(c for c in ev["cases"] if c["id"] == ids[1])["question"] == "a fixed question"

    client.post("/api/evals/runs", json={"kind": "retrieval"})
    client.post("/api/evals/runs", json={"kind": "grounding"})
    Worker().run_pending()
    runs = {r["kind"]: r for r in client.get("/api/evals").json()["runs"] if r["status"] == "done"}
    m = runs["retrieval"]["metrics"]
    assert m["n"] == made - 1 and 0 <= m["hit@1"] <= m["hit@5"] <= m["hit@10"] <= 1
    assert m["lecture_hit@5"] == 1.0  # only one lecture: it is always found
    assert runs["retrieval"]["config"]["prompts"]["system"] == 4
    detail = client.get(f"/api/evals/runs/{runs['retrieval']['id']}").json()["details"][0]
    assert {"question", "rank", "lecture_rank", "top", "gold"} <= detail.keys()

    g = runs["grounding"]["metrics"]
    assert (
        g["n"] > 0
        and abs(g["supported"] + g["partly"] + g["unsupported"] + g["bad_ref"] - 1) < 0.01
    )
    assert "card_supported" in g


def test_cli(client: TestClient, settings: Settings, capsys: pytest.CaptureFixture) -> None:
    from backend.core.state import update_state

    _lecture(client)
    update_state(llm_provider="mock")  # the CLI loads its own settings: pin the mock provider
    main(["--data-dir", str(settings.data_dir), "eval", "retrieval"])
    assert "No approved eval questions" in capsys.readouterr().out
    main(["--data-dir", str(settings.data_dir), "eval", "make", "-n", "3"])
    assert "candidate questions made" in capsys.readouterr().out
