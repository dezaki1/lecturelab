"""Weak spots: per-topic strength from reviews, practice and exams, and targeted practice.

A topic's strength combines what the student showed recently (last WINDOW_DAYS):

- memory: mean FSRS recall probability of the topic's reviewed cards one week from now. Not
  "right now": just after a review that is ~100% even for a card forgotten five times; a week
  ahead it reflects memory stability, which every lapse lowers. Unseen cards are "untested",
  not weak,
- recall: share of the topic's card reviews not rated Again (needs MIN_REVIEWS reviews),
- answers: points scored / points possible on the topic's questions (practice and exams).

Topics come from the subject hub; without one, each lecture is a topic. "Practice my weak
topics" writes new questions aimed at the forgotten cards and wrong answers of those topics
(main model) and stores them as a ``PracticeSet``.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from fsrs import Scheduler
from sqlmodel import Session, col, select

from backend.ai import schemas as sc
from backend.ai.prompts import load_prompt
from backend.ai.providers import get_provider, is_configured
from backend.ai.types import LLMRequest
from backend.core import jobs
from backend.core.config import get_settings
from backend.core.db import session_scope
from backend.core.events import bus
from backend.core.jobs import JobContext, JobError, handler
from backend.core.models import (
    Attempt,
    Flashcard,
    PracticeSet,
    Question,
    ReviewLog,
    Subject,
)
from backend.pipeline.generate import record_usage
from backend.pipeline.subject_hub import current_hub, lectures_with_notes
from backend.study.dashboard import retrievability
from backend.study.exams import (
    ALL_TYPES,
    SYSTEM,
    _notes_material,
    exam_profile,
    profile_text,
    store_generated,
)
from backend.study.review import now_utc
from backend.study.topics import topics_with_mastery

log = logging.getLogger(__name__)

WEAK_JOB = "weak_practice"
WINDOW_DAYS = 60
MIN_REVIEWS = 3
WEAK_BELOW = 0.6
WEIGHTS = {"memory": 0.4, "recall": 0.3, "answers": 0.3}
MEMORY_HORIZON = timedelta(days=7)
MAX_TOPICS = 4


class WeakError(Exception):
    pass


def _topics(s: Session, subject_id: int, now: datetime) -> list[dict[str, Any]]:
    hub = current_hub(s, subject_id)
    if hub is not None and hub.data.get("topics"):
        return topics_with_mastery(s, subject_id, hub, now)
    # No course overview yet: each lecture is a topic.
    sch = Scheduler(desired_retention=get_settings().study.desired_retention)
    out = []
    for n, lec in lectures_with_notes(s, subject_id):
        cards = s.exec(
            select(Flashcard).where(
                Flashcard.lecture_id == lec.id,
                col(Flashcard.is_current),
                col(Flashcard.suspended).is_(False),
            )
        ).all()
        qids = s.exec(
            select(Question.id).where(Question.lecture_id == lec.id, col(Question.is_current))
        ).all()
        if not cards and not qids:
            continue
        recall = [retrievability(sch, c, now) for c in cards]
        out.append(
            {
                "name": f"L{n} {lec.title}",
                "summary": "",
                "exam_relevant": False,
                "places": [
                    {
                        "lecture_id": lec.id,
                        "number": n,
                        "title": lec.title,
                        "start_s": 0.0,
                        "sections": [],
                    }
                ],
                "card_ids": [c.id for c in cards],
                "question_ids": list(qids),
                "mastery": round(sum(recall) / len(recall), 3) if recall else None,
            }
        )
    return out


def topic_stats(s: Session, subject_id: int, now: datetime | None = None) -> list[dict[str, Any]]:
    now = now or now_utc()
    since = now - timedelta(days=WINDOW_DAYS)
    topics = _topics(s, subject_id, now)
    card_ids = {cid for t in topics for cid in t["card_ids"]}
    question_ids = {qid for t in topics for qid in t["question_ids"]}
    reviews: dict[int, list[int]] = {}
    for cid, rating in s.exec(
        select(ReviewLog.card_id, ReviewLog.rating).where(
            col(ReviewLog.card_id).in_(card_ids), ReviewLog.reviewed_at >= since
        )
    ).all():
        reviews.setdefault(cid, []).append(rating)
    attempts: dict[int, list[Attempt]] = {}
    for a in s.exec(
        select(Attempt)
        .where(col(Attempt.question_id).in_(question_ids), Attempt.created_at >= since)
        .order_by(col(Attempt.id))
    ).all():
        attempts.setdefault(a.question_id, []).append(a)
    cards = {
        c.id: c for c in s.exec(select(Flashcard).where(col(Flashcard.id).in_(card_ids))).all()
    }
    sch = Scheduler(desired_retention=get_settings().study.desired_retention)
    questions = {
        q.id: q for q in s.exec(select(Question).where(col(Question.id).in_(question_ids))).all()
    }

    rows = []
    for t in topics:
        ratings = [r for cid in t["card_ids"] for r in reviews.get(cid, [])]
        again = sum(1 for r in ratings if r == 1)
        tries = [a for qid in t["question_ids"] for a in attempts.get(qid, [])]
        got = sum(a.score for a in tries)
        possible = sum(a.max_points for a in tries)
        parts: dict[str, float] = {}
        seen = [cards[cid] for cid in t["card_ids"] if cid in reviews and cid in cards]
        if seen:
            ahead = now + MEMORY_HORIZON
            parts["memory"] = sum(retrievability(sch, c, ahead) for c in seen) / len(seen)
        if len(ratings) >= MIN_REVIEWS:
            parts["recall"] = 1 - again / len(ratings)
        if possible > 0:
            parts["answers"] = got / possible
        strength = (
            sum(WEIGHTS[k] * v for k, v in parts.items()) / sum(WEIGHTS[k] for k in parts)
            if parts
            else None
        )
        status = "untested" if strength is None else "weak" if strength < WEAK_BELOW else "ok"
        forgotten = sorted(
            (cid for cid in t["card_ids"] if 1 in reviews.get(cid, [])),
            key=lambda cid: -reviews[cid].count(1),
        )
        latest = {qid: attempts[qid][-1] for qid in t["question_ids"] if qid in attempts}
        wrong = [a for a in latest.values() if a.score < a.max_points - 1e-9]
        rows.append(
            {
                **{k: t[k] for k in ("name", "summary", "exam_relevant", "places", "mastery")},
                "strength": round(strength, 3) if strength is not None else None,
                "status": status,
                "parts": {k: round(v, 3) for k, v in parts.items()},
                "reviews": len(ratings),
                "forgotten": again,
                "answers": len(tries),
                "score": round(got, 1),
                "max": round(possible, 1),
                "cards": len(t["card_ids"]),
                "questions": len(t["question_ids"]),
                "forgotten_cards": [
                    {
                        "id": cid,
                        "front": cards[cid].front,
                        "back": cards[cid].back,
                        "times": reviews[cid].count(1),
                    }
                    for cid in forgotten[:5]
                    if cid in cards
                ],
                "wrong_answers": [
                    {
                        "question_id": a.question_id,
                        "prompt": questions[a.question_id].prompt
                        if a.question_id in questions
                        else "",
                        "answer_text": a.answer_text,
                        "score": a.score,
                        "max": a.max_points,
                        "feedback": a.feedback,
                    }
                    for a in sorted(wrong, key=lambda a: a.score / (a.max_points or 1))[:5]
                ],
            }
        )
    order = {"weak": 0, "ok": 1, "untested": 2}
    rows.sort(
        key=lambda r: (
            order[r["status"]],
            r["strength"] if r["strength"] is not None else 1,
            not r["exam_relevant"],
        )
    )
    return rows


def weak_overview(s: Session, limit: int = 8) -> list[dict[str, Any]]:
    """The weakest topics across all subjects (for the dashboard)."""
    out = []
    for sub in s.exec(select(Subject).where(col(Subject.archived).is_(False))).all():
        for r in topic_stats(s, sub.id):  # type: ignore[arg-type]
            if r["status"] == "weak":
                out.append(
                    {
                        "subject_id": sub.id,
                        "subject": sub.name,
                        "color": sub.color,
                        "name": r["name"],
                        "strength": r["strength"],
                        "exam_relevant": r["exam_relevant"],
                    }
                )
    out.sort(key=lambda r: r["strength"])
    return out[:limit]


# Targeted practice ------------------------------------------------------------------------------
def create_practice_set(
    s: Session, subject_id: int, topics: list[str] | None, n: int
) -> PracticeSet:
    if not is_configured():
        raise WeakError("Add an Anthropic API key on the Settings page first")
    stats = topic_stats(s, subject_id)
    if topics:
        known = {r["name"] for r in stats}
        chosen = [t for t in topics if t in known][:MAX_TOPICS]
    else:
        chosen = [r["name"] for r in stats if r["status"] == "weak"][:3]
    if not chosen:
        raise WeakError("No weak topics yet: review cards and answer some questions first")
    ps = PracticeSet(subject_id=subject_id, topics=chosen)
    s.add(ps)
    s.commit()
    s.refresh(ps)
    jobs.enqueue(s, WEAK_JOB, payload={"set_id": ps.id, "subject_id": subject_id, "n": n})
    return ps


def weak_text(rows: list[dict[str, Any]]) -> str:
    parts = []
    for r in rows:
        lines = [f"## Topic: {r['name']}" + (f" ({r['summary']})" if r["summary"] else "")]
        if r["strength"] is not None:
            lines.append(
                f"Strength {round(r['strength'] * 100)}% (cards forgotten {r['forgotten']} of "
                f"{r['reviews']} reviews; answers {r['score']}/{r['max']} points)"
            )
        if r["forgotten_cards"]:
            lines.append("Cards the student keeps forgetting:")
            lines += [f"- {c['front']} -> {c['back']}" for c in r["forgotten_cards"]]
        if r["wrong_answers"]:
            lines.append("Questions answered wrongly (with the student's answer):")
            for w in r["wrong_answers"]:
                answer = (w["answer_text"] or "(no answer / wrong choice)").strip()[:300]
                lines.append(
                    f"- Q: {w['prompt'][:250]}\n  Student: {answer} ({w['score']:g}/{w['max']:g})"
                )
        parts.append("\n".join(lines))
    return "\n\n".join(parts)


@handler(WEAK_JOB)
def build_practice_set(ctx: JobContext) -> None:
    set_id = ctx.payload["set_id"]
    try:
        _build(ctx, set_id)
    except Exception as e:
        with session_scope() as s:
            ps = s.get(PracticeSet, set_id)
            if ps is not None:
                ps.status, ps.error = "failed", str(e)
                s.add(ps)
                s.commit()
        bus.publish("practice_set", {"id": set_id, "status": "failed"})
        raise


def _build(ctx: JobContext, set_id: int) -> None:
    subject_id, n = ctx.payload["subject_id"], int(ctx.payload.get("n") or 6)
    with session_scope() as s:
        ps = s.get(PracticeSet, set_id)
        subject = s.get(Subject, subject_id)
        if ps is None or subject is None:
            return
        rows = [r for r in topic_stats(s, subject_id) if r["name"] in ps.topics]
        lecture_ids = {p["lecture_id"] for r in rows for p in r["places"]}
        material, numbers = _notes_material(s, subject_id, lecture_ids or None)
        existing = [
            q.prompt
            for q in s.exec(
                select(Question).where(Question.subject_id == subject_id, col(Question.is_current))
            ).all()
        ][-40:]
        style = profile_text(exam_profile(s, subject_id))
        name = subject.name
    if not material:
        raise JobError("These topics have no study notes yet")
    ctx.progress(0.2, "Writing practice questions for your weak topics")
    result = get_provider().complete(
        LLMRequest(
            task="weak_practice",
            system=SYSTEM,
            context=f"# Lecture notes of {name}\n\n{material}",
            instruction=load_prompt("weak_practice").render(
                subject=name,
                n=max(2, min(n, 15)),
                weak=weak_text(rows),
                types=", ".join(ALL_TYPES),
                existing="; ".join(e[:80] for e in existing) or "(none)",
                style=style,
            ),
            output=sc.SimQuestionsOut,
        )
    )
    record_usage(None, subject_id, "weak_practice", result)
    saved = store_generated(result.parsed.questions, numbers, result.model, list(ALL_TYPES))
    if not saved:
        raise JobError("No usable questions came back; try again")
    with session_scope() as s:
        ps = s.get(PracticeSet, set_id)
        assert ps is not None
        ps.question_ids = [q.id for q in saved]  # type: ignore[misc]
        ps.status = "ready"
        s.add(ps)
        s.commit()
    bus.publish("practice_set", {"id": set_id, "status": "ready"})
