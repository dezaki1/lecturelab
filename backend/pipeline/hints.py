"""Exam hints: moments the lecturer flags as important.

A keyword detector (English + German) runs at transcription and needs no LLM, so offline
lectures get hints too. During generation the "hints" call confirms them, adds repeated points
and writes a one-line topic for each; those replace the keyword hints. The hint list is then
appended to the notes/cards/questions instructions (after the prompt-cache breakpoint), so
they mark these points ★ and weight them.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any, Protocol

from sqlmodel import Session, col, delete, select

from backend.core.models import ExamHint, Lecture, TranscriptSegment
from backend.core.util import format_hms

EXAM = re.compile(
    r"\b(?:in|on|for) the (?:exam|final|test)\b|\bexam (?:question|relevant|material)s?\b"
    r"|\bwill be (?:asked|tested|examined)\b|\bprüfungsrelevant\b"
    r"|\b(?:in|bei) der (?:prüfung|klausur)\b|\bkommt (?:in|bei) der (?:prüfung|klausur)\b",
    re.I,
)
IMPORTANT = re.compile(
    r"\b(?:this|that)(?: i|')s (?:very |really |super |extremely )?(?:important|crucial|key)\b"
    r"|\bremember (?:this|that)\b|\bkey (?:idea|point|takeaway|insight)\b"
    r"|\bmake sure (?:that )?you understand\b|\byou (?:need|have|must) to know\b"
    r"|\bdon'?t forget\b|\bdas ist (?:sehr |ganz |besonders )?wichtig\b|\bmerken sie sich\b"
    r"|\bwichtig ist\b",
    re.I,
)
SPAN_S = 30.0  # a hint covers the explanation that follows the signal
MERGE_S = 20.0


class _Seg(Protocol):
    start: float
    end: float
    text: str


def detect(segments: Sequence[_Seg]) -> list[dict[str, Any]]:
    """Keyword hints: [{start_s, end_s, kind, quote}], nearby matches merged."""
    out: list[dict[str, Any]] = []
    for i, seg in enumerate(segments):
        kind = (
            "exam" if EXAM.search(seg.text) else "important" if IMPORTANT.search(seg.text) else ""
        )
        if not kind:
            continue
        end = seg.end
        for nxt in segments[i + 1 :]:
            if nxt.start > seg.start + SPAN_S:
                break
            end = nxt.end
        if out and seg.start - out[-1]["start_s"] <= MERGE_S:
            prev = out[-1]
            prev["end_s"] = max(prev["end_s"], end)
            if kind == "exam":
                prev["kind"] = "exam"
            continue
        out.append({"start_s": seg.start, "end_s": end, "kind": kind, "quote": seg.text.strip()})
    return out


def store_rule_hints(s: Session, lecture: Lecture) -> int:
    """Replace the lecture's hints with keyword hints (after (re-)transcription)."""
    segments = s.exec(
        select(TranscriptSegment)
        .where(TranscriptSegment.lecture_id == lecture.id)
        .order_by(col(TranscriptSegment.idx))
    ).all()
    s.exec(delete(ExamHint).where(ExamHint.lecture_id == lecture.id))  # type: ignore[call-overload]
    found = detect(segments)
    for h in found:
        s.add(ExamHint(lecture_id=lecture.id, subject_id=lecture.subject_id, source="rule", **h))  # type: ignore[arg-type]
    lecture.meta = {**lecture.meta, "hints_scanned": True}
    s.add(lecture)
    return len(found)


def lecture_hints(s: Session, lecture_id: int) -> list[ExamHint]:
    return list(
        s.exec(
            select(ExamHint)
            .where(ExamHint.lecture_id == lecture_id)
            .order_by(col(ExamHint.start_s))
        ).all()
    )


def candidates_text(hints: list[ExamHint]) -> str:
    if not hints:
        return "A keyword search found no candidate phrases; there may still be hints."
    lines = [f'- [{format_hms(h.start_s)}] "{h.quote}"' for h in hints]
    return (
        "A keyword search found these candidate moments. Check each one (keep only real "
        "signals) and look for others it missed:\n" + "\n".join(lines)
    )


def hints_block(hints: list[ExamHint]) -> str:
    """Appended to the generation instructions."""
    if not hints:
        return ""
    lines = []
    for h in hints:
        what = h.topic or h.quote
        quote = f' ("{h.quote}")' if h.topic and h.quote else ""
        lines.append(f"- [{format_hms(h.start_s)}-{format_hms(h.end_s)}] {h.kind}: {what}{quote}")
    return (
        "\n\nExam hints: the lecturer flagged these moments. Mark content from them with ★ and "
        "give them extra weight (cards and questions should cover them):\n" + "\n".join(lines)
    )


def backfill(s: Session) -> int:
    """Keyword hints for transcribed lectures from before this feature."""
    n = 0
    for lecture in s.exec(select(Lecture)).all():
        if lecture.stages.get("transcribe") == "done" and not lecture.meta.get("hints_scanned"):
            store_rule_hints(s, lecture)
            n += 1
    s.commit()
    return n
