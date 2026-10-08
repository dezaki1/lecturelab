"""Job: generate study material for a lecture (SPEC §9).

All calls share one cacheable prefix: the system prompt (global rules + subject instructions)
and the lecture context (metadata, earlier lectures, bookmarks, timestamped transcript). The
first call runs alone so it writes the cache; the rest run in parallel and read it.

Every item carries ``source_refs`` built from the timestamps the model cites; refs that don't
parse or fall outside the lecture are dropped, and items left without refs are flagged.
Regenerating never deletes edited notes (old versions are kept) or cards/questions that were
edited or already reviewed.
"""

from __future__ import annotations

import difflib
import logging
import re
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel
from sqlmodel import Session, col, func, select

from backend.ai import schemas as sc
from backend.ai.prompts import load_prompt
from backend.ai.providers import Provider, get_provider
from backend.ai.types import LLMError, LLMRequest, LLMResult
from backend.core.config import get_settings
from backend.core.db import session_scope
from backend.core.events import bus
from backend.core.jobs import JobContext, JobError, handler
from backend.core.models import (
    Bookmark,
    Flashcard,
    Lecture,
    LectureStatus,
    Note,
    Question,
    Subject,
    TranscriptSegment,
    UsageRecord,
    utcnow,
)
from backend.core.util import format_hms
from backend.pipeline.stages import GENERATE_JOB, lecture_ready

log = logging.getLogger(__name__)

NOTE_KINDS = ("summary", "notes", "glossary", "announcements")
ITEM_KINDS = ("flashcards", "exercises", "questions")
ALL_KINDS = (*NOTE_KINDS, *ITEM_KINDS)
SHARED_OUTPUTS = {k: sc.OUTPUTS[k] for k in ALL_KINDS}
LANGUAGE_NAMES = {"en": "English", "de": "German"}
TS = re.compile(r"^(?:(\d+):)?(\d{1,3}):(\d{2})$")
REF_TOLERANCE_S = 5.0


# Timestamps & refs ----------------------------------------------------------------------------
def parse_ts(text: str) -> float | None:
    m = TS.match(text.strip().strip("[]"))
    if not m:
        return None
    h, mins, secs = int(m.group(1) or 0), int(m.group(2)), int(m.group(3))
    if secs >= 60:
        return None
    return h * 3600 + mins * 60 + secs


def to_refs(lecture_id: int, refs: list[sc.Ref], duration: float) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for r in refs:
        start, end = parse_ts(r.start), parse_ts(r.end)
        if start is None or start > duration + REF_TOLERANCE_S:
            continue
        if end is None or end < start:
            end = start
        out.append(
            {
                "type": "lecture",
                "lecture_id": lecture_id,
                "start_s": start,
                "end_s": min(end, duration),
            }
        )
    return out


def ts_ref(lecture_id: int, ts: str, duration: float) -> list[dict[str, Any]]:
    return to_refs(lecture_id, [sc.Ref(start=ts, end=ts)], duration)


def chip(refs: list[dict[str, Any]]) -> str:
    return " ".join(f"[{format_hms(r['start_s'])}]" for r in refs[:3])


# Context --------------------------------------------------------------------------------------
@dataclass
class LectureContext:
    lecture_id: int
    subject_id: int
    duration: float
    system: str
    context: str
    title_is_default: bool
    lines: list[tuple[float, float, str]]  # (start, end, text) for verification excerpts


def lecture_number(session: Session, lecture: Lecture) -> int:
    ids = session.exec(
        select(Lecture.id)
        .where(Lecture.subject_id == lecture.subject_id)
        .order_by(col(Lecture.started_at), col(Lecture.id))
    ).all()
    return ids.index(lecture.id) + 1


def build_context(session: Session, lecture: Lecture) -> LectureContext:
    subject = session.get(Subject, lecture.subject_id)
    assert subject is not None
    segments = session.exec(
        select(TranscriptSegment)
        .where(TranscriptSegment.lecture_id == lecture.id)
        .order_by(col(TranscriptSegment.idx))
    ).all()
    if not segments:
        raise JobError("This lecture has no transcript yet")
    duration = lecture.duration_s or segments[-1].end
    number = lecture_number(session, lecture)

    out_lang = LANGUAGE_NAMES.get(subject.output_language, subject.output_language or "English")
    instructions = subject.instructions.strip()
    if subject.vocabulary:
        instructions += "\nCourse vocabulary (correct spellings): " + ", ".join(subject.vocabulary)
    system = load_prompt("system").render(
        output_language=out_lang,
        subject=f"{subject.name}{f' ({subject.course_code})' if subject.course_code else ''}",
        subject_instructions=f"Course-specific instructions: {instructions}"
        if instructions
        else "",
    )

    parts = [
        "# Lecture material",
        f"Course: {subject.name}",
        f'Lecture: L{number} "{lecture.title}"'
        + (f" ({lecture.kind})" if lecture.kind else "")
        + f", {lecture.started_at.astimezone():%Y-%m-%d}, duration {format_hms(duration)}"
        + (f", spoken language: {lecture.language}" if lecture.language else ""),
    ]
    earlier = _earlier_lectures(session, lecture)
    parts.append("\n## Earlier lectures in this course")
    parts.append("\n".join(earlier) if earlier else "(none yet)")
    bookmarks = session.exec(
        select(Bookmark).where(Bookmark.lecture_id == lecture.id).order_by(col(Bookmark.t_s))
    ).all()
    parts.append("\n## Student bookmarks")
    parts.append(
        "\n".join(
            f"- [{format_hms(b.t_s)}] {b.kind.value}" + (f": {b.note}" if b.note else "")
            for b in bookmarks
        )
        or "(none)"
    )
    parts.append("\n## Transcript")
    parts.extend(f"[{format_hms(s.start)}] {s.text}" for s in segments)
    return LectureContext(
        lecture_id=lecture.id,  # type: ignore[arg-type]
        subject_id=lecture.subject_id,
        duration=duration,
        system=system,
        context="\n".join(parts),
        title_is_default=bool(lecture.meta.get("title_is_default")),
        lines=[(s.start, s.end, s.text) for s in segments],
    )


def _earlier_lectures(session: Session, lecture: Lecture) -> list[str]:
    rows = session.exec(
        select(Lecture)
        .where(Lecture.subject_id == lecture.subject_id, Lecture.started_at < lecture.started_at)
        .order_by(col(Lecture.started_at))
    ).all()
    out = []
    for i, prev in enumerate(rows, start=1):
        note = session.exec(
            select(Note).where(
                Note.lecture_id == prev.id,
                Note.kind == "summary",
                col(Note.is_current),
            )
        ).first()
        bullets = [b["text"] for b in (note.data.get("tldr") or [])[:3]] if note else []
        line = f'- L{i} ({prev.started_at.astimezone():%Y-%m-%d}) "{prev.title}"'
        out.append(line + (": " + " ".join(bullets) if bullets else ""))
    return out[-12:]  # keep the context bounded for long semesters


def excerpt(
    lines: list[tuple[float, float, str]], refs: list[dict[str, Any]], pad: float = 15
) -> str:
    chunks = []
    for r in refs[:3]:
        lo, hi = r["start_s"] - pad, r["end_s"] + pad
        chunks.append(" ".join(f"[{format_hms(a)}] {t}" for a, _, t in lines if lo <= a <= hi))
    return "\n".join(chunks) or "(no excerpt)"


# Calls & usage --------------------------------------------------------------------------------
class Runner:
    def __init__(self, ctx: LectureContext, provider: Provider) -> None:
        self.ctx = ctx
        self.provider = provider

    def call(
        self, task: str, instruction: str, output: type[BaseModel], tier: str = "main"
    ) -> LLMResult:
        # All main-tier generation calls for a lecture send the same set of output schemas, so
        # they share one cached prefix (see LLMRequest.shared_outputs).
        shared = SHARED_OUTPUTS if tier == "main" and task in SHARED_OUTPUTS else None
        req = LLMRequest(
            task=task,
            system=self.ctx.system,
            context=self.ctx.context,
            instruction=instruction,
            output=output,
            tier=tier,
            shared_outputs=shared,
        )
        result = self.provider.complete(req)
        cost = result.usage.cost(result.model)
        with session_scope() as s:
            s.add(
                UsageRecord(
                    lecture_id=self.ctx.lecture_id,
                    subject_id=self.ctx.subject_id,
                    task=task,
                    provider=result.provider,
                    model=result.model,
                    input_tokens=result.usage.input_tokens,
                    output_tokens=result.usage.output_tokens,
                    cache_read_tokens=result.usage.cache_read_tokens,
                    cache_write_tokens=result.usage.cache_write_tokens,
                    cost_usd=cost,
                )
            )
            lecture = s.get(Lecture, self.ctx.lecture_id)
            if lecture is not None:
                lecture.cost_usd = (lecture.cost_usd or 0) + cost
                s.add(lecture)
            s.commit()
        return result


# Notes ----------------------------------------------------------------------------------------
def render_summary(out: sc.SummaryOut, refs: Callable[[list[sc.Ref]], list[dict]]) -> str:
    lines = ["## TL;DR", ""]
    lines += [f"- {b.text} {chip(refs(b.refs))}".rstrip() for b in out.tldr]
    lines += ["", "## If you remember only 3 things", ""]
    lines += [f"{i}. {b.text} {chip(refs(b.refs))}".rstrip() for i, b in enumerate(out.remember, 1)]
    return "\n".join(lines) + "\n"


def render_notes(out: sc.NotesOut, refs: Callable[[list[sc.Ref]], list[dict]]) -> str:
    parts = []
    for sec in out.sections:
        star = "★ " if sec.emphasized else ""
        parts.append(f"## {star}{sec.heading} {chip(refs(sec.refs))}".rstrip())
        parts.append(sec.markdown.strip())
    if out.open_questions:
        parts.append("## Open questions")
        parts.append("\n".join(f"- {q}" for q in out.open_questions))
    return "\n\n".join(parts) + "\n"


def render_glossary(out: sc.GlossaryOut) -> str:
    rows = ["| Term | Definition | German | First mention |", "|---|---|---|---|"]
    for t in out.terms:
        cell = lambda x: x.replace("|", "\\|").replace("\n", " ")  # noqa: E731
        rows.append(
            f"| **{cell(t.term)}** | {cell(t.definition)} | {cell(t.german)} "
            f"| [{t.first_mention}] |"
        )
    return "\n".join(rows) + "\n"


def render_announcements(out: sc.AnnouncementsOut) -> str:
    if not out.items:
        return "_No announcements in this lecture._\n"
    return (
        "\n".join(
            f"- **{a.kind.title()}**{f' ({a.date})' if a.date else ''}: {a.text} [{a.at}]"
            for a in out.items
        )
        + "\n"
    )


def save_note(
    ctx: LectureContext,
    kind: str,
    content: str,
    data: dict,
    refs: list[dict],
    result: LLMResult,
    prompt_tag: str,
    instructions: str,
) -> Note:
    with session_scope() as s:
        prev = s.exec(
            select(Note).where(
                Note.lecture_id == ctx.lecture_id, Note.kind == kind, col(Note.is_current)
            )
        ).all()
        version = (
            s.exec(
                select(func.max(Note.version)).where(
                    Note.lecture_id == ctx.lecture_id, Note.kind == kind
                )
            ).one()
            or 0
        ) + 1
        for p in prev:
            p.is_current = False
            s.add(p)
        note = Note(
            subject_id=ctx.subject_id,
            lecture_id=ctx.lecture_id,
            kind=kind,
            version=version,
            content_md=content,
            data=data,
            source_refs=refs,
            model=result.model,
            prompt_version=prompt_tag,
            instructions=instructions,
        )
        s.add(note)
        s.commit()
        s.refresh(note)
        return note


def gen_note(r: Runner, kind: str, instructions: str) -> str:
    ctx = r.ctx
    prompt = load_prompt(kind)
    instruction = prompt.render() + _extra(instructions)
    result = r.call(kind, instruction, sc.OUTPUTS[kind])
    out = result.parsed

    def refs(rs: list[sc.Ref]) -> list[dict]:
        return to_refs(ctx.lecture_id, rs, ctx.duration)

    all_refs: list[dict] = []
    if kind == "summary":
        content = render_summary(out, refs)
        for b in [*out.tldr, *out.remember]:
            all_refs += refs(b.refs)
        if ctx.title_is_default and out.topic_title.strip():
            _set_title(ctx.lecture_id, out.topic_title.strip())
    elif kind == "notes":
        content = render_notes(out, refs)
        for sec in out.sections:
            all_refs += refs(sec.refs)
    elif kind == "glossary":
        content = render_glossary(out)
        for t in out.terms:
            all_refs += ts_ref(ctx.lecture_id, t.first_mention, ctx.duration)
    else:
        content = render_announcements(out)
        for a in out.items:
            all_refs += ts_ref(ctx.lecture_id, a.at, ctx.duration)
    tag = f"{prompt.tag}+{load_prompt('system').tag}"
    save_note(ctx, kind, content, out.model_dump(), all_refs, result, tag, instructions)
    return kind


def _set_title(lecture_id: int, title: str) -> None:
    with session_scope() as s:
        lecture = s.get(Lecture, lecture_id)
        if lecture is not None and lecture.meta.get("title_is_default"):
            lecture.title = title[:120]
            lecture.meta = {**lecture.meta, "title_is_default": False}
            s.add(lecture)
            s.commit()


def _extra(instructions: str) -> str:
    return (
        f"\n\nAdditional instructions from the student: {instructions.strip()}"
        if instructions.strip()
        else ""
    )


# Cards & questions ----------------------------------------------------------------------------
def _amount(minutes: float, per10: tuple[float, float], floor: int) -> tuple[int, int]:
    lo = max(floor, round(minutes / 10 * per10[0]))
    return lo, max(lo + 1, round(minutes / 10 * per10[1]))


def _norm(text: str) -> str:
    return re.sub(r"\W+", " ", text.lower()).strip()


def card_key(front: str, back: str) -> str:
    """Front and back together: cards with near-identical questions but different answers
    ("eigenvectors" vs "eigenvalues") are different cards."""
    return _norm(f"{front} || {back}")


def is_duplicate(key: str, existing: list[str], threshold: float = 0.9) -> bool:
    n = _norm(key)
    return any(difflib.SequenceMatcher(None, n, e).ratio() >= threshold for e in existing)


def _next_generation(
    s: Session, model: type[Flashcard] | type[Question], lecture_id: int, **where: Any
) -> int:
    q = select(func.max(model.generation)).where(model.lecture_id == lecture_id)
    for k, v in where.items():
        q = q.where(getattr(model, k) == v)
    return (s.exec(q).one() or 0) + 1


def _retire_previous(
    s: Session, model: type[Flashcard] | type[Question], lecture_id: int, **where: Any
) -> None:
    """Old generation stops being current unless the student edited or reviewed it."""
    q = select(model).where(model.lecture_id == lecture_id, col(model.is_current))
    for k, v in where.items():
        q = q.where(getattr(model, k) == v)
    for item in s.exec(q).all():
        reviewed = bool(getattr(item, "fsrs", None))
        if not item.edited_by_me and not reviewed:
            item.is_current = False
            s.add(item)


def gen_flashcards(r: Runner, instructions: str) -> str:
    ctx, cfg = r.ctx, get_settings().generation
    lo, hi = _amount(ctx.duration / 60, cfg.cards_per_10min, cfg.min_items)
    with session_scope() as s:
        existing = s.exec(
            select(Flashcard.front).where(
                Flashcard.subject_id == ctx.subject_id,
                col(Flashcard.is_current),
                Flashcard.lecture_id != ctx.lecture_id,
            )
        ).all()
    existing_txt = (
        "\nThe course already has these cards; don't duplicate them:\n"
        + "\n".join(f"- {f}" for f in existing[-150:])
        if existing
        else ""
    )
    prompt = load_prompt("flashcards")
    result = r.call(
        "flashcards",
        prompt.render(min=lo, max=hi, existing_cards=existing_txt) + _extra(instructions),
        sc.FlashcardsOut,
    )
    cards: list[Flashcard] = []
    with session_scope() as s:
        gen = _next_generation(s, Flashcard, ctx.lecture_id)
        _retire_previous(s, Flashcard, ctx.lecture_id)
        s.commit()
        kept = [
            card_key(f, b)
            for f, b in s.exec(
                select(Flashcard.front, Flashcard.back).where(
                    Flashcard.subject_id == ctx.subject_id,
                    col(Flashcard.is_current),
                )
            ).all()
        ]
        for c in result.parsed.cards:
            key = card_key(c.front, c.back)
            if not c.front.strip() or is_duplicate(key, kept):
                continue
            refs = to_refs(ctx.lecture_id, c.refs, ctx.duration)
            card = Flashcard(
                subject_id=ctx.subject_id,
                lecture_id=ctx.lecture_id,
                type=c.type,
                front=c.front.strip(),
                back=c.back.strip(),
                tags=c.tags[:3],
                difficulty=c.difficulty,
                source_refs=refs,
                generation=gen,
                model=result.model,
                verified="unchecked" if refs else "flagged",
                verify_note="" if refs else "No valid source timestamp",
            )
            kept.append(key)
            s.add(card)
            cards.append(card)
        s.commit()
        for c in cards:
            s.refresh(c)
    verify_items(r, Flashcard, [(c.id, f"Q: {c.front}\nA: {c.back}", c.source_refs) for c in cards])  # type: ignore[misc]
    return "flashcards"


def gen_questions(r: Runner, kind: str, instructions: str) -> str:
    """kind: "questions" (exam) or "exercises"."""
    ctx, cfg = r.ctx, get_settings().generation
    minutes = ctx.duration / 60
    per10 = cfg.questions_per_10min if kind == "questions" else cfg.exercises_per_10min
    lo, hi = _amount(minutes, per10, 1 if kind == "exercises" else cfg.min_items)
    prompt = load_prompt(kind)
    output = sc.QuestionsOut if kind == "questions" else sc.ExercisesOut
    result = r.call(kind, prompt.render(min=lo, max=hi) + _extra(instructions), output)
    qkind = "exam" if kind == "questions" else "exercise"
    rows: list[Question] = []
    with session_scope() as s:
        gen = _next_generation(s, Question, ctx.lecture_id, kind=qkind)
        _retire_previous(s, Question, ctx.lecture_id, kind=qkind)
        s.commit()
        if kind == "questions":
            for q in result.parsed.questions:
                rows.append(
                    Question(
                        subject_id=ctx.subject_id,
                        lecture_id=ctx.lecture_id,
                        kind="exam",
                        type=q.type,
                        bloom=q.bloom,
                        difficulty=q.difficulty,
                        points=q.points,
                        prompt=q.prompt,
                        options=[o.model_dump() for o in q.options],
                        answer=q.answer,
                        explanation=q.explanation,
                        rubric=[x.model_dump() for x in q.rubric],
                        emphasized=q.emphasized,
                        source_refs=to_refs(ctx.lecture_id, q.refs, ctx.duration),
                        generation=gen,
                        model=result.model,
                    )
                )
        else:
            for e in result.parsed.exercises:
                rows.append(
                    Question(
                        subject_id=ctx.subject_id,
                        lecture_id=ctx.lecture_id,
                        kind="exercise",
                        type=e.kind,
                        title=e.title,
                        difficulty=e.difficulty,
                        prompt=e.prompt,
                        answer=e.answer,
                        hints=e.hints,
                        solution_steps=e.solution_steps,
                        expected_output=e.expected_output,
                        source_refs=to_refs(ctx.lecture_id, e.refs, ctx.duration),
                        generation=gen,
                        model=result.model,
                    )
                )
        for q in rows:
            if not q.source_refs:
                q.verified, q.verify_note = "flagged", "No valid source timestamp"
            s.add(q)
        s.commit()
        for q in rows:
            s.refresh(q)
    verify_items(
        r,
        Question,
        [(q.id, item_text(q), q.source_refs) for q in rows],  # type: ignore[misc]
    )
    return kind


def item_text(q: Question) -> str:
    """Everything the verifier needs to judge a question or exercise, incl. its solution."""
    kind = "Practice exercise" if q.kind == "exercise" else f"Exam question ({q.type})"
    parts = [f"{kind}: {q.title}" if q.title else kind, q.prompt]
    for o in q.options:
        parts.append(f"- [{'x' if o.get('correct') else ' '}] {o.get('text', '')}")
    if q.solution_steps:
        parts.append("Solution: " + " ".join(q.solution_steps))
    parts.append(f"Answer: {q.answer}")
    if q.expected_output:
        parts.append(f"Expected output: {q.expected_output}")
    return "\n".join(p for p in parts if p)


def verify_items(
    r: Runner, model: type[Flashcard] | type[Question], items: list[tuple[int, str, list[dict]]]
) -> None:
    """Cheap second pass: check each item against the transcript excerpt it cites."""
    items = [it for it in items if it[2]]
    if not items or not get_settings().generation.verify:
        return
    blocks = [
        f"- id={i}\n  Item: {text}\n  Cited excerpt: {excerpt(r.ctx.lines, refs)}"
        for i, text, refs in items
    ]
    try:
        result = r.call(
            "verify",
            load_prompt("verify").render(items="\n".join(blocks)),
            sc.VerifyOut,
            tier="cheap",
        )
    except LLMError as e:
        log.warning("Verification skipped: %s", e)
        return
    verdicts = {v.id: v for v in result.parsed.verdicts}
    with session_scope() as s:
        for i, _, _ in items:
            row = s.get(model, i)
            v = verdicts.get(i)
            if row is None or v is None:
                continue
            row.verified = "ok" if v.supported else "flagged"
            row.verify_note = "" if v.supported else v.issue
            s.add(row)
        s.commit()


# Job ------------------------------------------------------------------------------------------
@handler(GENERATE_JOB)
def generate(ctx_job: JobContext) -> None:
    kinds = [k for k in (ctx_job.payload.get("kinds") or ALL_KINDS) if k in ALL_KINDS]
    instructions = ctx_job.payload.get("instructions") or ""
    lecture_id = ctx_job.lecture_id
    assert lecture_id is not None
    with session_scope() as s:
        lecture = s.get(Lecture, lecture_id)
        if lecture is None:
            raise JobError("Lecture no longer exists")
        ctx = build_context(s, lecture)
        lecture.stages = {**lecture.stages, "generate": "running"}
        lecture.status = LectureStatus.processing
        s.add(lecture)
        s.commit()
        subject_id = lecture.subject_id
    bus.publish("lecture", {"id": lecture_id, "subject_id": subject_id})

    try:
        provider = get_provider()
    except LLMError as e:
        _finish(lecture_id, {k: str(e) for k in kinds}, kinds)
        raise JobError(str(e)) from e

    done: list[str] = []
    errors: dict[str, str] = {}

    def progress(kind: str) -> None:
        done.append(kind)
        ctx_job.progress(len(done) / len(kinds), f"Generated {len(done)}/{len(kinds)}")
        bus.publish("lecture", {"id": lecture_id, "subject_id": subject_id})

    runner = Runner(ctx, provider)

    def run(kind: str) -> str:
        if kind in NOTE_KINDS:
            return gen_note(runner, kind, instructions)
        if kind == "flashcards":
            return gen_flashcards(runner, instructions)
        return gen_questions(runner, kind, instructions)

    ctx_job.progress(0.0, f"Generating {len(kinds)} section(s)")
    # First call alone: it writes the prompt cache that the parallel calls then read.
    first, rest = kinds[0], kinds[1:]
    for batch, workers in (([first], 1), (rest, 3)):
        if not batch:
            continue
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(run, k): k for k in batch}
            for fut in as_completed(futures):
                kind = futures[fut]
                try:
                    fut.result()
                    progress(kind)
                except (LLMError, JobError) as e:
                    errors[kind] = str(e)
                except Exception as e:
                    log.exception("Generating %s failed", kind)
                    errors[kind] = f"{type(e).__name__}: {e}"
    _finish(lecture_id, errors, kinds)
    if errors and len(errors) == len(kinds):
        raise JobError(next(iter(errors.values())))


def _finish(lecture_id: int, errors: dict[str, str], kinds: list[str]) -> None:
    with session_scope() as s:
        lecture = s.get(Lecture, lecture_id)
        if lecture is None:
            return
        ok = len(errors) < len(kinds)
        lecture.stages = {**lecture.stages, "generate": "done" if not errors else "failed"}
        lecture.status = LectureStatus.ready if ok else LectureStatus.failed
        lecture.error = (
            "Generation failed for: " + "; ".join(f"{k}: {v}" for k, v in errors.items())
            if errors
            else None
        )
        lecture.meta = {**lecture.meta, "generated_at": utcnow().isoformat()}
        s.add(lecture)
        s.commit()
        if ok:
            from backend.search.index import queue_index

            queue_index(s, lecture_id)
        subject_id, title = lecture.subject_id, lecture.title
    bus.publish("lecture", {"id": lecture_id, "subject_id": subject_id})
    if len(errors) < len(kinds):
        lecture_ready(lecture_id, title)
    else:
        bus.publish(
            "notify",
            {
                "title": "Generation failed",
                "message": next(iter(errors.values())),
                "level": "error",
            },
        )


def estimate(session: Session, lecture: Lecture) -> dict[str, Any]:
    """Rough cost estimate for generating everything with the main model."""
    from backend.ai.providers import active_provider_name
    from backend.ai.types import PRICING

    chars = sum(
        len(t) + 9
        for t in session.exec(
            select(TranscriptSegment.text).where(TranscriptSegment.lecture_id == lecture.id)
        ).all()
    )
    ctx_tokens = int(chars / 3.6) + 1500
    minutes = (lecture.duration_s or 0) / 60
    provider = active_provider_name()
    model = get_settings().llm.anthropic_main
    out_tokens = int(9000 + minutes * 330)  # incl. thinking, measured on test lectures
    if provider != "anthropic" or model not in PRICING:
        return {"provider": provider, "usd": 0.0, "context_tokens": ctx_tokens}
    p = PRICING[model]
    calls = len(ALL_KINDS)
    usd = (ctx_tokens * p[2] + (calls - 1) * ctx_tokens * p[3] + out_tokens * p[1]) / 1e6
    return {
        "provider": provider,
        "model": model,
        "usd": round(usd * 1.1, 2),
        "context_tokens": ctx_tokens,
    }
