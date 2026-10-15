"""Quick questions during a lecture, answered from the live transcript (see capture.live)."""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from backend.ai import schemas as sc
from backend.ai.prompts import load_prompt
from backend.ai.providers import get_provider, is_configured
from backend.ai.types import LLMRequest
from backend.capture.live import transcript_text
from backend.core.config import get_settings
from backend.core.events import bus
from backend.core.util import format_hms
from backend.pipeline.generate import parse_ts, record_usage

log = logging.getLogger(__name__)

QA_NAME = "live_qa.jsonl"
SYSTEM = (
    "You help a university student follow a lecture while it is happening. You answer quickly "
    "and briefly, grounded in the live transcript."
)


class LiveAskError(Exception):
    pass


def load_answers(folder) -> list[dict[str, Any]]:  # type: ignore[no-untyped-def]
    path = folder / QA_NAME
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def ask(recorder, question: str) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    a = recorder._active
    if a is None:
        raise LiveAskError("Not recording")
    if not is_configured():
        raise LiveAskError("No AI provider is set up (Settings → AI provider)")
    state = recorder.live_state()
    text = transcript_text(state["segments"], last_s=get_settings().live.ask_minutes * 60)
    if not text.strip():
        raise LiveAskError("Nothing transcribed yet: turn on the live transcript and wait a moment")
    t0 = time.monotonic()
    result = get_provider().complete(
        LLMRequest(
            task="live_ask",
            system=SYSTEM,
            context=f'# {a.subject_name}: "{a.title}" (live transcript, latest last)\n\n{text}',
            instruction=load_prompt("live_ask").render(question=question.strip()),
            output=sc.LiveAnswerOut,
            effort="low",
            max_tokens=3000,
        )
    )
    record_usage(a.lecture_id, a.subject_id, "live_ask", result)
    out: sc.LiveAnswerOut = result.parsed
    refs = []
    for r in out.refs:
        t = parse_ts(r)
        if t is not None:
            refs.append({"t_s": t, "label": format_hms(t)})
    qa = {
        "question": question.strip(),
        "answer": out.answer.strip(),
        "refs": refs,
        "at_s": state["elapsed_s"],
        "seconds": round(time.monotonic() - t0, 1),
    }
    with open(a.folder / QA_NAME, "a", encoding="utf-8") as f:
        f.write(json.dumps(qa, ensure_ascii=False) + "\n")
    bus.publish("live.answer", {"lecture_id": a.lecture_id, **qa})
    return qa
