from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from backend.ai import schemas as sc
from backend.ai.prompts import PROMPTS_DIR, load_prompt
from backend.ai.providers.anthropic_provider import AnthropicProvider
from backend.ai.types import LLMRequest, LLMResult, Usage
from backend.app import create_app
from backend.core.config import LLMConfig, Settings
from backend.core.db import session_scope
from backend.core.jobs import Worker
from backend.core.models import Flashcard, Lecture, UsageRecord
from backend.pipeline import generate as gen
from tests.fakes import fake_factory

FIXTURES = Path(__file__).parent / "fixtures"
pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not on PATH")


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    settings.generation.auto = True
    with TestClient(create_app(settings, start_worker=False, source_factory=fake_factory())) as c:
        yield c


def _import(c: TestClient, sid: int, name: str = "lecture.m4a", **params: object) -> int:
    r = c.post(
        "/api/imports",
        params={"subject_id": sid, "filename": name, **params},
        content=(FIXTURES / "lecture.m4a").read_bytes(),
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _lecture(c: TestClient, name: str = "ML") -> int:
    sid = c.post("/api/subjects", json={"name": name, "instructions": "Show derivations."}).json()[
        "id"
    ]
    lid = _import(c, sid)
    assert Worker().run_pending() == 3  # import -> transcribe -> generate
    return lid


def test_full_pipeline_with_mock_provider(client: TestClient) -> None:
    lid = _lecture(client)
    lec = client.get(f"/api/lectures/{lid}").json()
    assert lec["status"] == "ready", lec
    assert lec["stages"] == {"capture": "done", "transcribe": "done", "generate": "done"}
    assert lec["title"] == "Mock lecture topic"  # default title replaced by the topic title

    m = client.get(f"/api/lectures/{lid}/materials").json()
    assert set(m["notes"]) == {"summary", "notes", "glossary", "announcements"}
    summary = m["notes"]["summary"]
    assert "## TL;DR" in summary["content_md"] and "[00:" in summary["content_md"]
    assert summary["prompt_version"] == "summary@v1" and summary["version"] == 1
    assert "★" in m["notes"]["notes"]["content_md"]  # emphasized section marked
    assert "Hauptkomponente" in m["notes"]["glossary"]["content_md"]

    # Every generated item carries valid lecture refs inside the lecture.
    items = [*m["flashcards"], *m["exercises"], *m["questions"]]
    assert len(m["flashcards"]) == 5 and len(m["exercises"]) == 1 and len(m["questions"]) == 2
    for item in items:
        assert item["source_refs"], item
        for ref in item["source_refs"]:
            assert ref["lecture_id"] == lid and 0 <= ref["start_s"] <= ref["end_s"] <= 114
        assert item["verified"] == "ok"  # verification pass ran
    with session_scope() as s:
        tasks = sorted(r.task for r in s.exec(select(UsageRecord)).all())
    assert tasks.count("verify") == 3 and "notes" in tasks and len(tasks) == 10


def test_regenerate_keeps_edits_as_versions(client: TestClient) -> None:
    lid = _lecture(client)
    note = client.get(f"/api/lectures/{lid}/materials").json()["notes"]["notes"]
    edited = client.patch(f"/api/notes/{note['id']}", json={"content_md": "# My notes"}).json()
    assert edited["version"] == 2 and edited["edited_by_me"] and edited["is_current"]
    again = client.patch(f"/api/notes/{edited['id']}", json={"content_md": "# My notes v2"}).json()
    assert again["id"] == edited["id"] and again["version"] == 2  # edits update in place

    r = client.post(
        f"/api/lectures/{lid}/generate", json={"kinds": ["notes"], "instructions": "shorter"}
    )
    assert r.status_code == 202
    assert client.post(f"/api/lectures/{lid}/generate", json={}).status_code == 409  # busy
    Worker().run_pending()
    cur = client.get(f"/api/lectures/{lid}/materials").json()["notes"]["notes"]
    assert cur["version"] == 3 and not cur["edited_by_me"]
    assert [v["version"] for v in cur["versions"]] == [3, 2, 1]
    restored = client.post(f"/api/notes/{edited['id']}/restore").json()
    assert restored["is_current"] and restored["content_md"] == "# My notes v2"


def test_regenerating_cards_keeps_reviewed_and_edited(client: TestClient) -> None:
    lid = _lecture(client)
    with session_scope() as s:
        cards = s.exec(select(Flashcard).where(Flashcard.lecture_id == lid)).all()
        cards[0].fsrs = {"stability": 1.0}  # reviewed
        cards[1].edited_by_me = True
        for c in cards[:2]:
            s.add(c)
        s.commit()
        kept_ids = {cards[0].id, cards[1].id}
    client.post(f"/api/lectures/{lid}/generate", json={"kinds": ["flashcards"]})
    Worker().run_pending()
    current = client.get(f"/api/lectures/{lid}/materials").json()["flashcards"]
    # 2 kept + 3 new: the mock re-generates the same 5 cards, the 2 duplicates are dropped
    assert len(current) == 5 and len({c["front"] for c in current}) == 5
    assert kept_ids <= {c["id"] for c in current}


def test_generate_requires_transcript_and_key(client: TestClient, monkeypatch) -> None:
    sid = client.post("/api/subjects", json={"name": "X"}).json()["id"]
    lid = _import(client, sid, "a.m4a")
    assert client.post(f"/api/lectures/{lid}/generate", json={}).status_code == 409  # no transcript
    client.put("/api/settings/llm/provider", json={"provider": "anthropic"})
    monkeypatch.setattr("backend.ai.providers.get_key", lambda _: None)
    Worker().run_pending()  # import + transcribe; auto-generate skipped without a key
    lec = client.get(f"/api/lectures/{lid}").json()
    assert lec["status"] == "ready" and "generate" not in lec["stages"]
    r = client.post(f"/api/lectures/{lid}/generate", json={})
    assert r.status_code == 409 and "API key" in r.json()["detail"]
    est = client.get(f"/api/lectures/{lid}/generate/estimate").json()
    assert est["configured"] is False and 0.01 < est["usd"] < 1.0


def test_settings_never_return_the_key(client: TestClient, monkeypatch) -> None:
    stored: dict[str, str] = {}
    monkeypatch.setattr("backend.ai.keys.set_key", lambda p, k: stored.__setitem__(p, k))
    monkeypatch.setattr("backend.ai.keys.get_key", lambda p: stored.get(p))
    assert client.put("/api/settings/api-key", json={"key": "not-a-key-123"}).status_code == 422
    r = client.put("/api/settings/api-key", json={"key": "sk-ant-test-0000000000abcd"})
    assert r.json() == {"set": True, "hint": "…abcd", "source": "keyring"}
    body = client.get("/api/settings/llm").text
    assert "sk-ant" not in body
    assert client.post("/api/settings/llm/test").json()["ok"] is True  # mock provider


def test_timestamps_and_refs() -> None:
    assert gen.parse_ts("12:34") == 754 and gen.parse_ts("1:02:03") == 3723
    assert gen.parse_ts("[05:00]") == 300 and gen.parse_ts("12:75") is None
    assert gen.parse_ts("soon") is None
    refs = gen.to_refs(
        1,
        [
            sc.Ref(start="00:10", end="00:20"),
            sc.Ref(start="bad", end="00:20"),
            sc.Ref(start="99:00", end="99:10"),  # beyond the lecture
            sc.Ref(start="01:50", end="01:40"),  # end before start
        ],
        duration=114,
    )
    assert [(r["start_s"], r["end_s"]) for r in refs] == [(10, 20), (110, 110)]


def test_dedupe_and_amounts() -> None:
    k = gen.card_key
    existing = [k("In PCA, what do the eigenvectors represent?", "Directions of maximum variance.")]
    assert gen.is_duplicate(
        k("In PCA what do the eigenvectors represent", "Directions of maximum variance"), existing
    )
    # same question shape, different concept and answer: not a duplicate
    assert not gen.is_duplicate(
        k("In PCA, what do the eigenvalues represent?", "Variance explained per component."),
        existing,
    )
    assert gen._amount(90, (2.0, 6.0), 3) == (18, 54)
    assert gen._amount(5, (2.0, 6.0), 3) == (3, 4)


def test_prompts_render() -> None:
    names = {p.stem for p in PROMPTS_DIR.glob("*.md")}
    assert {"system", "summary", "notes", "flashcards", "questions", "verify"} <= names
    fc = load_prompt("flashcards").render(min=3, max=9, existing_cards="")
    assert "{{c1::largest eigenvalues}}" in fc and "3-9" in fc  # cloze syntax untouched
    with pytest.raises(KeyError):
        load_prompt("flashcards").render(min=1)


def test_verification_flags_unsupported(client: TestClient) -> None:
    lid = _lecture(client)
    with session_scope() as s:
        ctx = gen.build_context(s, s.get(Lecture, lid))
        card = s.exec(select(Flashcard).where(Flashcard.lecture_id == lid)).first()
        assert card is not None

    class Stub:
        name = "stub"

        def model_for(self, task: str, tier: str) -> str:
            return "stub"

        def complete(self, req: LLMRequest) -> LLMResult:
            assert req.tier == "cheap" and f"id={card.id}" in req.instruction
            assert "Cited excerpt: [00:" in req.instruction
            out = sc.VerifyOut(verdicts=[sc.Verdict(id=card.id, supported=False, issue="wrong")])
            return LLMResult(out, "stub", "stub", Usage())

    gen.verify_items(gen.Runner(ctx, Stub()), Flashcard, [(card.id, "Q", card.source_refs)])
    with session_scope() as s:
        row = s.get(Flashcard, card.id)
        assert row is not None and row.verified == "flagged" and row.verify_note == "wrong"


def test_context_has_bookmarks_and_earlier_lectures(client: TestClient) -> None:
    first = _lecture(client)
    sid = client.get(f"/api/lectures/{first}").json()["subject_id"]
    # a later lecture (file modified in 2100) in the same subject
    second = _import(client, sid, "b.m4a", last_modified_ms=4_102_444_800_000)
    client.post(f"/api/lectures/{second}/bookmarks", json={"t_s": 61, "kind": "confused"})
    Worker().run_pending()
    with session_scope() as s:
        ctx = gen.build_context(s, s.get(Lecture, second))
    assert "- [01:01] confused" in ctx.context
    assert "- L1 (" in ctx.context and "Mock lecture topic" in ctx.context
    assert ctx.context.count("[00:") > 5 and "Show derivations." in ctx.system


class _FakeStream:
    def __init__(self, parsed: object) -> None:
        self.parsed = parsed

    def __enter__(self) -> _FakeStream:
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def get_final_message(self) -> SimpleNamespace:
        usage = SimpleNamespace(
            input_tokens=10,
            output_tokens=20,
            cache_read_input_tokens=1000,
            cache_creation_input_tokens=0,
        )
        block = SimpleNamespace(type="text", parsed_output=self.parsed)
        return SimpleNamespace(
            usage=usage,
            stop_reason="end_turn",
            stop_details=None,
            content=[block],
            model="claude-sonnet-5-5",
        )


def test_anthropic_request_shape_without_network() -> None:
    """Checks caching, effort and fallbacks on the outgoing request using a fake client."""
    calls: list[dict] = []
    parsed = sc.SummaryOut(topic_title="t", tldr=[], remember=[])

    def stream(**kwargs: object) -> _FakeStream:
        calls.append(kwargs)
        return _FakeStream(parsed)

    provider = AnthropicProvider(LLMConfig(), api_key="sk-ant-fake")
    provider.client = SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(stream=stream)))
    req = LLMRequest(
        task="summary", system="SYS", context="CTX", instruction="DO", output=sc.SummaryOut
    )
    result = provider.complete(req)
    sent = calls[0]
    assert sent["model"] == "claude-sonnet-5-5"
    assert sent["messages"][0]["content"][0] == {
        "type": "text",
        "text": "CTX",
        "cache_control": {"type": "ephemeral"},
    }
    assert sent["messages"][0]["content"][1]["text"] == "DO"
    assert sent["output_config"] == {"effort": "medium"}
    assert sent["fallbacks"] == "default" and sent["betas"] == ["server-side-fallback-2026-07-01"]
    assert result.usage.cache_read_tokens == 1000
    expected = (10 * 2 + 20 * 10 + 1000 * 0.2) / 1e6
    assert abs(result.usage.cost("claude-sonnet-5-5") - expected) < 1e-12

    calls.clear()
    cheap = LLMRequest(
        task="verify", system="S", context="C", instruction="I", output=sc.SummaryOut, tier="cheap"
    )
    provider.complete(cheap)
    assert calls[0]["model"] == "claude-haiku-5-5" and "fallbacks" not in calls[0]
    assert calls[0]["output_config"] == {"effort": "low"}
