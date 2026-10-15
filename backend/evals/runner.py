"""Evals: measure search and grounding on the student's own lectures; rerun after prompt changes.

Retrieval (no LLM needed). ``EvalCase`` rows are search questions with the lecture moment that
answers them. Candidates are made from sections of the generated notes (the section's [mm:ss]
refs are the gold range; the cheap model rephrases the section into a question a student
would type, so the eval tests meaning, not copied words) and the student approves, fixes or
rejects them. A run searches every approved question like the search page does and reports
hit@1/5/10 (a top-k hit in the right lecture overlapping the gold range, ± TOLERANCE_S),
lecture hit@5 and MRR.

Grounding (cheap model). A fixed sample of current flashcards and exam questions (same seed,
so runs compare like with like) is checked against the transcript passage each one cites:
supported / partly / unsupported, plus refs that point at no transcript at all.

Each run stores its metrics, per-item details and the prompt versions and search settings
in force, so two runs show what a prompt change did.
"""

from __future__ import annotations

import logging
import random
from typing import Any

from sqlmodel import Session, col, select

from backend.ai import schemas as sc
from backend.ai.prompts import PROMPTS_DIR, load_prompt
from backend.ai.providers import active_provider_name, get_provider, is_configured
from backend.ai.types import LLMError, LLMRequest
from backend.core import jobs
from backend.core.config import get_settings
from backend.core.db import session_scope
from backend.core.events import bus
from backend.core.jobs import JobContext, JobError, handler
from backend.core.models import (
    EvalCase,
    EvalRun,
    Flashcard,
    Lecture,
    Note,
    Question,
    TranscriptSegment,
)
from backend.core.util import format_hms
from backend.pipeline.generate import parse_ts, record_usage
from backend.search.retrieve import Hit, search

log = logging.getLogger(__name__)

EVAL_JOB = "eval"
TOLERANCE_S = 30.0
KS = (1, 5, 10)
CHUNK_GUESS_S = 90.0  # a hit with a start but no end covers roughly this much
GROUNDING_SAMPLE = 40
GROUNDING_BATCH = 10
CONTEXT_PAD_S = 15.0
SEED = 42


class EvalError(Exception):
    pass


def prompt_versions() -> dict[str, int]:
    return {p.stem: load_prompt(p.stem).version for p in sorted(PROMPTS_DIR.glob("*.md"))}


# Candidates -------------------------------------------------------------------------------


def _sections(s: Session) -> list[dict[str, Any]]:
    """Note sections with refs: lecture, heading, text, gold range."""
    out = []
    notes = s.exec(select(Note).where(Note.kind == "notes", col(Note.is_current))).all()
    for n in notes:
        if n.lecture_id is None:
            continue
        for sec in (n.data or {}).get("sections", []):
            times = [
                t
                for r in sec.get("refs", [])
                for t in (parse_ts(str(r.get("start", ""))), parse_ts(str(r.get("end", ""))))
                if t is not None
            ]
            if not times or not sec.get("heading"):
                continue
            out.append(
                {
                    "subject_id": n.subject_id,
                    "lecture_id": n.lecture_id,
                    "heading": str(sec["heading"]),
                    "text": str(sec.get("markdown", ""))[:700],
                    "start": min(times),
                    "end": max(max(times), min(times) + 10),
                }
            )
    return out


def make_candidates(s: Session, n: int = 20, subject_id: int | None = None) -> int:
    """New candidate cases from note sections not used yet; returns how many were made."""
    used = {(c.lecture_id, c.section) for c in s.exec(select(EvalCase)).all()}
    pool = [
        x
        for x in _sections(s)
        if (x["lecture_id"], x["heading"]) not in used
        and (subject_id is None or x["subject_id"] == subject_id)
    ]
    if not pool:
        return 0
    picked = random.Random().sample(pool, min(n, len(pool)))
    questions = [x["heading"] for x in picked]
    if is_configured():
        try:
            questions = _rephrase(picked)
        except LLMError as e:
            log.warning("Eval questions from headings only: %s", e)
    for x, q in zip(picked, questions, strict=True):
        s.add(
            EvalCase(
                subject_id=x["subject_id"],
                lecture_id=x["lecture_id"],
                question=q.strip() or x["heading"],
                gold_start_s=x["start"],
                gold_end_s=x["end"],
                section=x["heading"],
            )
        )
    s.commit()
    bus.publish("eval", {"cases": True})
    return len(picked)


def _rephrase(picked: list[dict[str, Any]]) -> list[str]:
    listing = "\n\n".join(
        f"[{i}] {x['heading']}\n{x['text']}" for i, x in enumerate(picked, start=1)
    )
    result = get_provider().complete(
        LLMRequest(
            task="eval_questions",
            system="You write realistic search queries for evaluating a study app's search.",
            context=listing,
            instruction=load_prompt("eval_questions").render(n=len(picked)),
            output=sc.EvalQuestionsOut,
            tier="cheap",
            max_tokens=4000,
        )
    )
    record_usage(None, picked[0]["subject_id"], "eval_questions", result)
    by_index = {q.item: q.question for q in result.parsed.questions}
    return [by_index.get(i, x["heading"]) for i, x in enumerate(picked, start=1)]


# Retrieval --------------------------------------------------------------------------------


def _overlaps(hit: Hit, case: EvalCase) -> bool:
    if hit.lecture_id != case.lecture_id or hit.start_s is None:
        return False
    end = hit.end_s if hit.end_s is not None else hit.start_s + CHUNK_GUESS_S
    return hit.start_s <= case.gold_end_s + TOLERANCE_S and end >= case.gold_start_s - TOLERANCE_S


def run_retrieval(s: Session) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    cases = s.exec(
        select(EvalCase).where(EvalCase.status == "approved").order_by(col(EvalCase.id))
    ).all()
    if not cases:
        raise EvalError("No approved eval questions yet: make candidates and approve some")
    details = []
    for c in cases:
        hits = search(s, c.question, subject_id=c.subject_id, limit=max(KS))
        rank = next((i for i, h in enumerate(hits, start=1) if _overlaps(h, c)), None)
        lecture_rank = next(
            (i for i, h in enumerate(hits, start=1) if h.lecture_id == c.lecture_id), None
        )
        details.append(
            {
                "case_id": c.id,
                "question": c.question,
                "lecture_id": c.lecture_id,
                "gold": f"{format_hms(c.gold_start_s)}-{format_hms(c.gold_end_s)}",
                "rank": rank,
                "lecture_rank": lecture_rank,
                "top": [
                    {
                        "lecture_id": h.lecture_id,
                        "start_s": h.start_s,
                        "source": h.source,
                        "heading": h.heading[:80],
                    }
                    for h in hits[:3]
                ],
            }
        )
    n = len(details)
    metrics: dict[str, Any] = {"n": n}
    for k in KS:
        metrics[f"hit@{k}"] = round(sum(1 for d in details if d["rank"] and d["rank"] <= k) / n, 3)
    metrics["lecture_hit@5"] = round(
        sum(1 for d in details if d["lecture_rank"] and d["lecture_rank"] <= 5) / n, 3
    )
    metrics["mrr"] = round(sum(1 / d["rank"] for d in details if d["rank"]) / n, 3)
    return metrics, details


# Grounding --------------------------------------------------------------------------------


def _passage(s: Session, lecture_id: int, start: float, end: float) -> str:
    segs = s.exec(
        select(TranscriptSegment)
        .where(
            TranscriptSegment.lecture_id == lecture_id,
            TranscriptSegment.end >= start - CONTEXT_PAD_S,
            TranscriptSegment.start <= end + CONTEXT_PAD_S,
        )
        .order_by(col(TranscriptSegment.idx))
    ).all()
    return "\n".join(f"[{format_hms(x.start)}] {x.text}" for x in segs)


def _grounding_items(s: Session) -> list[dict[str, Any]]:
    cards = s.exec(
        select(Flashcard).where(col(Flashcard.is_current)).order_by(col(Flashcard.id))
    ).all()
    questions = s.exec(
        select(Question).where(col(Question.is_current)).order_by(col(Question.id))
    ).all()
    items = [
        {"kind": "card", "id": c.id, "text": f"Q: {c.front}\nA: {c.back}", "refs": c.source_refs}
        for c in cards
    ] + [
        {
            "kind": "question",
            "id": q.id,
            "text": f"Q: {q.prompt}\nA: {q.answer}",
            "refs": q.source_refs,
        }
        for q in questions
    ]
    if len(items) > GROUNDING_SAMPLE:
        items = random.Random(SEED).sample(items, GROUNDING_SAMPLE)
    return items


def run_grounding(s: Session, ctx: JobContext | None = None) -> tuple[dict, list, float]:
    if not is_configured():
        raise EvalError("The grounding eval needs an AI provider (Settings → AI provider)")
    items = _grounding_items(s)
    if not items:
        raise EvalError("No flashcards or exam questions to check yet")
    lectures = {x.id: x for x in s.exec(select(Lecture)).all()}
    details: list[dict[str, Any]] = []
    to_judge: list[dict[str, Any]] = []
    for it in items:
        refs = [r for r in it["refs"] if r.get("lecture_id") in lectures]
        passage = "\n".join(
            _passage(s, r["lecture_id"], float(r.get("start_s", 0)), float(r.get("end_s", 0)))
            for r in refs[:3]
        )
        row = {"kind": it["kind"], "id": it["id"], "text": it["text"][:300]}
        if not passage.strip():
            details.append({**row, "verdict": "bad_ref", "reason": "cites no transcript passage"})
        else:
            to_judge.append({**row, "passage": passage[:4000]})
    cost = 0.0
    for b in range(0, len(to_judge), GROUNDING_BATCH):
        batch = to_judge[b : b + GROUNDING_BATCH]
        if ctx:
            ctx.progress(b / max(len(to_judge), 1), "Checking items against the transcript")
        listing = "\n\n".join(
            f"## Item {i}\n{x['text']}\n\nCited passage:\n{x['passage']}"
            for i, x in enumerate(batch, start=1)
        )
        result = get_provider().complete(
            LLMRequest(
                task="eval_judge",
                system="You check whether study material is supported by its cited source.",
                context=listing,
                instruction=load_prompt("eval_judge").render(n=len(batch)),
                output=sc.EvalJudgeOut,
                tier="cheap",
                max_tokens=4000,
            )
        )
        cost += record_usage(None, None, "eval_judge", result)  # type: ignore[arg-type]
        verdicts = {v.item: v for v in result.parsed.verdicts}
        for i, x in enumerate(batch, start=1):
            v = verdicts.get(i)
            details.append(
                {
                    "kind": x["kind"],
                    "id": x["id"],
                    "text": x["text"],
                    "verdict": v.verdict if v else "unjudged",
                    "reason": v.reason if v else "",
                }
            )
    n = len(details)
    metrics: dict[str, Any] = {"n": n}
    for verdict in ("supported", "partly", "unsupported", "bad_ref"):
        metrics[verdict] = round(sum(1 for d in details if d["verdict"] == verdict) / n, 3)
    for kind in ("card", "question"):
        mine = [d for d in details if d["kind"] == kind]
        if mine:
            metrics[f"{kind}_supported"] = round(
                sum(1 for d in mine if d["verdict"] == "supported") / len(mine), 3
            )
    return metrics, details, cost


# Job --------------------------------------------------------------------------------------


def queue_run(s: Session, kind: str) -> EvalRun:
    if kind not in ("retrieval", "grounding"):
        raise EvalError(f"Unknown eval {kind!r}")
    run = EvalRun(kind=kind, config=_config())
    s.add(run)
    s.commit()
    s.refresh(run)
    jobs.enqueue(s, EVAL_JOB, payload={"run_id": run.id})
    bus.publish("eval", {"run_id": run.id})
    return run


def _config() -> dict[str, Any]:
    st = get_settings()
    return {
        "prompts": prompt_versions(),
        "provider": active_provider_name(),
        "search": st.search.model_dump(mode="json"),
    }


@handler(EVAL_JOB)
def eval_job(ctx: JobContext) -> None:
    run_id = ctx.payload["run_id"]
    with session_scope() as s:
        run = s.get(EvalRun, run_id)
        if run is None:
            return
        try:
            if run.kind == "retrieval":
                metrics, details = run_retrieval(s)
                cost = 0.0
            else:
                metrics, details, cost = run_grounding(s, ctx)
        except (EvalError, LLMError) as e:
            run.status, run.error = "failed", str(e)
            s.add(run)
            s.commit()
            bus.publish("eval", {"run_id": run_id})
            raise JobError(str(e)) from e
        run.metrics, run.details, run.cost_usd, run.status = metrics, details, cost, "done"
        s.add(run)
        s.commit()
    bus.publish("eval", {"run_id": run_id})
