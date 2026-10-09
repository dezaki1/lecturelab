"""Past exams: read an old exam PDF and describe its format, style and topic weighting.

The ``document`` job extracts the text (OCR for scanned exams, which is common for old ones),
then queues ``past_exam``, which asks the main model for a ``PastExamOut`` stored in
``Material.data["analysis"]``. ``style_block`` turns a subject's analysed exams into the
instructions that make generated exam questions and simulated exams match them (SPEC §7, §9).
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from sqlmodel import Session, col, select

from backend.ai import schemas as sc
from backend.ai.prompts import load_prompt
from backend.ai.providers import get_provider, is_configured
from backend.ai.types import LLMRequest
from backend.core import jobs
from backend.core.db import session_scope
from backend.core.events import bus
from backend.core.jobs import JobContext, JobError, handler
from backend.core.models import Job, JobStatus, Material, Subject

log = logging.getLogger(__name__)
PAGE_MARK = re.compile(r"^\[page \d+\]$", re.M)

PAST_EXAM_JOB = "past_exam"
PAST_EXAM_KIND = "past_exam"
MAX_TEXT_CHARS = 60_000  # a long exam is ~15k chars; scripts uploaded by mistake are cut
MAX_EXAMPLES = 12  # verbatim example questions passed on to question writers
SYSTEM = (
    "You analyse old university exams so that new practice exams can match them. Describe "
    "only what the exam paper shows; don't invent points, durations or topics."
)


def has_text(extracted: str) -> bool:
    return bool(PAGE_MARK.sub("", extracted).strip())


def queue_analysis(s: Session, material_id: int) -> bool:
    if not is_configured():
        return False
    jobs.enqueue(s, PAST_EXAM_JOB, payload={"material_id": material_id})
    return True


def is_analysing(s: Session, material_id: int) -> bool:
    active = s.exec(
        select(Job).where(
            Job.kind == PAST_EXAM_JOB, col(Job.status).in_([JobStatus.queued, JobStatus.running])
        )
    ).all()
    return any(j.payload.get("material_id") == material_id for j in active)


def ocr_pages(pdf_path: Path, pages_dir: Path, page_count: int, ctx: JobContext) -> list[str]:
    """Scanned exam: render every page and read it with the local OCR worker."""
    from backend.pipeline.documents import render_pages
    from backend.pipeline.slides import run_ocr

    files = render_pages(pdf_path, pages_dir, list(range(1, page_count + 1)), ctx)
    ctx.progress(0.6, "Reading the scanned pages")
    return run_ocr([files[p] for p in sorted(files)], ctx)


@handler(PAST_EXAM_JOB)
def analyse_past_exam(ctx: JobContext) -> None:
    material_id = ctx.payload["material_id"]
    with session_scope() as s:
        m = s.get(Material, material_id)
        if m is None:
            return  # deleted while queued
        subject = s.get(Subject, m.subject_id)
        text, subject_id = m.extracted_text, m.subject_id
        name = subject.name if subject else ""
    if not has_text(text):
        raise JobError("No text could be read from this exam")
    ctx.progress(0.1, "Analysing the past exam")
    prompt = load_prompt("past_exam")
    result = get_provider().complete(
        LLMRequest(
            task="past_exam",
            system=SYSTEM,
            context=f"# Past exam of {name}\n\n{text[:MAX_TEXT_CHARS]}",
            instruction=prompt.render(subject=name),
            output=sc.PastExamOut,
        )
    )
    from backend.pipeline.generate import record_usage

    record_usage(None, subject_id, "past_exam", result)
    out: sc.PastExamOut = result.parsed
    with session_scope() as s:
        m = s.get(Material, material_id)
        if m is None:
            return
        m.data = {
            **m.data,
            "analysis": out.model_dump(),
            "model": result.model,
            "prompt_version": prompt.tag,
        }
        s.add(m)
        s.commit()
    bus.publish("subject_documents", {"subject_id": subject_id})
    log.info("Past exam %s analysed: %d questions", material_id, len(out.questions))


def analysed_exams(s: Session, subject_id: int) -> list[sc.PastExamOut]:
    rows = s.exec(
        select(Material)
        .where(Material.subject_id == subject_id, Material.kind == PAST_EXAM_KIND)
        .order_by(col(Material.id))
    ).all()
    out = []
    for m in rows:
        if m.data.get("analysis"):
            try:
                out.append(sc.PastExamOut.model_validate(m.data["analysis"]))
            except ValueError:
                log.warning("Ignoring unreadable analysis of past exam %s", m.id)
    return out


def points_per_minute(exams: list[sc.PastExamOut]) -> float | None:
    rates = [e.total_points / e.duration_min for e in exams if e.total_points and e.duration_min]
    return sum(rates) / len(rates) if rates else None


def topic_weights(exams: list[sc.PastExamOut]) -> list[tuple[str, float]]:
    """Share of points per topic across the past exams (topic names as the analyses wrote them)."""
    totals: dict[str, float] = {}
    names: dict[str, str] = {}
    for e in exams:
        pts = sum(t.points for t in e.topics)
        for t in e.topics:
            key = t.name.casefold().strip()
            names.setdefault(key, t.name.strip())
            share = t.points / pts if pts else 1 / max(len(e.topics), 1)
            totals[key] = totals.get(key, 0.0) + share / len(exams)
    return sorted(((names[k], v) for k, v in totals.items()), key=lambda x: -x[1])


def style_block(s: Session, subject_id: int) -> str:
    """Appended to exam-question instructions: how the real exams of this course look."""
    exams = analysed_exams(s, subject_id)
    if not exams:
        return ""
    lines = ["\n\nPast exams of this course. Match their format, style, depth and topic weighting:"]
    for e in exams:
        facts = [x for x in (e.date, f"{e.duration_min} min" if e.duration_min else "",
                             f"{e.total_points:g} points" if e.total_points else "",
                             f"aids: {e.aids}" if e.aids else "") if x]  # fmt: skip
        lines.append(f"- {e.title}" + (f" ({', '.join(facts)})" if facts else "") + f": {e.format}")
        lines += [f"  - {x}" for x in e.style[:6]]
    weights = topic_weights(exams)
    if weights:
        lines.append(
            "Topic weighting in past exams: "
            + "; ".join(f"{name} {share:.0%}" for name, share in weights[:15])
        )
    examples: list[sc.PastExamQuestion] = []
    for e in exams:
        examples += e.questions[: max(2, MAX_EXAMPLES // len(exams))]
    if examples:
        lines.append("Example tasks from past exams (copy their style, not their content):")
        for q in examples[:MAX_EXAMPLES]:
            pts = f", {q.points:g} pts" if q.points else ""
            lines.append(f"- [{q.type}{pts}] {q.prompt.strip()[:400]}")
    return "\n".join(lines)


def summary(m: Material) -> dict[str, Any] | None:
    """What the Materials tab shows for an analysed past exam."""
    a = m.data.get("analysis")
    if not a:
        return None
    return {
        "title": a.get("title", ""),
        "date": a.get("date", ""),
        "duration_min": a.get("duration_min", 0),
        "total_points": a.get("total_points", 0),
        "aids": a.get("aids", ""),
        "format": a.get("format", ""),
        "style": a.get("style", []),
        "topics": a.get("topics", []),
        "questions": len(a.get("questions", [])),
        "ocr": bool(m.data.get("ocr")),
    }
