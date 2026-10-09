"""Chat over the student's material (SPEC §10).

- Lecture scope: the whole transcript and the lecture's notes go into the (cached) context.
- Subject / everything scope: hybrid retrieval over transcript and note chunks for each turn;
  follow-up questions are first rewritten into a standalone search query by the cheap model.

Answers cite sources as labels like [L5 · 34:12] (or [NLP L5 · 34:12] across subjects). The
labels are resolved back to lecture + timestamp so the UI can render them as seekable chips.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel
from sqlmodel import Session, col, select

from backend.ai import schemas as sc
from backend.ai.prompts import load_prompt
from backend.ai.providers import Provider
from backend.ai.types import ChatEvent, ChatRequest, LLMError, LLMRequest
from backend.core.config import get_settings
from backend.core.models import (
    ChatMessage,
    ChatThread,
    Flashcard,
    Lecture,
    Note,
    Subject,
    TranscriptSegment,
    UsageRecord,
)
from backend.core.util import format_hms
from backend.pipeline.generate import card_key, is_duplicate, parse_ts
from backend.search.retrieve import Hit, lecture_numbers, search, short_name

log = logging.getLogger(__name__)

HISTORY_TURNS = 8
# [L5 · 34:12], [NLP L5 · 1:02:03], [MfAI III L2] (subject prefix optional)
LABEL = re.compile(r"\[((?:[^\[\]]{1,40} )?L\d+(?: · \d{1,2}:\d{2}(?::\d{2})?)?)\]")
GROUNDED = (
    "Grounded mode is on. If the provided material does not contain the answer, say so plainly "
    'first ("Your lectures don\'t cover this."), then you may give a short answer from general '
    "knowledge under a bold **General knowledge:** label, without citations. Never invent "
    "citations."
)
FREE = (
    "You may add general knowledge where it helps. Mark such parts with **General knowledge:** "
    "and cite only the student's material, never invent citations."
)


class StandaloneQuery(BaseModel):
    query: str


@dataclass
class Prepared:
    request: ChatRequest
    labels: dict[str, tuple[int, float | None]] = field(default_factory=dict)
    # "NLP L5" -> lecture id: lets the model cite any [mm:ss] inside an excerpt precisely.
    prefixes: dict[str, int] = field(default_factory=dict)
    lecture_scope: tuple[int, int] | None = None  # (lecture number, lecture id)
    sources: list[dict[str, Any]] = field(default_factory=list)


def _system(thread: ChatThread) -> str:
    return load_prompt("chat").render(grounding_rule=GROUNDED if thread.grounded else FREE)


def _history(s: Session, thread: ChatThread) -> list[dict[str, str]]:
    msgs = s.exec(
        select(ChatMessage)
        .where(ChatMessage.thread_id == thread.id, col(ChatMessage.error).is_(None))
        .order_by(col(ChatMessage.id))
    ).all()
    out = [{"role": m.role, "content": m.content} for m in msgs if m.content.strip()]
    return out[-HISTORY_TURNS * 2 :]


def lecture_context(s: Session, lecture: Lecture) -> tuple[str, int]:
    number = lecture_numbers(s, {lecture.subject_id})[lecture.id]  # type: ignore[index]
    subject = s.get(Subject, lecture.subject_id)
    segments = s.exec(
        select(TranscriptSegment)
        .where(TranscriptSegment.lecture_id == lecture.id)
        .order_by(col(TranscriptSegment.idx))
    ).all()
    notes = s.exec(
        select(Note).where(
            Note.lecture_id == lecture.id,
            col(Note.is_current),
            col(Note.kind).in_(["summary", "notes", "announcements"]),
        )
    ).all()
    parts = [
        f"# The student's material: {subject.name if subject else ''}, lecture L{number} "
        f'"{lecture.title}" ({lecture.started_at.astimezone():%Y-%m-%d})',
        f"Cite this lecture as [L{number} · mm:ss] using the timestamps of the transcript lines.",
    ]
    for n in sorted(notes, key=lambda n: ["summary", "notes", "announcements"].index(n.kind)):
        parts.append(f"\n## Generated {n.kind} (derived from the transcript)\n{n.content_md}")
    parts.append("\n## Transcript")
    parts.extend(f"[{format_hms(seg.start)}] {seg.text}" for seg in segments)
    return "\n".join(parts), number


def _standalone_query(provider: Provider, history: list[dict[str, str]], question: str) -> str:
    if not history:
        return question
    convo = "\n".join(f"{m['role']}: {m['content'][:600]}" for m in history[-4:])
    try:
        result = provider.complete(
            LLMRequest(
                task="query_rewrite",
                system="Rewrite the student's latest question as a standalone search query.",
                context=f"Conversation so far:\n{convo}",
                instruction=(
                    f"Latest question: {question}\n\nReturn a short standalone search query "
                    "that includes the topic the question refers to."
                ),
                output=StandaloneQuery,
                tier="cheap",
                max_tokens=2000,
            )
        )
        return result.parsed.query or question
    except LLMError as e:
        log.info("Query rewrite failed, using the raw question: %s", e)
        return question


def prepare(s: Session, provider: Provider, thread: ChatThread, question: str) -> Prepared:
    system = _system(thread)
    history = _history(s, thread)
    if thread.scope == "lecture" and thread.lecture_id:
        lecture = s.get(Lecture, thread.lecture_id)
        if lecture is None:
            raise LLMError("This chat's lecture was deleted")
        context, number = lecture_context(s, lecture)
        messages = [*history, {"role": "user", "content": question}]
        return Prepared(
            ChatRequest(system=system, context=context, messages=messages),
            lecture_scope=(number, lecture.id),  # type: ignore[arg-type]
        )

    query = _standalone_query(provider, history, question)
    hits = search(
        s,
        query,
        subject_id=thread.subject_id if thread.scope == "subject" else None,
        date_from=thread.date_from,
        date_to=thread.date_to,
        limit=get_settings().search.chat_top_k,
    )
    with_subject = thread.scope == "all"
    labels: dict[str, tuple[int, float | None]] = {}
    prefixes: dict[str, int] = {}
    blocks = []
    for h in hits:
        label = h.label(with_subject)
        labels[label] = (h.lecture_id, h.start_s)  # type: ignore[assignment]
        prefixes[label.split(" · ")[0]] = h.lecture_id  # type: ignore[assignment]
        what = {
            "note": f"notes: {h.heading}",
            "slide": "slide text (OCR), shown on screen at this time",
        }.get(h.source, "transcript")
        body = _timestamped_transcript(s, h) if h.source == "transcript" else h.text
        blocks.append(
            f'<source label="{label}" lecture="{h.lecture_title}" '
            f'date="{(h.started_at or "")[:10]}" kind="{what}">\n{body}\n</source>'
        )
    example = next(iter(prefixes), "L1")
    material = (
        "Excerpts from the student's lectures. Cite them by their label. Note excerpts contain "
        f"[mm:ss] timestamps; to cite such a precise moment, combine the lecture part of the "
        f"label with it, e.g. [{example} · 12:34].\n" + "\n".join(blocks)
        if blocks
        else "No matching material was found in the student's lectures."
    )
    messages = [*history, {"role": "user", "content": f"{material}\n\nQuestion: {question}"}]
    sources = [_source(h, with_subject) for h in hits]
    return Prepared(
        ChatRequest(system=system, messages=messages),
        labels=labels,
        prefixes=prefixes,
        sources=sources,
    )


def _timestamped_transcript(s: Session, h: Hit) -> str:
    """A transcript chunk as "[mm:ss] sentence" lines, so answers can cite the exact moment."""
    if h.start_s is None or h.end_s is None:
        return h.text
    segs = s.exec(
        select(TranscriptSegment)
        .where(
            TranscriptSegment.lecture_id == h.lecture_id,
            TranscriptSegment.start >= h.start_s - 0.01,
            TranscriptSegment.start <= h.end_s,
        )
        .order_by(col(TranscriptSegment.idx))
    ).all()
    return "\n".join(f"[{format_hms(x.start)}] {x.text}" for x in segs) or h.text


def _source(h: Hit, with_subject: bool) -> dict[str, Any]:
    return {
        "label": h.label(with_subject),
        "lecture_id": h.lecture_id,
        "start_s": h.start_s,
        "title": h.lecture_title,
        "subject": short_name(h.subject_name),
    }


def extract_citations(answer: str, prep: Prepared) -> list[dict[str, Any]]:
    seen: dict[str, dict[str, Any]] = {}
    for label in LABEL.findall(answer):
        label = label.strip()
        if label in seen:
            continue
        prefix, _, ts = label.partition(" · ")
        if label in prep.labels:
            lecture_id, start = prep.labels[label]
            seen[label] = {"label": label, "lecture_id": lecture_id, "start_s": start}
        elif prefix in prep.prefixes and (not ts or parse_ts(ts) is not None):
            start = parse_ts(ts) if ts else None
            seen[label] = {"label": label, "lecture_id": prep.prefixes[prefix], "start_s": start}
        elif prep.lecture_scope:
            m = re.fullmatch(r"L(\d+)(?: · (\S+))?", label)
            if m and int(m.group(1)) == prep.lecture_scope[0]:
                start = parse_ts(m.group(2)) if m.group(2) else None
                seen[label] = {
                    "label": label,
                    "lecture_id": prep.lecture_scope[1],
                    "start_s": start,
                }
    return list(seen.values())


def run_turn(
    s: Session, provider: Provider, thread: ChatThread, question: str
) -> Iterator[tuple[str, Any]]:
    """Yields ("sources", list) once, then ("delta", text)..., then ("done", ChatMessage)."""
    prep = prepare(s, provider, thread, question)
    yield "sources", prep.sources
    parts: list[str] = []
    final: ChatEvent | None = None
    for ev in provider.chat_stream(prep.request):
        if ev.type == "text":
            parts.append(ev.text)
            yield "delta", ev.text
        else:
            final = ev
    answer = "".join(parts).strip()
    usage = final.usage if final and final.usage else None
    model = final.model if final else ""
    cost = usage.cost(model) if usage else 0.0
    msg = ChatMessage(
        thread_id=thread.id,  # type: ignore[arg-type]
        role="assistant",
        content=answer,
        citations=extract_citations(answer, prep),
        model=model,
        cost_usd=cost,
    )
    s.add(msg)
    if usage:
        s.add(
            UsageRecord(
                lecture_id=thread.lecture_id,
                subject_id=thread.subject_id,
                task="chat",
                provider=provider.name,
                model=model,
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                cache_read_tokens=usage.cache_read_tokens,
                cache_write_tokens=usage.cache_write_tokens,
                cost_usd=cost,
            )
        )
    s.commit()
    s.refresh(msg)
    yield "done", msg


def flashcards_from_message(s: Session, provider: Provider, msg: ChatMessage) -> int:
    """Turn a chat answer into flashcards attached to the first cited lecture."""
    thread = s.get(ChatThread, msg.thread_id)
    cites = [c for c in msg.citations if c.get("lecture_id")]
    lecture = s.get(Lecture, cites[0]["lecture_id"]) if cites else None
    if lecture is None and thread and thread.lecture_id:
        lecture = s.get(Lecture, thread.lecture_id)
    if lecture is None:
        raise LLMError("This answer doesn't cite a lecture, so there is nothing to attach cards to")
    subject = s.get(Subject, lecture.subject_id)
    existing = s.exec(
        select(Flashcard.front, Flashcard.back).where(
            Flashcard.subject_id == lecture.subject_id, col(Flashcard.is_current)
        )
    ).all()
    result = provider.complete(
        LLMRequest(
            task="flashcards",
            system=load_prompt("system").render(
                output_language="English",
                subject=subject.name if subject else "",
                subject_instructions="",
            ),
            context=f"Answer from the student's study chat:\n\n{msg.content}",
            instruction=load_prompt("flashcards").render(
                min=2,
                max=6,
                existing_cards=(
                    "\nThe course already has these cards; don't duplicate them:\n"
                    + "\n".join(f"- {f}" for f, _ in existing[-100:])
                    if existing
                    else ""
                ),
            )
            + "\nWrite cards only about what the answer explains. For `refs`, use the timestamps"
            " cited in the answer.",
            output=sc.FlashcardsOut,
        )
    )
    kept = [card_key(f, b) for f, b in existing]
    duration = lecture.duration_s or 10**6
    n = 0
    for c in result.parsed.cards:
        key = card_key(c.front, c.back)
        if is_duplicate(key, kept):
            continue
        refs = [
            {"type": "lecture", "lecture_id": lecture.id, "start_s": t, "end_s": t}
            for r in c.refs
            if (t := parse_ts(r.start)) is not None and t <= duration
        ] or [
            {
                "type": "lecture",
                "lecture_id": x["lecture_id"],
                "start_s": x["start_s"],
                "end_s": x["start_s"],
            }
            for x in cites[:1]
            if x.get("start_s") is not None
        ]
        s.add(
            Flashcard(
                subject_id=lecture.subject_id,
                lecture_id=lecture.id,
                type=c.type,
                front=c.front.strip(),
                back=c.back.strip(),
                tags=[*c.tags[:2], "from-chat"],
                difficulty=c.difficulty,
                source_refs=refs,
                generation=0,
                model=result.model,
            )
        )
        kept.append(key)
        n += 1
    s.commit()
    return n
