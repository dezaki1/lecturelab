"""Exam simulator: build a timed exam, sit it, and have the AI grade it (SPEC §7).

An exam is built either by the main model from the per-lecture study notes of the chosen topics
(job ``exam_build``; new questions stored as ``Question`` kind "sim", so practice and the
lecture pages don't show them) or from the existing question bank (instant, works offline).
Each ``ExamItem`` keeps a snapshot of its question, so the history survives later edits.

Sitting: ``start`` fixes the deadline; answers are autosaved until it (plus a short grace for
the last save in flight); a late read or save submits automatically. ``submit`` grades choice
questions at once and queues ``exam_grade`` for the open answers, which the main model grades
against the rubric with partial credit and feedback, citing the lecture passage. Each graded
item is also stored as an ``Attempt``, so practice stats and weak spots count it.
"""

from __future__ import annotations

import difflib
import logging
import random
from datetime import timedelta
from typing import Any

from sqlmodel import Session, col, delete, select

from backend.ai import schemas as sc
from backend.ai.prompts import load_prompt
from backend.ai.providers import get_provider
from backend.ai.types import LLMRequest, LLMResult
from backend.core import jobs
from backend.core.db import session_scope
from backend.core.events import bus
from backend.core.jobs import JobContext, JobError, handler
from backend.core.models import (
    Attempt,
    Exam,
    ExamHint,
    ExamItem,
    ExamStatus,
    Job,
    JobStatus,
    Note,
    Question,
    Subject,
    TranscriptSegment,
    utcnow,
)
from backend.core.util import format_hms
from backend.pipeline.generate import LANGUAGE_NAMES, record_usage, to_refs
from backend.pipeline.past_exams import analysed_exams, points_per_minute, style_block
from backend.pipeline.subject_hub import current_hub, lectures_with_notes
from backend.study.practice import CHOICE_TYPES, grade_choice

log = logging.getLogger(__name__)

BUILD_JOB = "exam_build"
GRADE_JOB = "exam_grade"
SIM_KIND = "sim"
TYPES = ("mcq", "multi", "tf", "short", "long", "calculation", "code")
DIFFICULTIES = ("exam", "easy", "medium", "hard")
SOURCES = ("generate", "bank")
GRACE_S = 30  # last autosave in flight when the clock runs out
DEFAULT_POINTS_PER_MIN = 1.0
MAX_MATERIAL_CHARS = 200_000  # ~50k tokens of notes
EXCERPT_PAD_S = 20.0
MAX_EXCERPT_CHARS = 1500
SYSTEM_BUILD = (
    "You write university exams for a final-year AI student, from the course's study notes. "
    "Questions must be answerable from the material, precise, and fair; every question cites "
    "the lecture passage it is based on."
)
SYSTEM_GRADE = (
    "You are a strict but fair university examiner grading a student's exam answers against "
    "a rubric. Award partial credit where the answer earns it; never credit what is not in the "
    "answer. Judge content, not style or spelling."
)


class ExamError(Exception):
    pass


# Building ---------------------------------------------------------------------------------------
def create_exam(
    s: Session,
    subject_id: int,
    *,
    duration_min: int,
    topics: list[str] | None = None,
    types: list[str] | None = None,
    difficulty: str = "exam",
    source: str = "generate",
    title: str = "",
    seed: int | None = None,
) -> Exam:
    subject = s.get(Subject, subject_id)
    if subject is None:
        raise ExamError("Subject not found")
    types = [t for t in (types or []) if t in TYPES]
    if difficulty not in DIFFICULTIES:
        raise ExamError(f"difficulty must be one of {DIFFICULTIES}")
    if source not in SOURCES:
        raise ExamError(f"source must be one of {SOURCES}")
    exam = Exam(
        subject_id=subject_id,
        title=title.strip() or default_title(subject, topics or []),
        duration_min=duration_min,
        config={
            "topics": topics or [],
            "types": types,
            "difficulty": difficulty,
            "source": source,
            "target_points": target_points(s, subject_id, duration_min),
            "title_set": bool(title.strip()),
        },
    )
    if source == "bank":
        picked = pick_from_bank(s, exam, seed=seed)
        if not picked:
            raise ExamError(
                "No matching questions yet. Generate study material for lectures first, "
                "or let the AI write the exam."
            )
        s.add(exam)
        s.commit()
        s.refresh(exam)
        add_items(s, exam, picked)
        exam.status = ExamStatus.ready
        s.add(exam)
        s.commit()
    else:
        s.add(exam)
        s.commit()
        s.refresh(exam)
        jobs.enqueue(s, BUILD_JOB, payload={"exam_id": exam.id})
    s.refresh(exam)
    publish(exam)
    return exam


def default_title(subject: Subject, topics: list[str]) -> str:
    if not topics:
        return f"{subject.name}: mock exam"
    shown = ", ".join(topics[:3]) + (f" +{len(topics) - 3}" if len(topics) > 3 else "")
    return f"{subject.name}: {shown}"


def target_points(s: Session, subject_id: int, duration_min: int) -> int:
    rate = points_per_minute(analysed_exams(s, subject_id)) or DEFAULT_POINTS_PER_MIN
    return max(1, round(duration_min * rate))


def topic_question_ids(s: Session, subject_id: int, topics: list[str]) -> set[int] | None:
    """Bank questions belonging to the chosen topics (None: no topic filter)."""
    hub = current_hub(s, subject_id)
    if not topics or hub is None:
        return None
    from backend.study.topics import topics_with_mastery

    wanted = {t.casefold() for t in topics}
    ids: set[int] = set()
    for t in topics_with_mastery(s, subject_id, hub):
        if t["name"].casefold() in wanted:
            ids.update(t["question_ids"])
    return ids


def pick_from_bank(s: Session, exam: Exam, seed: int | None = None) -> list[Question]:
    """Unseen and weakest questions first, until the exam's points are reached."""
    cfg = exam.config
    q = select(Question).where(
        Question.subject_id == exam.subject_id, Question.kind == "exam", col(Question.is_current)
    )
    if cfg["types"]:
        q = q.where(col(Question.type).in_(cfg["types"]))
    pool = list(s.exec(q).all())
    allowed = topic_question_ids(s, exam.subject_id, cfg["topics"])
    if allowed is not None:
        pool = [x for x in pool if x.id in allowed]
    if cfg["difficulty"] in ("easy", "medium", "hard"):
        matching = [x for x in pool if x.difficulty == cfg["difficulty"]]
        pool = matching or pool  # rather a mixed exam than none
    last: dict[int, float] = {}
    if pool:
        for a in s.exec(
            select(Attempt)
            .where(col(Attempt.question_id).in_([x.id for x in pool]))
            .order_by(col(Attempt.id))
        ).all():
            last[a.question_id] = a.score / a.max_points if a.max_points else 1.0
    rng = random.Random(seed)
    rng.shuffle(pool)
    pool.sort(key=lambda x: last.get(x.id, -1.0))  # type: ignore[arg-type]
    picked: list[Question] = []
    total = 0.0
    for x in pool:
        if total >= cfg["target_points"]:
            break
        picked.append(x)
        total += x.points
    # Exam order: choice questions first, then open ones, each in lecture order.
    picked.sort(key=lambda x: (x.type not in CHOICE_TYPES, x.lecture_id or 0, x.id))
    return picked


def snapshot(q: Question, topic: str = "") -> dict[str, Any]:
    keys = (
        "id", "kind", "type", "title", "bloom", "difficulty", "points", "prompt", "options",
        "answer", "explanation", "rubric", "hints", "solution_steps", "expected_output",
        "emphasized", "source_refs", "lecture_id",
    )  # fmt: skip
    return {**{k: getattr(q, k) for k in keys}, "topic": topic}


def add_items(s: Session, exam: Exam, questions: list[Question], topics: list[str] | None = None):
    s.exec(delete(ExamItem).where(ExamItem.exam_id == exam.id))  # type: ignore[call-overload]
    total = 0.0
    for i, q in enumerate(questions):
        s.add(
            ExamItem(
                exam_id=exam.id,  # type: ignore[arg-type]
                idx=i,
                question_id=q.id,
                question=snapshot(q, topics[i] if topics else ""),
                max_points=q.points,
            )
        )
        total += q.points
    exam.max_points = round(total, 2)
    s.add(exam)
    s.commit()


def _current_note(s: Session, lecture_id: int, kind: str) -> Note | None:
    return s.exec(
        select(Note).where(Note.lecture_id == lecture_id, Note.kind == kind, col(Note.is_current))
    ).first()


def exam_material(s: Session, subject: Subject, topics: list[str]) -> tuple[str, list[str]]:
    """The study notes of the chosen topics (all notes if none), labelled for citing."""
    numbered = lectures_with_notes(s, subject.id)  # type: ignore[arg-type]
    by_number = {n: lec for n, lec in numbered}
    wanted: dict[int, set[str]] | None = None  # lecture id -> section headings ("" = all)
    hub = current_hub(s, subject.id)  # type: ignore[arg-type]
    names: list[str] = []
    if topics and hub is not None:
        wanted = {}
        chosen = {t.casefold() for t in topics}
        for t in hub.data.get("topics") or []:
            if t.get("name", "").casefold() not in chosen:
                continue
            names.append(t["name"])
            for p in t.get("places") or []:
                lec = by_number.get(int(p.get("lecture") or 0))
                if lec is not None:
                    wanted.setdefault(lec.id, set()).update(p.get("sections") or [""])  # type: ignore[arg-type]
    parts = [f"# Course: {subject.name}"]
    if subject.instructions.strip():
        parts.append(f"Course notes from the student: {subject.instructions.strip()}")
    size = 0
    for number, lec in numbered:
        if wanted is not None and lec.id not in wanted:
            continue
        notes = _current_note(s, lec.id, "notes")  # type: ignore[arg-type]
        if notes is None:
            continue
        sections = notes.data.get("sections") or []
        if wanted is not None and "" not in wanted[lec.id]:  # type: ignore[index]
            headings = [sec.get("heading", "") for sec in sections]
            keep = set()
            for h in wanted[lec.id]:  # type: ignore[index]
                close = difflib.get_close_matches(h, headings, n=1, cutoff=0.6)
                keep.update(close)
            sections = [sec for sec in sections if sec.get("heading", "") in keep] or sections
        block = [f'\n## L{number} "{lec.title}" ({lec.started_at.astimezone():%Y-%m-%d})']
        for sec in sections:
            refs = ", ".join(
                f"L{number} {r.get('start', '')}-{r.get('end', '')}" for r in sec.get("refs") or []
            )
            star = "★ " if sec.get("emphasized") else ""
            block.append(f"### {star}{sec.get('heading', '')} ({refs})\n{sec.get('markdown', '')}")
        hints = s.exec(
            select(ExamHint).where(ExamHint.lecture_id == lec.id).order_by(col(ExamHint.start_s))
        ).all()
        if hints:
            block.append(
                "Exam hints: "
                + "; ".join(
                    f"[L{number} {format_hms(h.start_s)}] {h.topic or h.quote}" for h in hints
                )
            )
        text = "\n".join(block)
        if size + len(text) > MAX_MATERIAL_CHARS:
            parts.append("\n(Further lectures left out to keep the exam material bounded.)")
            break
        parts.append(text)
        size += len(text)
    if size == 0:
        raise JobError("None of the chosen lectures has study notes yet")
    return "\n".join(parts), names


def build_instruction(s: Session, exam: Exam, subject: Subject, topic_names: list[str]) -> str:
    cfg = exam.config
    types = cfg.get("types") or []
    has_past = bool(analysed_exams(s, exam.subject_id))
    if types:
        mix = "Use only these question types: " + ", ".join(types) + "."
    elif has_past:
        mix = "Use the mix of question types the past exams use."
    else:
        mix = (
            "Mix the types like a real written exam: a few mcq/multi/tf, short answers, and "
            "long, calculation/derivation or code tasks where the material allows."
        )
    difficulty = {
        "exam": "as hard as the real exam: mostly apply/analyze, some remember/understand, "
        "one or two evaluate/create tasks",
        "easy": "easier than the real exam (warm-up): mostly remember/understand",
        "medium": "medium: understand/apply",
        "hard": "harder than the real exam: analyze/evaluate, multi-step tasks",
    }[cfg.get("difficulty", "exam")]
    out_lang = LANGUAGE_NAMES.get(subject.output_language, subject.output_language or "English")
    return load_prompt("exam_build").render(
        duration=exam.duration_min,
        points=cfg.get("target_points") or exam.duration_min,
        topics=("Topics: " + "; ".join(topic_names))
        if topic_names
        else "Topics: the whole course.",
        mix=mix,
        difficulty=difficulty,
        output_language=out_lang,
    ) + style_block(s, exam.subject_id)


@handler(BUILD_JOB)
def build_exam(ctx: JobContext) -> None:
    exam_id = ctx.payload["exam_id"]
    with session_scope() as s:
        exam = s.get(Exam, exam_id)
        if exam is None or exam.status not in (ExamStatus.building, ExamStatus.failed):
            return
        _set_status(s, exam, ExamStatus.building)
        subject_id = exam.subject_id
        subject = s.get(Subject, subject_id)
        assert subject is not None
        numbers = {n: lec for n, lec in lectures_with_notes(s, subject.id)}  # type: ignore[arg-type]
        try:
            material, names = exam_material(s, subject, exam.config.get("topics") or [])
            instruction = build_instruction(s, exam, subject, names)
        except JobError as e:
            _fail(s, exam, str(e))
            raise
    ctx.progress(0.1, "Writing the exam")
    try:
        result = get_provider().complete(
            LLMRequest(
                task=BUILD_JOB,
                system=SYSTEM_BUILD,
                context=material,
                instruction=instruction,
                output=sc.SimExamOut,
            )
        )
    except Exception as e:
        with session_scope() as s:
            exam = s.get(Exam, exam_id)
            if exam is not None:
                _fail(s, exam, str(e))
        raise
    cost = record_usage(None, subject_id, BUILD_JOB, result)
    out: sc.SimExamOut = result.parsed
    with session_scope() as s:
        exam = s.get(Exam, exam_id)
        if exam is None:
            return
        questions, topics = [], []
        for x in out.questions:
            refs: list[dict[str, Any]] = []
            for r in x.refs:
                lec = numbers.get(r.lecture)
                if lec is not None:
                    duration = lec.duration_s or float("inf")
                    refs += to_refs(lec.id, [r], duration)  # type: ignore[arg-type]
            q = Question(
                subject_id=exam.subject_id,
                lecture_id=refs[0]["lecture_id"] if refs else None,
                kind=SIM_KIND,
                type=x.type,
                bloom=x.bloom,
                difficulty=x.difficulty,
                points=max(x.points, 0.5),
                prompt=x.prompt,
                options=[o.model_dump() for o in x.options] if x.type in CHOICE_TYPES else [],
                answer=x.answer,
                explanation=x.explanation,
                rubric=[r.model_dump() for r in x.rubric],
                emphasized=x.emphasized,
                source_refs=refs,
                model=result.model,
            )
            s.add(q)
            questions.append(q)
            topics.append(x.topic)
        if not questions:
            _fail(s, exam, "The model returned no questions")
            raise JobError("The model returned no questions")
        s.commit()
        for q in questions:
            s.refresh(q)
        add_items(s, exam, questions, topics)
        if out.title.strip() and not exam.config.get("title_set"):
            exam.title = f"{exam.title.split(':', 1)[0]}: {out.title.strip()}"[:200]
        exam.model = result.model
        exam.cost_usd = (exam.cost_usd or 0) + cost
        exam.error = None
        _set_status(s, exam, ExamStatus.ready)
    log.info("Exam %s built: %d questions", exam_id, len(questions))


# Sitting ----------------------------------------------------------------------------------------
def start(s: Session, exam: Exam) -> Exam:
    if exam.status == ExamStatus.running:
        return exam
    if exam.status != ExamStatus.ready:
        raise ExamError("This exam can't be started")
    now = utcnow()
    exam.started_at = now
    exam.deadline = now + timedelta(minutes=exam.duration_min)
    _set_status(s, exam, ExamStatus.running)
    return exam


def time_left(exam: Exam) -> float | None:
    if exam.status != ExamStatus.running or exam.deadline is None:
        return None
    return max(0.0, (exam.deadline - utcnow()).total_seconds())


def has_active_job(s: Session, exam_id: int) -> bool:
    active = s.exec(
        select(Job).where(
            col(Job.kind).in_([BUILD_JOB, GRADE_JOB]),
            col(Job.status).in_([JobStatus.queued, JobStatus.running]),
        )
    ).all()
    return any(j.payload.get("exam_id") == exam_id for j in active)


def check_deadline(s: Session, exam: Exam) -> Exam:
    """Bring a stored exam up to date before showing it.

    A running exam past its deadline is submitted (the browser may have been closed); one
    whose build or grading job was cancelled on the Jobs page is marked failed, so it can be
    retried or deleted.
    """
    if exam.status in (ExamStatus.building, ExamStatus.grading) and not has_active_job(
        s,
        exam.id,  # type: ignore[arg-type]
    ):
        _fail(s, exam, "The job was cancelled")
        return exam
    if (
        exam.status == ExamStatus.running
        and exam.deadline is not None
        and utcnow() > exam.deadline + timedelta(seconds=GRACE_S)
    ):
        submit(s, exam)
    return exam


def save_answer(
    s: Session,
    exam: Exam,
    item: ExamItem,
    *,
    answer_text: str | None = None,
    selected: list[int] | None = None,
    flagged: bool | None = None,
) -> ExamItem:
    check_deadline(s, exam)
    if exam.status != ExamStatus.running:
        raise ExamError("Time is up: the exam has been submitted")
    if answer_text is not None:
        item.answer_text = answer_text
    if selected is not None:
        n = len(item.question.get("options") or [])
        if any(not 0 <= i < n for i in selected):
            raise ExamError("Unknown option")
        item.selected = sorted(set(selected))
    if flagged is not None:
        item.flagged = flagged
    s.add(item)
    s.commit()
    s.refresh(item)
    return item


def items_of(s: Session, exam_id: int) -> list[ExamItem]:
    return list(
        s.exec(select(ExamItem).where(ExamItem.exam_id == exam_id).order_by(col(ExamItem.idx)))
    )


def is_choice(item: ExamItem) -> bool:
    return item.question.get("type") in CHOICE_TYPES and bool(item.question.get("options"))


def submit(s: Session, exam: Exam) -> Exam:
    if exam.status != ExamStatus.running:
        raise ExamError("This exam is not running")
    exam.submitted_at = utcnow()
    open_items = False
    for item in items_of(s, exam.id):  # type: ignore[arg-type]
        q = item.question
        if is_choice(item):
            probe = Question(
                subject_id=exam.subject_id,
                type=q["type"],
                prompt=q["prompt"],
                options=q["options"],
                points=q["points"],
            )
            _, score = grade_choice(probe, item.selected) if item.selected else (False, 0.0)
            item.score, item.graded_by = score, "auto"
            if item.selected:
                right = [i for i, o in enumerate(q["options"]) if o.get("correct")]
                item.feedback = "" if set(item.selected) == set(right) else q.get("explanation", "")
            else:
                item.feedback = "Not answered."
        elif not item.answer_text.strip():
            item.score, item.graded_by, item.feedback = 0.0, "empty", "Not answered."
            item.rubric_awarded = [{"points": 0.0, "comment": ""} for _ in q.get("rubric") or []]
        else:
            item.score, item.graded_by = None, ""
            open_items = True
        s.add(item)
    if open_items:
        _set_status(s, exam, ExamStatus.grading)
        jobs.enqueue(s, GRADE_JOB, payload={"exam_id": exam.id})
    else:
        finish(s, exam)
    return exam


# Grading ----------------------------------------------------------------------------------------
def source_excerpt(s: Session, refs: list[dict[str, Any]]) -> str:
    chunks = []
    for r in refs[:2]:
        if r.get("lecture_id") is None or r.get("start_s") is None:
            continue
        lo = r["start_s"] - EXCERPT_PAD_S
        hi = (r.get("end_s") or r["start_s"]) + EXCERPT_PAD_S
        segs = s.exec(
            select(TranscriptSegment)
            .where(
                TranscriptSegment.lecture_id == r["lecture_id"],
                col(TranscriptSegment.start) >= lo,
                col(TranscriptSegment.start) <= hi,
            )
            .order_by(col(TranscriptSegment.idx))
        ).all()
        text = " ".join(f"[{format_hms(x.start)}] {x.text}" for x in segs)
        if text:
            chunks.append(text[:MAX_EXCERPT_CHARS])
    return "\n".join(chunks) or "(no excerpt)"


def grading_text(s: Session, items: list[ExamItem]) -> str:
    blocks = []
    for item in items:
        q = item.question
        rubric = q.get("rubric") or []
        lines = [
            f"### Item id={item.id} ({q['points']:g} points, type {q['type']})",
            "Question:",
            q["prompt"].strip(),
        ]
        if rubric:
            lines.append("Rubric (criterion index, maximum points):")
            lines += [
                f"{i}. ({float(r.get('points', 0)):g} pts) {r.get('criterion', '')}"
                for i, r in enumerate(rubric)
            ]
        lines += ["Model answer:", (q.get("answer") or "").strip() or "(none)"]
        lines += [
            "Lecture source (transcript excerpt):",
            source_excerpt(s, q.get("source_refs", [])),
        ]
        lines += ["Student answer:", '"""', item.answer_text.strip(), '"""']
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def apply_grade(item: ExamItem, g: sc.ItemGrade) -> None:
    """Clamp the model's points to the rubric and the question's points."""
    q = item.question
    rubric = q.get("rubric") or []
    points = float(q.get("points") or 0)
    if rubric:
        awarded = [{"points": 0.0, "comment": ""} for _ in rubric]
        for r in g.rubric:
            if 0 <= r.index < len(rubric):
                cap = float(rubric[r.index].get("points", 0))
                awarded[r.index] = {
                    "points": round(min(max(r.points, 0.0), cap), 2),
                    "comment": r.comment.strip(),
                }
        item.rubric_awarded = awarded
        score = sum(a["points"] for a in awarded)
    else:
        score = g.score
    item.score = round(min(max(score, 0.0), points), 2)
    item.feedback = g.feedback.strip()
    item.graded_by = "ai"


@handler(GRADE_JOB)
def grade_exam(ctx: JobContext) -> None:
    exam_id = ctx.payload["exam_id"]
    with session_scope() as s:
        exam = s.get(Exam, exam_id)
        if exam is None or exam.status not in (ExamStatus.grading, ExamStatus.failed):
            return
        if exam.submitted_at is None:
            return  # a failed build, not a grading
        _set_status(s, exam, ExamStatus.grading)
        subject_id = exam.subject_id
    total_cost = 0.0
    overall = ""
    model = ""
    try:
        for attempt in range(2):  # a second call for items the model skipped
            with session_scope() as s:
                todo = [i for i in items_of(s, exam_id) if i.graded_by == ""]
                if not todo:
                    break
                ctx.progress(0.1 + 0.4 * attempt, f"Grading {len(todo)} answer(s)")
                text = grading_text(s, todo)
            result: LLMResult = get_provider().complete(
                LLMRequest(
                    task=GRADE_JOB,
                    system=SYSTEM_GRADE,
                    context=text,
                    instruction=load_prompt("exam_grade").render(),
                    output=sc.ExamGradeOut,
                )
            )
            total_cost += record_usage(None, subject_id, GRADE_JOB, result)
            model = result.model
            out: sc.ExamGradeOut = result.parsed
            overall = overall or out.overall
            grades = {g.item_id: g for g in out.items}
            with session_scope() as s:
                for item in items_of(s, exam_id):
                    if item.graded_by == "" and item.id in grades:
                        apply_grade(item, grades[item.id])
                        s.add(item)
                s.commit()
        with session_scope() as s:
            if any(i.graded_by == "" for i in items_of(s, exam_id)):
                raise JobError("The model did not grade every answer; retry the job")
    except Exception as e:
        with session_scope() as s:
            exam = s.get(Exam, exam_id)
            if exam is not None:
                exam.cost_usd = (exam.cost_usd or 0) + total_cost
                _fail(s, exam, str(e))
        raise
    with session_scope() as s:
        exam = s.get(Exam, exam_id)
        if exam is None:
            return
        exam.feedback_md = overall.strip()
        exam.cost_usd = (exam.cost_usd or 0) + total_cost
        exam.model = model or exam.model
        finish(s, exam)
    bus.publish(
        "notify",
        {"title": "Exam graded", "message": exam.title, "level": "success", "exam_id": exam_id},
    )


def finish(s: Session, exam: Exam) -> None:
    """All items graded: total the score and log each item as a practice attempt."""
    items = items_of(s, exam.id)  # type: ignore[arg-type]
    exam.score = round(sum(i.score or 0 for i in items), 2)
    exam.max_points = round(sum(i.max_points for i in items), 2)
    exam.graded_at = utcnow()
    exam.error = None
    taken_ms = None
    if exam.started_at and exam.submitted_at:
        taken_ms = int((exam.submitted_at - exam.started_at).total_seconds() * 1000)
    for i in items:
        if i.question_id is None or s.get(Question, i.question_id) is None:
            continue
        rubric = i.question.get("rubric") or []
        full = [
            k
            for k, a in enumerate(i.rubric_awarded)
            if k < len(rubric) and a["points"] >= float(rubric[k].get("points", 0)) - 1e-9
        ]
        s.add(
            Attempt(
                question_id=i.question_id,
                selected=i.selected,
                answer_text=i.answer_text,
                rubric_checked=full,
                auto_graded=True,
                correct=(i.score or 0) >= i.max_points - 1e-9,
                score=i.score or 0.0,
                max_points=i.max_points,
                duration_ms=taken_ms // len(items) if taken_ms and items else None,
            )
        )
    _set_status(s, exam, ExamStatus.graded)


# Helpers ----------------------------------------------------------------------------------------
def _set_status(s: Session, exam: Exam, status: ExamStatus) -> None:
    exam.status = status
    s.add(exam)
    s.commit()
    s.refresh(exam)
    publish(exam)


def _fail(s: Session, exam: Exam, error: str) -> None:
    exam.error = error
    _set_status(s, exam, ExamStatus.failed)


def publish(exam: Exam) -> None:
    bus.publish("exam", {"id": exam.id, "subject_id": exam.subject_id, "status": exam.status})


def retry(s: Session, exam: Exam) -> None:
    if exam.status != ExamStatus.failed:
        raise ExamError("Only a failed exam can be retried")
    kind = GRADE_JOB if exam.submitted_at else BUILD_JOB
    exam.error = None
    _set_status(s, exam, ExamStatus.grading if exam.submitted_at else ExamStatus.building)
    jobs.enqueue(s, kind, payload={"exam_id": exam.id})


def delete_exam(s: Session, exam: Exam) -> None:
    """Delete an exam with the questions written for it (its attempts go with them)."""
    sim_ids = [
        i.question_id
        for i in items_of(s, exam.id)  # type: ignore[arg-type]
        if i.question_id is not None and i.question.get("kind", SIM_KIND) == SIM_KIND
    ]
    s.exec(delete(ExamItem).where(ExamItem.exam_id == exam.id))  # type: ignore[call-overload]
    if sim_ids:
        for q in s.exec(
            select(Question).where(col(Question.id).in_(sim_ids), Question.kind == SIM_KIND)
        ).all():
            s.delete(q)
    s.delete(exam)
    s.commit()


def lecture_numbers(s: Session, subject_id: int) -> dict[int, int]:
    return {lec.id: n for n, lec in lectures_with_notes(s, subject_id)}  # type: ignore[misc]


def ref_label(numbers: dict[int, int], ref: dict[str, Any]) -> str:
    n = numbers.get(ref.get("lecture_id") or 0)
    t = ref.get("start_s")
    return f"L{n}" + (f" · {format_hms(t)}" if t is not None else "") if n else ""
