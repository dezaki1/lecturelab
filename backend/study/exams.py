"""Past exams and the exam simulator.

Past exam PDFs (Material kind "past_exam") are analysed once by the main model (questions,
types, points, topics, style); their combined "exam profile" steers the question generator
and the simulator. A simulated exam is built from the subject's question bank (weighted towards
weak topics and unanswered questions), topped up with new questions in the past exams' style,
taken under a time limit and graded: choice questions automatically, open answers by the main
model against the rubric (partial credit, feedback). Every graded answer is also stored as an
``Attempt``, so exams count towards practice stats and topic mastery.
"""

from __future__ import annotations

import logging
import random
from collections import Counter
from datetime import timedelta
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
from backend.core.models import (
    Attempt,
    Exam,
    ExamAnswer,
    Lecture,
    Material,
    Note,
    Question,
    Subject,
    TranscriptSegment,
    utcnow,
)
from backend.core.util import format_hms
from backend.pipeline.generate import record_usage, to_refs
from backend.study.practice import CHOICE_TYPES, grade_choice

log = logging.getLogger(__name__)

PAST_EXAM_JOB = "past_exam"
EXAM_BUILD_JOB = "exam_build"
EXAM_GRADE_JOB = "exam_grade"
ALL_TYPES = ("mcq", "multi", "tf", "short", "long", "calculation", "code")
GRACE_S = 60  # submissions this long after the deadline still count as on time
NOTES_CHARS_PER_LECTURE = 6000
NOTES_CHARS_TOTAL = 60_000
SYSTEM = (
    "You are an experienced university examiner for a final-year AI course. You write and "
    "grade exam questions strictly based on the course material provided."
)


class ExamError(Exception):
    pass


# Past exams -------------------------------------------------------------------------------------
def queue_past_exam_analysis(s: Session, material_id: int) -> bool:
    if not is_configured():
        return False
    jobs.enqueue(s, PAST_EXAM_JOB, payload={"material_id": material_id})
    return True


@handler(PAST_EXAM_JOB)
def analyse_past_exam(ctx: JobContext) -> None:
    with session_scope() as s:
        m = s.get(Material, ctx.payload["material_id"])
        if m is None:
            return
        subject = s.get(Subject, m.subject_id)
        text, title, subject_id = m.extracted_text.strip(), m.title, m.subject_id
        name = subject.name if subject else ""
    if not text:
        raise JobError("The PDF has no readable text")
    ctx.progress(0.2, "Analysing the past exam")
    result = get_provider().complete(
        LLMRequest(
            task="past_exam",
            system=SYSTEM,
            context=f"# Past exam: {title}\n\n{text[:60_000]}",
            instruction=load_prompt("past_exam").render(subject=name),
            output=sc.PastExamOut,
        )
    )
    record_usage(None, subject_id, "past_exam", result)
    with session_scope() as s:
        m = s.get(Material, ctx.payload["material_id"])
        if m is None:
            return
        m.analysis = {**result.parsed.model_dump(), "model": result.model}
        s.add(m)
        s.commit()
    bus.publish("exams", {"subject_id": subject_id})


def past_exams(s: Session, subject_id: int) -> list[Material]:
    return list(
        s.exec(
            select(Material)
            .where(Material.subject_id == subject_id, Material.kind == "past_exam")
            .order_by(col(Material.id))
        ).all()
    )


def exam_profile(s: Session, subject_id: int) -> dict[str, Any] | None:
    """What the subject's past exams look like, combined."""
    analyses = [m.analysis for m in past_exams(s, subject_id) if m.analysis.get("questions")]
    if not analyses:
        return None
    types: Counter[str] = Counter()
    topics: Counter[str] = Counter()
    examples: list[str] = []
    points: list[float] = []
    for a in analyses:
        for q in a["questions"]:
            weight = float(q.get("points") or 0) or 1.0
            types[q.get("type", "other")] += weight
            if q.get("topic"):
                topics[q["topic"].strip()] += weight
            if q.get("points"):
                points.append(float(q["points"]))
        seen = {e.split(":", 1)[0] for e in examples}
        for q in a["questions"]:
            if len(examples) < 6 and q.get("type") not in seen and q.get("text"):
                examples.append(f"{q.get('type')}: {q['text'][:300]}")
                seen.add(q.get("type"))
    total_w = sum(types.values()) or 1
    durations = [a["duration_min"] for a in analyses if a.get("duration_min")]
    totals = [a["total_points"] for a in analyses if a.get("total_points")]
    return {
        "exams": len(analyses),
        "types": {t: round(w / total_w, 2) for t, w in types.most_common()},
        "topics": [{"topic": t, "share": round(w / total_w, 2)} for t, w in topics.most_common(15)],
        "duration_min": round(sum(durations) / len(durations)) if durations else None,
        "total_points": round(sum(totals) / len(totals), 1) if totals else None,
        "avg_points": round(sum(points) / len(points), 1) if points else None,
        "style_notes": [a["style_notes"] for a in analyses if a.get("style_notes")],
        "examples": examples,
    }


def profile_text(p: dict[str, Any] | None) -> str:
    """For prompts: how the real exams of this course look."""
    if not p:
        return ""
    mix = ", ".join(f"{t} {round(share * 100)}%" for t, share in p["types"].items())
    lines = [
        f"\nPast exams of this course ({p['exams']} analysed): question types by points: {mix}."
    ]
    if p["duration_min"] and p["total_points"]:
        lines.append(f"Typical exam: {p['duration_min']} min, {p['total_points']} points.")
    if p["topics"]:
        lines.append("Topic weighting: " + ", ".join(t["topic"] for t in p["topics"][:10]) + ".")
    for note in p["style_notes"][:3]:
        lines.append(f"Style: {note}")
    if p["examples"]:
        lines.append(
            "Example questions from past exams:\n" + "\n".join(f"- {e}" for e in p["examples"])
        )
    lines.append("Match their format, style and difficulty.")
    return "\n".join(lines)


# Building ---------------------------------------------------------------------------------------
def normalise_config(cfg: dict[str, Any], profile: dict[str, Any] | None) -> dict[str, Any]:
    types = [t for t in cfg.get("types") or ALL_TYPES if t in ALL_TYPES] or list(ALL_TYPES)
    duration = int(cfg.get("duration_min") or (profile or {}).get("duration_min") or 60)
    duration = max(5, min(duration, 300))
    # Points: as many as a real exam has per minute, else about one point per minute.
    rate = 1.0
    if profile and profile.get("duration_min") and profile.get("total_points"):
        rate = profile["total_points"] / profile["duration_min"]
    target = float(cfg.get("target_points") or round(duration * rate))
    return {
        "topics": [t for t in cfg.get("topics") or [] if isinstance(t, str)],
        "types": types,
        "difficulty": cfg.get("difficulty")
        if cfg.get("difficulty") in ("easy", "medium", "hard")
        else "mixed",
        "duration_min": duration,
        "target_points": max(1.0, target),
        "allow_new": bool(cfg.get("allow_new", True)),
    }


def create_exam(s: Session, subject_id: int, cfg: dict[str, Any]) -> Exam:
    subject = s.get(Subject, subject_id)
    if subject is None:
        raise ExamError("Subject not found")
    config = normalise_config(cfg, exam_profile(s, subject_id))
    n = len(s.exec(select(Exam.id).where(Exam.subject_id == subject_id)).all()) + 1
    exam = Exam(
        subject_id=subject_id,
        title=cfg.get("title") or f"Practice exam {n}",
        config=config,
        time_limit_s=config["duration_min"] * 60,
    )
    s.add(exam)
    s.commit()
    s.refresh(exam)
    jobs.enqueue(s, EXAM_BUILD_JOB, payload={"exam_id": exam.id, "subject_id": subject_id})
    return exam


def _topic_index(s: Session, subject_id: int) -> tuple[dict[str, list[int]], dict[int, float]]:
    """topic name -> question ids, and question id -> mastery of its topic (for weighting)."""
    from backend.pipeline.subject_hub import current_hub
    from backend.study.topics import topics_with_mastery

    hub = current_hub(s, subject_id)
    if hub is None:
        return {}, {}
    by_topic: dict[str, list[int]] = {}
    mastery: dict[int, float] = {}
    for t in topics_with_mastery(s, subject_id, hub):
        by_topic[t["name"]] = t["question_ids"]
        for qid in t["question_ids"]:
            mastery[qid] = min(
                mastery.get(qid, 1.0), t["mastery"] if t["mastery"] is not None else 0.0
            )
    return by_topic, mastery


def pick_questions(
    s: Session, subject_id: int, cfg: dict[str, Any], rng: random.Random
) -> list[Question]:
    q = select(Question).where(
        Question.subject_id == subject_id,
        Question.kind == "exam",
        col(Question.is_current),
        col(Question.type).in_(cfg["types"]),
    )
    if cfg["difficulty"] != "mixed":
        q = q.where(Question.difficulty == cfg["difficulty"])
    candidates = list(s.exec(q).all())
    by_topic, mastery = _topic_index(s, subject_id)
    if cfg["topics"] and by_topic:
        wanted = {qid for t in cfg["topics"] for qid in by_topic.get(t, [])}
        candidates = [c for c in candidates if c.id in wanted]
    attempted = set(s.exec(select(Attempt.question_id)).all())

    def weight(c: Question) -> float:
        w = 1.0 + (1.0 - mastery.get(c.id, 0.5))  # type: ignore[arg-type]
        w += 0.5 if c.emphasized else 0.0
        w += 0.5 if c.id not in attempted else 0.0
        return w

    picked: list[Question] = []
    total = 0.0
    pool = candidates[:]
    while pool and total < cfg["target_points"]:
        c = rng.choices(pool, weights=[weight(x) for x in pool], k=1)[0]
        pool.remove(c)
        if total + c.points > cfg["target_points"] * 1.15 and picked:
            continue  # would overshoot clearly; try a smaller one
        picked.append(c)
        total += c.points
    return picked


def _notes_material(
    s: Session, subject_id: int, lecture_ids: set[int] | None
) -> tuple[str, dict[int, Lecture]]:
    from backend.pipeline.subject_hub import lectures_with_notes

    parts, numbers, total = [], {}, 0
    for n, lec in lectures_with_notes(s, subject_id):
        numbers[n] = lec
        if lecture_ids and lec.id not in lecture_ids:
            continue
        note = s.exec(
            select(Note).where(
                Note.lecture_id == lec.id, Note.kind == "notes", col(Note.is_current)
            )
        ).first()
        if note is None:
            continue
        body = note.content_md[:NOTES_CHARS_PER_LECTURE]
        total += len(body)
        if total > NOTES_CHARS_TOTAL:
            break
        parts.append(f'## L{n} "{lec.title}"\n{body}')
    return "\n\n".join(parts), numbers


def generate_questions(
    s_subject: Subject, cfg: dict[str, Any], n: int, points: float, existing: list[str]
) -> list[Question]:
    """New questions in the past exams' style; saved to the bank (generation 0)."""
    with session_scope() as s:
        by_topic, _ = _topic_index(s, s_subject.id)  # type: ignore[arg-type]
        lecture_ids: set[int] | None = None
        if cfg["topics"] and by_topic:
            qids = {qid for t in cfg["topics"] for qid in by_topic.get(t, [])}
            lecture_ids = {
                lid
                for lid in s.exec(
                    select(Question.lecture_id).where(col(Question.id).in_(qids))
                ).all()
                if lid
            } or None
        material, numbers = _notes_material(s, s_subject.id, lecture_ids)  # type: ignore[arg-type]
        style = profile_text(exam_profile(s, s_subject.id))  # type: ignore[arg-type]
    if not material:
        return []
    result = get_provider().complete(
        LLMRequest(
            task="exam_questions",
            system=SYSTEM,
            context=f"# Lecture notes of {s_subject.name}\n\n{material}",
            instruction=load_prompt("exam_questions").render(
                n=n,
                points=round(points),
                subject=s_subject.name,
                topics=", ".join(cfg["topics"]) or "all topics of the course",
                types=", ".join(cfg["types"]),
                difficulty=cfg["difficulty"],
                style=style,
                existing="; ".join(e[:80] for e in existing[:40]) or "(none)",
            ),
            output=sc.SimQuestionsOut,
        )
    )
    record_usage(None, s_subject.id, "exam_questions", result)  # type: ignore[arg-type]
    return store_generated(result.parsed.questions, numbers, result.model, cfg["types"])


def store_generated(
    questions: list[sc.SimQuestion], numbers: dict[int, Lecture], model: str, types: list[str]
) -> list[Question]:
    """Save generated questions to the bank (generation 0: kept across lecture regenerations)."""
    out: list[Question] = []
    with session_scope() as s:
        for q in questions:
            lec = numbers.get(q.lecture)
            if lec is None or q.type not in types:
                continue
            refs = to_refs(lec.id, q.refs, lec.duration_s or 10**6)  # type: ignore[arg-type]
            row = Question(
                subject_id=lec.subject_id,
                lecture_id=lec.id,
                kind="exam",
                type=q.type,
                title=q.topic,
                bloom=q.bloom,
                difficulty=q.difficulty,
                points=q.points,
                prompt=q.prompt,
                options=[o.model_dump() for o in q.options],
                answer=q.answer,
                explanation=q.explanation,
                rubric=[r.model_dump() for r in q.rubric],
                emphasized=q.emphasized,
                source_refs=refs,
                generation=0,
                model=model,
            )
            s.add(row)
            out.append(row)
        s.commit()
        for row in out:
            s.refresh(row)
            s.expunge(row)
    return out


@handler(EXAM_BUILD_JOB)
def build_exam(ctx: JobContext) -> None:
    exam_id = ctx.payload["exam_id"]
    try:
        _build(ctx, exam_id)
    except Exception as e:
        _set(exam_id, status="failed", error=str(e))
        raise


def _build(ctx: JobContext, exam_id: int) -> None:
    rng = random.Random(exam_id)
    with session_scope() as s:
        exam = s.get(Exam, exam_id)
        if exam is None:
            return
        cfg = exam.config
        subject = s.get(Subject, exam.subject_id)
        assert subject is not None
        s.expunge(subject)
        picked = pick_questions(s, exam.subject_id, cfg, rng)
        for q in picked:
            s.expunge(q)
        existing = [q.prompt for q in picked]
    total = sum(q.points for q in picked)
    remaining = cfg["target_points"] - total
    if remaining >= max(2.0, 0.15 * cfg["target_points"]) and cfg["allow_new"] and is_configured():
        ctx.progress(0.3, "Writing new exam questions")
        with session_scope() as s:
            avg = (exam_profile(s, subject.id) or {}).get("avg_points") or 4.0  # type: ignore[arg-type]
        n = max(1, min(15, round(remaining / avg)))
        picked += generate_questions(subject, cfg, n, remaining, existing)
    if not picked:
        raise JobError(
            "No exam questions match these settings yet. Generate study material for some "
            "lectures, or allow new questions."
        )
    # Choice questions first, then open ones; each group in lecture order.
    picked.sort(key=lambda q: (q.type not in CHOICE_TYPES, q.lecture_id or 0, q.id or 0))
    with session_scope() as s:
        for i, q in enumerate(picked):
            s.add(ExamAnswer(exam_id=exam_id, question_id=q.id, position=i, max_points=q.points))  # type: ignore[arg-type]
        exam = s.get(Exam, exam_id)
        assert exam is not None
        exam.total_points = round(sum(q.points for q in picked), 1)
        exam.status = "ready"
        s.add(exam)
        s.commit()
    bus.publish("exam", {"id": exam_id, "status": "ready"})


def _set(exam_id: int, **fields: Any) -> None:
    with session_scope() as s:
        exam = s.get(Exam, exam_id)
        if exam is None:
            return
        for k, v in fields.items():
            setattr(exam, k, v)
        s.add(exam)
        s.commit()
    bus.publish("exam", {"id": exam_id, "status": fields.get("status", "")})


# Taking -----------------------------------------------------------------------------------------
def deadline(exam: Exam):  # type: ignore[no-untyped-def]
    return exam.started_at + timedelta(seconds=exam.time_limit_s) if exam.started_at else None


def start(s: Session, exam: Exam) -> Exam:
    if exam.status != "ready":
        raise ExamError("This exam can't be started now")
    exam.status = "in_progress"
    exam.started_at = utcnow()
    s.add(exam)
    s.commit()
    s.refresh(exam)
    return exam


def save_answer(
    s: Session, exam: Exam, answer_id: int, selected: list[int] | None, text: str | None
) -> ExamAnswer:
    if exam.status != "in_progress":
        raise ExamError("The exam is not running")
    a = s.get(ExamAnswer, answer_id)
    if a is None or a.exam_id != exam.id:
        raise ExamError("Answer not found")
    q = s.get(Question, a.question_id)
    assert q is not None
    if selected is not None:
        if any(not 0 <= i < len(q.options) for i in selected):
            raise ExamError("Unknown option")
        a.selected = sorted(set(selected))
    if text is not None:
        a.answer_text = text[:20_000]
    a.updated_at = utcnow()
    s.add(a)
    s.commit()
    s.refresh(a)
    return a


def submit(s: Session, exam: Exam) -> Exam:
    if exam.status != "in_progress":
        raise ExamError("The exam is not running")
    now = utcnow()
    end = deadline(exam)
    exam.status = "grading"
    exam.submitted_at = now
    exam.config = {**exam.config, "late": bool(end and now > end + timedelta(seconds=GRACE_S))}
    s.add(exam)
    s.commit()
    jobs.enqueue(s, EXAM_GRADE_JOB, payload={"exam_id": exam.id, "subject_id": exam.subject_id})
    s.refresh(exam)
    return exam


# Grading ----------------------------------------------------------------------------------------
def _excerpt(s: Session, refs: list[dict[str, Any]]) -> str:
    out = []
    for r in refs[:2]:
        if r.get("lecture_id") is None or r.get("start_s") is None:
            continue
        segs = s.exec(
            select(TranscriptSegment)
            .where(
                TranscriptSegment.lecture_id == r["lecture_id"],
                TranscriptSegment.start >= r["start_s"] - 20,
                TranscriptSegment.start <= (r.get("end_s") or r["start_s"]) + 20,
            )
            .order_by(col(TranscriptSegment.idx))
        ).all()
        text = " ".join(f"[{format_hms(x.start)}] {x.text}" for x in segs)
        if text:
            out.append(text[:900])
    return "\n".join(out) or "(no lecture passage)"


@handler(EXAM_GRADE_JOB)
def grade_exam(ctx: JobContext) -> None:
    exam_id = ctx.payload["exam_id"]
    try:
        _grade(ctx, exam_id)
    except Exception as e:
        _set(exam_id, status="grading_failed", error=str(e))
        raise


def _grade(ctx: JobContext, exam_id: int) -> None:
    with session_scope() as s:
        exam = s.get(Exam, exam_id)
        if exam is None:
            return
        subject = s.get(Subject, exam.subject_id)
        answers = s.exec(
            select(ExamAnswer)
            .where(ExamAnswer.exam_id == exam_id)
            .order_by(col(ExamAnswer.position))
        ).all()
        open_blocks: list[str] = []
        open_ids: dict[int, Question] = {}
        for a in answers:
            q = s.get(Question, a.question_id)
            assert q is not None
            if q.type in CHOICE_TYPES and q.options:
                correct, score = grade_choice(q, a.selected)
                a.score, a.graded_by = score, "auto"
                a.feedback = "" if correct else (q.explanation or "")
            elif not a.answer_text.strip():
                a.score, a.graded_by, a.feedback = 0.0, "auto", "No answer given."
            else:
                open_ids[a.id] = q  # type: ignore[index]
                rubric = "\n".join(
                    f"    {i}) {r.get('criterion', '')} ({r.get('points', 0)} pts)"
                    for i, r in enumerate(q.rubric)
                )
                open_blocks.append(
                    f"- answer_id={a.id} (max {q.points} points)\n  Question: {q.prompt}\n"
                    f"  Rubric:\n{rubric or '    (none: grade the whole answer)'}\n"
                    f"  Model answer: {q.answer}\n  Lecture passage: {_excerpt(s, q.source_refs)}\n"
                    f"  Student answer: {a.answer_text.strip()}"
                )
            s.add(a)
        s.commit()
        name = subject.name if subject else ""
    summary, revise = "", []
    if open_blocks and is_configured():
        ctx.progress(0.3, "Grading open answers")
        result = get_provider().complete(
            LLMRequest(
                task="exam_grade",
                system=SYSTEM,
                context=f"# Practice exam of {name}",
                instruction=load_prompt("exam_grade").render(
                    subject=name, answers="\n\n".join(open_blocks)
                ),
                output=sc.ExamGradeOut,
            )
        )
        record_usage(None, ctx.payload["subject_id"], "exam_grade", result)
        out: sc.ExamGradeOut = result.parsed
        summary, revise = out.summary.strip(), [r.strip() for r in out.revise if r.strip()]
        with session_scope() as s:
            for g in out.answers:
                q = open_ids.get(g.answer_id)
                a = s.get(ExamAnswer, g.answer_id)
                if q is None or a is None:
                    continue
                items = []
                for r in g.rubric:
                    if 0 <= r.item < len(q.rubric):
                        cap = float(q.rubric[r.item].get("points", 0))
                        items.append(
                            {
                                "item": r.item,
                                "points": max(0.0, min(r.points, cap)),
                                "comment": r.comment,
                            }
                        )
                points = sum(i["points"] for i in items) if items else g.points
                a.score = round(max(0.0, min(points, q.points)), 2)
                a.rubric_scores, a.feedback, a.graded_by = items, g.feedback.strip(), "ai"
                s.add(a)
            s.commit()
    with session_scope() as s:
        answers = s.exec(select(ExamAnswer).where(ExamAnswer.exam_id == exam_id)).all()
        for a in answers:
            if a.score is None:  # no grader available (or it skipped one)
                a.score, a.graded_by = 0.0, "none"
                a.feedback = (
                    a.feedback or "Not graded automatically: compare with the model answer."
                )
                s.add(a)
            s.add(
                Attempt(
                    question_id=a.question_id,
                    selected=a.selected,
                    answer_text=a.answer_text,
                    auto_graded=a.graded_by == "auto",
                    correct=a.score >= a.max_points - 1e-9,
                    score=a.score,
                    max_points=a.max_points,
                    exam_id=exam_id,
                    feedback=a.feedback,
                )
            )
        exam = s.get(Exam, exam_id)
        assert exam is not None
        exam.score = round(sum(a.score or 0 for a in answers), 1)
        exam.summary_md, exam.revise = summary, revise
        exam.status, exam.graded_at, exam.error = "graded", utcnow(), ""
        s.add(exam)
        s.commit()
        title, score, total = exam.title, exam.score, exam.total_points
    bus.publish("exam", {"id": exam_id, "status": "graded"})
    bus.publish(
        "notify",
        {
            "title": "Exam graded",
            "message": f"{title}: {score:g} / {total:g} points",
            "level": "success",
        },
    )
