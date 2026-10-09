"""Job: transcript cleanup. Fix misheard technical terms using slide text and vocabulary.

Runs after transcription and before generation, with the cheap model. The model returns only
the lines it changed; a change is accepted only if the line stays close to the original
(it's a spelling fix, not a rewrite). Whisper's original line is kept in ``raw_text`` so the
UI can show a diff and revert. A failed cleanup never blocks the pipeline: the next step runs
on the uncorrected transcript.
"""

from __future__ import annotations

import difflib
import logging
from typing import Any

from sqlmodel import Session, col, select

from backend.ai import schemas as sc
from backend.ai.prompts import load_prompt
from backend.ai.providers import get_provider
from backend.ai.types import LLMRequest
from backend.core import jobs
from backend.core.config import get_settings
from backend.core.db import session_scope
from backend.core.events import bus
from backend.core.jobs import JobContext, JobError, handler
from backend.core.models import Lecture, LectureStatus, Subject, TranscriptSegment, utcnow
from backend.core.util import format_hms
from backend.pipeline.generate import record_usage
from backend.pipeline.slides import primary_slides, slides_text
from backend.pipeline.stages import GENERATE_JOB, lecture_ready

log = logging.getLogger(__name__)

CLEANUP_JOB = "cleanup"
MIN_SIMILARITY = 0.6
SYSTEM = (
    "You proofread automatic speech-recognition transcripts of university lectures and fix "
    "misheard technical terms. You never rewrite, translate or summarise."
)


def cleanup_enabled(provider_name: str) -> bool:
    cfg = get_settings().generation
    return cfg.cleanup and (provider_name not in ("ollama", "openai") or cfg.cleanup_local)


def queue_cleanup(s: Session, lecture_id: int, then: str) -> None:
    """``then``: "generate" (auto pipeline), "ready" (auto pipeline, no generation) or
    "index" (manual re-run: just refresh the search index)."""
    lecture = s.get(Lecture, lecture_id)
    if lecture is not None:
        lecture.stages = {**lecture.stages, "cleanup": "pending"}
        s.add(lecture)
        s.commit()
    jobs.enqueue(s, CLEANUP_JOB, lecture_id=lecture_id, payload={"then": then})


def accept(original: str, corrected: str) -> bool:
    """A fix must look like a spelling correction of the same sentence."""
    a, b = original.strip(), corrected.strip()
    if not b or a == b:
        return False
    if not 0.6 <= len(b) / max(len(a), 1) <= 1.6:
        return False
    return difflib.SequenceMatcher(None, a.lower(), b.lower()).ratio() >= MIN_SIMILARITY


def material(s: Session, lecture: Lecture) -> str:
    subject = s.get(Subject, lecture.subject_id)
    assert subject is not None
    parts = ["# Reference spellings", f"Course: {subject.name}"]
    if subject.vocabulary:
        parts.append("Vocabulary: " + ", ".join(subject.vocabulary))
    slides = slides_text(primary_slides(s, lecture.id))  # type: ignore[arg-type]
    parts.append("\n## Slide text\n" + (slides or "(no slides)"))
    return "\n".join(parts)


@handler(CLEANUP_JOB)
def cleanup(ctx: JobContext) -> None:
    lecture_id = ctx.lecture_id
    assert lecture_id is not None
    then = ctx.payload.get("then", "index")
    try:
        _cleanup(ctx)
    except Exception as e:
        log.warning("Transcript cleanup failed for lecture %s: %s", lecture_id, e)
        _set_stage(lecture_id, "failed", {"cleanup_error": str(e)})
        _continue(lecture_id, then)
        raise
    _continue(lecture_id, then)


def _cleanup(ctx: JobContext) -> None:
    lecture_id = ctx.lecture_id
    assert lecture_id is not None
    cfg = get_settings().generation
    with session_scope() as s:
        lecture = s.get(Lecture, lecture_id)
        if lecture is None:
            raise JobError("Lecture no longer exists")
        segments = s.exec(
            select(TranscriptSegment)
            .where(TranscriptSegment.lecture_id == lecture_id)
            .order_by(col(TranscriptSegment.idx))
        ).all()
        lines = [(seg.id, seg.idx, seg.start, seg.text) for seg in segments]
        context = material(s, lecture)
        subject_id = lecture.subject_id
    if not lines:
        raise JobError("This lecture has no transcript yet")
    _set_stage(lecture_id, "running")
    provider = get_provider()
    prompt = load_prompt("cleanup")
    size = max(cfg.cleanup_lines_per_call, 50)
    batches = [lines[i : i + size] for i in range(0, len(lines), size)]
    fixes: dict[int, tuple[str, str]] = {}  # segment id -> (text, reason)
    model = ""
    for n, batch in enumerate(batches):
        ctx.progress(n / len(batches), "Checking the transcript for misheard terms")
        numbered = "\n".join(f"#{idx} [{format_hms(t)}] {text}" for _, idx, t, text in batch)
        result = provider.complete(
            LLMRequest(
                task="cleanup",
                system=SYSTEM,
                context=context,
                instruction=prompt.render(lines=numbered),
                output=sc.CleanupOut,
                tier="cheap",
                max_tokens=16_000,
            )
        )
        record_usage(lecture_id, subject_id, "cleanup", result)
        model = result.model
        by_idx = {idx: (sid, text) for sid, idx, _, text in batch}
        for fix in result.parsed.fixes:
            if fix.line not in by_idx:
                continue
            sid, original = by_idx[fix.line]
            if accept(original, fix.text):
                fixes[sid] = (fix.text.strip(), fix.reason.strip())
    with session_scope() as s:
        for sid, (text, _) in fixes.items():
            seg = s.get(TranscriptSegment, sid)
            if seg is None:
                continue
            if seg.raw_text is None:
                seg.raw_text = seg.text  # keep Whisper's original across re-runs
            seg.text = text
            s.add(seg)
        s.commit()
    info: dict[str, Any] = {
        "changed": len(fixes),
        "model": model,
        "prompt_version": prompt.tag,
        "at": utcnow().isoformat(),
    }
    _set_stage(lecture_id, "done", {"cleanup": info})
    log.info("Cleanup of lecture %s changed %d line(s)", lecture_id, len(fixes))


def _set_stage(lecture_id: int, state: str, meta: dict[str, Any] | None = None) -> None:
    with session_scope() as s:
        lecture = s.get(Lecture, lecture_id)
        if lecture is None:
            return
        lecture.stages = {**lecture.stages, "cleanup": state}
        base = {k: v for k, v in lecture.meta.items() if k != "cleanup_error"}
        lecture.meta = {**base, **(meta or {})}
        s.add(lecture)
        s.commit()
        subject_id = lecture.subject_id
    bus.publish("lecture", {"id": lecture_id, "subject_id": subject_id})


def _continue(lecture_id: int, then: str) -> None:
    from backend.search.index import queue_index

    title = ""
    with session_scope() as s:
        lecture = s.get(Lecture, lecture_id)
        if lecture is None:
            return
        if then == "generate":
            lecture.stages = {**lecture.stages, "generate": "pending"}
            s.add(lecture)
            s.commit()
            jobs.enqueue(s, GENERATE_JOB, lecture_id=lecture_id)
        else:
            if then == "ready":
                lecture.status = LectureStatus.ready
                s.add(lecture)
                s.commit()
            queue_index(s, lecture_id)
        title, subject_id = lecture.title, lecture.subject_id
    bus.publish("lecture", {"id": lecture_id, "subject_id": subject_id})
    if then == "ready":
        lecture_ready(lecture_id, title)


def revert(s: Session, segment_ids: list[int] | None, lecture_id: int) -> int:
    """Restore Whisper's text for the given segments (or all of the lecture's)."""
    q = select(TranscriptSegment).where(
        TranscriptSegment.lecture_id == lecture_id, col(TranscriptSegment.raw_text).is_not(None)
    )
    if segment_ids is not None:
        q = q.where(col(TranscriptSegment.id).in_(segment_ids))
    n = 0
    for seg in s.exec(q).all():
        seg.text, seg.raw_text = seg.raw_text or seg.text, None
        s.add(seg)
        n += 1
    s.commit()
    return n
