"""Half-price batch mode for background Claude calls, and the lane that waits for them.

A fake Anthropic client stands in for the API: nothing here reaches the network.
"""

from __future__ import annotations

from types import SimpleNamespace as NS
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel

from backend.ai.providers import anthropic_provider as ap
from backend.ai.types import LLMRequest, Usage
from backend.core import jobs
from backend.core.config import LLMConfig


class Summary(BaseModel):
    tldr: str


def _msg(stop: str = "tool_use", tool_input: dict | None = None) -> Any:
    content = [NS(type="tool_use", name="submit_summary", input=tool_input or {"tldr": "short"})]
    usage = NS(
        input_tokens=100,
        output_tokens=50,
        cache_read_input_tokens=0,
        cache_creation_input_tokens=1000,
    )
    return NS(content=content, usage=usage, stop_reason=stop, stop_details=None, model="m")


class FakeBatches:
    def __init__(self, results: list[Any]) -> None:
        self.results_queue = results
        self.created: list[dict] = []
        self.cancelled: list[str] = []
        self.polls = 0

    def create(self, requests: list[dict]) -> Any:
        self.created.append(requests[0]["params"])
        return NS(id=f"b{len(self.created)}", processing_status="in_progress")

    def retrieve(self, batch_id: str) -> Any:
        self.polls += 1
        return NS(id=batch_id, processing_status="ended")

    def results(self, batch_id: str) -> list[Any]:
        return [NS(custom_id="summary", result=self.results_queue.pop(0))]

    def cancel(self, batch_id: str) -> None:
        self.cancelled.append(batch_id)


class FakeStream:
    def __init__(self, calls: list[dict], **kwargs: Any) -> None:
        calls.append(kwargs)

    def __enter__(self) -> Any:
        return self

    def __exit__(self, *a: Any) -> None:
        pass

    def get_final_message(self) -> Any:
        msg = _msg(stop="end_turn")  # the normal path parses a structured text answer
        msg.content = [NS(type="text", parsed_output=Summary(tldr="short"))]
        return msg


def _provider(monkeypatch, results: list[Any]) -> tuple[Any, FakeBatches, list[dict]]:
    monkeypatch.setattr(ap.time, "sleep", lambda s: None)
    p = ap.AnthropicProvider(LLMConfig(), "sk-ant-test", batch=True)
    batches = FakeBatches(results)
    streamed: list[dict] = []
    p.client = NS(
        messages=NS(batches=batches),
        beta=NS(messages=NS(stream=lambda **kw: FakeStream(streamed, **kw))),
    )
    return p, batches, streamed


def _req(task: str = "summary") -> LLMRequest:
    return LLMRequest(
        task=task, system="sys", context="lecture text", instruction="Summarise.", output=Summary
    )


def test_background_call_goes_through_a_batch(monkeypatch) -> None:
    p, batches, streamed = _provider(monkeypatch, [NS(type="succeeded", message=_msg())])
    out = p.complete(_req())
    assert out.parsed == Summary(tldr="short") and not streamed
    params = batches.created[0]
    assert "fallbacks" not in params and "betas" not in params  # rejected by the Batches API
    assert params["messages"][0]["content"][0]["cache_control"] == {
        "type": "ephemeral",
        "ttl": "1h",
    }
    assert params["tools"][0]["name"] == "submit_summary"  # single schema -> single tool
    assert out.usage.batch and out.usage.cache_write_1h_tokens == 1000
    assert p.max_parallel == 8


def test_refused_in_a_batch_is_sent_normally_with_fallbacks(monkeypatch) -> None:
    p, _, streamed = _provider(monkeypatch, [NS(type="succeeded", message=_msg(stop="refusal"))])
    out = p.complete(_req())
    assert out.parsed == Summary(tldr="short")
    assert streamed and streamed[0]["fallbacks"] == "default"


def test_interactive_calls_never_wait_for_a_batch(monkeypatch) -> None:
    p, batches, streamed = _provider(monkeypatch, [])
    p.complete(_req(task="exam_grade"))
    assert not batches.created and streamed


def test_shutdown_cancels_the_batch(monkeypatch) -> None:
    p, batches, _ = _provider(monkeypatch, [])
    batches.retrieve = lambda batch_id: NS(id=batch_id, processing_status="in_progress")
    calls = iter([False, True])
    monkeypatch.setattr(jobs, "shutting_down", lambda: next(calls))
    with pytest.raises(ap.LLMError, match="shutdown"):
        p.complete(_req())
    assert batches.cancelled == ["b1"]


def test_batch_usage_costs_half() -> None:
    full = Usage(input_tokens=1_000_000, output_tokens=100_000)
    half = Usage(input_tokens=1_000_000, output_tokens=100_000, batch=True)
    assert half.cost("claude-sonnet-5-5") == pytest.approx(full.cost("claude-sonnet-5-5") / 2)
    # An hour-long cache write costs twice the input price (before the batch discount).
    assert Usage(cache_write_1h_tokens=1_000_000).cost("claude-sonnet-5-5") == pytest.approx(4.0)


def test_claude_jobs_have_their_own_lane() -> None:
    assert jobs.lane_of("generate", remote_llm=True) == "llm"
    assert jobs.lane_of("generate", remote_llm=False) == "main"  # Ollama uses the GPU
    assert jobs.lane_of("transcribe", remote_llm=True) == "main"
    assert jobs.lane_of("slides", remote_llm=True) == "side"


def test_batch_switch_in_settings(client: TestClient) -> None:
    assert client.get("/api/settings/llm").json()["anthropic"]["batch"] is True
    r = client.put("/api/settings/llm/batch", json={"enabled": False})
    assert r.json()["anthropic"]["batch"] is False
