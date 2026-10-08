"""Question practice: auto-graded choice questions, rubric-based self-grading for open ones."""

from __future__ import annotations

import random
from typing import Any

from sqlmodel import Session, col, select

from backend.core.models import Attempt, Lecture, Question

CHOICE_TYPES = {"mcq", "multi", "tf"}


class PracticeError(Exception):
    pass


def grade_choice(q: Question, selected: list[int]) -> tuple[bool, float]:
    """MCQ/TF: all or nothing. Multi-select: partial credit, wrong picks cancel right ones."""
    correct = {i for i, o in enumerate(q.options) if o.get("correct")}
    chosen = set(selected)
    if q.type != "multi":
        ok = chosen == correct
        return ok, q.points if ok else 0.0
    if not correct:
        return not chosen, q.points if not chosen else 0.0
    right = len(chosen & correct)
    wrong = len(chosen - correct)
    fraction = max(0.0, (right - wrong) / len(correct))
    return chosen == correct, round(q.points * fraction, 2)


def submit(
    s: Session,
    question_id: int,
    *,
    selected: list[int] | None = None,
    answer_text: str = "",
    rubric_checked: list[int] | None = None,
    self_score: float | None = None,
    duration_ms: int | None = None,
) -> Attempt:
    q = s.get(Question, question_id)
    if q is None:
        raise PracticeError("Question not found")
    attempt = Attempt(
        question_id=question_id,
        answer_text=answer_text,
        duration_ms=duration_ms,
        max_points=q.points,
    )
    if q.type in CHOICE_TYPES and q.options:
        if selected is None:
            raise PracticeError("Select an option")
        bad = [i for i in selected if not 0 <= i < len(q.options)]
        if bad:
            raise PracticeError(f"Unknown option(s): {bad}")
        attempt.selected = sorted(set(selected))
        attempt.correct, attempt.score = grade_choice(q, attempt.selected)
        attempt.auto_graded = True
    else:
        # Open question: the student compares with the model answer and ticks rubric items
        # (or gives a score directly when there is no rubric).
        checked = sorted(set(rubric_checked or []))
        if checked:
            bad = [i for i in checked if not 0 <= i < len(q.rubric)]
            if bad:
                raise PracticeError(f"Unknown rubric item(s): {bad}")
            score = sum(float(q.rubric[i].get("points", 0)) for i in checked)
        elif self_score is not None:
            score = self_score
        else:
            score = 0.0
        attempt.rubric_checked = checked
        attempt.score = round(min(max(score, 0.0), q.points), 2)
        attempt.correct = attempt.score >= q.points - 1e-9
    s.add(attempt)
    s.commit()
    s.refresh(attempt)
    return attempt


def pick_questions(
    s: Session,
    *,
    subject_id: int | None = None,
    lecture_id: int | None = None,
    kind: str = "exam",
    mode: str = "mixed",
    limit: int = 10,
    seed: int | None = None,
) -> list[dict[str, Any]]:
    """mixed: random order; weakest: never-tried and last-wrong first, then the rest."""
    q = (
        select(Question)
        .join(Lecture, col(Lecture.id) == col(Question.lecture_id), isouter=True)
        .where(col(Question.is_current), Question.kind == kind)
    )
    if subject_id:
        q = q.where(Question.subject_id == subject_id)
    if lecture_id:
        q = q.where(Question.lecture_id == lecture_id)
    questions = list(s.exec(q).all())
    last: dict[int, Attempt] = {}
    tries: dict[int, int] = {}
    if questions:
        for a in s.exec(
            select(Attempt)
            .where(col(Attempt.question_id).in_([x.id for x in questions]))
            .order_by(col(Attempt.id))
        ).all():
            last[a.question_id] = a
            tries[a.question_id] = tries.get(a.question_id, 0) + 1
    rng = random.Random(seed)
    rng.shuffle(questions)
    if mode == "weakest":

        def weakness(x: Question) -> float:
            a = last.get(x.id)  # type: ignore[arg-type]
            if a is None:
                return 0.0  # never tried
            return 1.0 + (a.score / a.max_points if a.max_points else 1.0)

        questions.sort(key=weakness)
    out = []
    for x in questions[:limit]:
        a = last.get(x.id)  # type: ignore[arg-type]
        out.append(
            {
                "id": x.id,
                "attempts": tries.get(x.id, 0),  # type: ignore[arg-type]
                "last_score": a.score if a else None,
                "last_max": a.max_points if a else None,
            }
        )
    return out
