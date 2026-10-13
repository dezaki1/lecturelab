"""Teach-back and oral exam: conversations with the AI, by voice or typed.

Teach-back (Feynman technique): the student explains a topic in their own words; the main
model compares it with the lecture notes and returns what was covered, what is missing and
what is wrong (with lecture refs) plus one follow-up question about the biggest gap. The answer
to that follow-up is judged together with the first explanation.

Oral exam: the examiner (main model, low effort so turns come back quickly) asks
``questions`` main questions, each with up to two follow-ups, then the whole conversation is
evaluated (main model, normal effort): score, Austrian grade, per-question feedback, gaps.

The lecture notes of the chosen topics form the cached prompt prefix (the lecture set is fixed
when the session starts); the conversation goes after the cache breakpoint. Calls are
synchronous: a turn is a few seconds, and an answer must not wait behind queued jobs.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlmodel import Session, col, select

from backend.ai import schemas as sc
from backend.ai.prompts import load_prompt
from backend.ai.providers import get_provider, is_configured
from backend.ai.types import LLMError, LLMRequest
from backend.core.db import session_scope
from backend.core.events import bus
from backend.core.models import SpeakSession, Subject, utcnow
from backend.pipeline.generate import parse_ts, record_usage
from backend.study.exams import _notes_material
from backend.study.weak import topic_stats

log = logging.getLogger(__name__)

SYSTEM = (
    "You are an experienced university examiner and tutor for an AI course. You judge strictly "
    "against the course material provided and never invent facts beyond it."
)
MAX_FOLLOWUPS = 2
MAX_TEACH_ROUNDS = 2  # explanation + answer to the follow-up
MAX_ANSWER_CHARS = 8000


class SpeakError(Exception):
    pass


def topics(s: Session, subject_id: int) -> list[dict[str, Any]]:
    return [
        {
            "name": t["name"],
            "strength": t["strength"],
            "status": t["status"],
            "exam_relevant": t["exam_relevant"],
            "lecture_ids": sorted({p["lecture_id"] for p in t["places"]}),
        }
        for t in topic_stats(s, subject_id)
    ]


def _lecture_ids(s: Session, subject_id: int, names: list[str]) -> list[int]:
    wanted = set(names)
    return sorted(
        {lid for t in topics(s, subject_id) if t["name"] in wanted for lid in t["lecture_ids"]}
    )


def _material(s: Session, sess: SpeakSession) -> tuple[str, dict[int, Any]]:
    ids = set(sess.config.get("lecture_ids") or []) or None
    text, numbers = _notes_material(s, sess.subject_id, ids)
    if not text.strip():
        raise SpeakError("This subject has no study notes yet: generate them for a lecture first")
    return text, numbers


def _conversation(sess: SpeakSession) -> str:
    who = {"examiner": "Examiner", "student": "Student", "coach": "Tutor feedback"}
    lines = [f"{who.get(t['role'], t['role'])}: {t['text']}" for t in sess.turns]
    return "# Conversation so far\n\n" + "\n\n".join(lines)


def _refs(refs: list[sc.LectureRef], numbers: dict[int, Any]) -> list[dict[str, Any]]:
    out = []
    for r in refs:
        lec = numbers.get(r.lecture)
        t = parse_ts(r.at)
        if lec is None or t is None:
            continue
        out.append(
            {
                "lecture_id": lec.id,
                "start_s": t,
                "label": f"L{r.lecture} {r.at}",
                "title": lec.title,
            }
        )
    return out


def _call(
    sess: SpeakSession,
    subject: Subject,
    task: str,
    output: type,
    effort: str | None = None,
    **values: Any,
) -> Any:
    if not is_configured():
        raise SpeakError("No AI provider is set up (Settings → AI provider)")
    with session_scope() as s:
        material, numbers = _material(s, sess)
    result = get_provider().complete(
        LLMRequest(
            task=task,
            system=SYSTEM,
            context=f"# Lecture notes of {subject.name}\n\n{material}",
            instruction=load_prompt(task).render(
                subject=subject.name, conversation=_conversation(sess), **values
            ),
            output=output,
            effort=effort,
            max_tokens=8000,
        )
    )
    sess.cost_usd = (sess.cost_usd or 0) + record_usage(None, subject.id, task, result)  # type: ignore[arg-type]
    return result.parsed, numbers


def _save(sess: SpeakSession) -> dict[str, Any]:
    with session_scope() as s:
        s.add(sess)
        s.commit()
        s.refresh(sess)
    bus.publish("speak", {"id": sess.id, "subject_id": sess.subject_id})
    return session_dict(sess)


def session_dict(sess: SpeakSession) -> dict[str, Any]:
    return {
        "id": sess.id,
        "subject_id": sess.subject_id,
        "mode": sess.mode,
        "title": sess.title,
        "config": sess.config,
        "turns": sess.turns,
        "result": sess.result,
        "score": sess.score,
        "status": sess.status,
        "error": sess.error,
        "cost_usd": round(sess.cost_usd or 0, 4),
        "created_at": sess.created_at,
        "finished_at": sess.finished_at,
    }


def _load(session_id: int) -> tuple[SpeakSession, Subject]:
    with session_scope() as s:
        sess = s.get(SpeakSession, session_id)
        if sess is None:
            raise SpeakError("Session not found")
        subject = s.get(Subject, sess.subject_id)
        assert subject is not None
        s.expunge(sess)
        s.expunge(subject)
    return sess, subject


# Start ----------------------------------------------------------------------------------------


def start(subject_id: int, mode: str, topic_names: list[str], questions: int, voice: bool) -> dict:
    with session_scope() as s:
        subject = s.get(Subject, subject_id)
        if subject is None:
            raise SpeakError("Subject not found")
        s.expunge(subject)
        lecture_ids = _lecture_ids(s, subject_id, topic_names) if topic_names else []
    if mode == "teach" and len(topic_names) != 1:
        raise SpeakError("Pick the one topic you want to explain")
    title = topic_names[0] if mode == "teach" else (", ".join(topic_names) or "All topics")
    sess = SpeakSession(
        subject_id=subject_id,
        mode=mode,
        title=title[:200],
        config={
            "topics": topic_names,
            "questions": questions,
            "voice": voice,
            "lecture_ids": lecture_ids,
        },
    )
    with session_scope() as s:
        _material(s, sess)  # fail early without notes
    if mode == "teach":
        sess.turns = [
            {
                "role": "examiner",
                "kind": "prompt",
                "text": f"Explain {topic_names[0]} in your own words, as if you were teaching a "
                "classmate: what it is, why it matters and how it works.",
            }
        ]
    else:
        _examiner_turn(sess, subject)
    return _save(sess)


# Turns ----------------------------------------------------------------------------------------


def _examiner_turn(sess: SpeakSession, subject: Subject) -> None:
    asked = sum(1 for t in sess.turns if t["role"] == "examiner" and t.get("kind") == "question")
    followups = 0
    for t in reversed(sess.turns):
        if t["role"] != "examiner":
            continue
        if t.get("kind") != "follow_up":
            break
        followups += 1
    n = int(sess.config.get("questions") or 3)
    out, _ = _call(
        sess,
        subject,
        "oral_turn",
        sc.OralTurnOut,
        effort="low",
        topics=", ".join(sess.config.get("topics") or []) or "all topics in the notes",
        questions=n,
        asked=asked,
        followups=followups,
    )
    kind = out.kind
    # Hard limits in case the model doesn't keep count.
    if kind == "follow_up" and followups >= MAX_FOLLOWUPS:
        kind = "question"
    if kind == "question" and asked >= n:
        kind = "end"
    if kind == "follow_up" and asked == 0:
        kind = "question"
    turns = list(sess.turns)
    if turns and turns[-1]["role"] == "student" and asked:
        turns[-1] = {**turns[-1], "rating": out.last_answer}
    say = out.say.strip()
    if kind == "end" and out.kind != "end":
        say = "Thank you, that's the end of the exam."
    turns.append({"role": "examiner", "kind": kind, "text": say, "topic": out.topic})
    sess.turns = turns


def answer(session_id: int, text: str, spoken: bool = False) -> dict[str, Any]:
    sess, subject = _load(session_id)
    if sess.status != "active":
        raise SpeakError("This session is already finished")
    text = text.strip()[:MAX_ANSWER_CHARS]
    if not text:
        raise SpeakError("Say or type an answer first")
    sess.turns = [
        *sess.turns,
        {"role": "student", "kind": "answer", "text": text, "spoken": spoken},
    ]
    try:
        if sess.mode == "teach":
            _teach_feedback(sess, subject)
        else:
            _examiner_turn(sess, subject)
            if sess.turns[-1]["kind"] == "end":
                _evaluate_oral(sess, subject)
    except (LLMError, SpeakError) as e:
        sess.turns = sess.turns[:-1]  # let the student send it again
        raise SpeakError(str(e)) from e
    return _save(sess)


def _teach_feedback(sess: SpeakSession, subject: Subject) -> None:
    topic = sess.config["topics"][0]
    out, numbers = _call(sess, subject, "teach_back", sc.TeachBackOut, topic=topic)
    result = {
        "score": max(0, min(100, out.score)),
        "verdict": out.verdict,
        "covered": out.covered,
        "missing": [{"point": m.point, "refs": _refs(m.refs, numbers)} for m in out.missing],
        "misconceptions": [
            {
                "student_said": m.student_said,
                "correction": m.correction,
                "refs": _refs(m.refs, numbers),
            }
            for m in out.misconceptions
        ],
        "feedback": out.feedback,
    }
    rounds = sum(1 for t in sess.turns if t["role"] == "student")
    turns = [
        *sess.turns,
        {"role": "coach", "kind": "feedback", "text": out.feedback, "data": result},
    ]
    if out.follow_up.strip() and rounds < MAX_TEACH_ROUNDS:
        turns.append({"role": "examiner", "kind": "follow_up", "text": out.follow_up.strip()})
    else:
        sess.status = "done"
        sess.finished_at = utcnow()
    sess.turns = turns
    sess.result = result
    sess.score = result["score"]


def _evaluate_oral(sess: SpeakSession, subject: Subject) -> None:
    out, numbers = _call(sess, subject, "oral_result", sc.OralResultOut)
    sess.result = {
        "score": max(0, min(100, out.score)),
        "grade": max(1, min(5, out.grade)),
        "summary": out.summary,
        "strengths": out.strengths,
        "gaps": [{"point": g.point, "refs": _refs(g.refs, numbers)} for g in out.gaps],
        "misconceptions": [
            {
                "student_said": m.student_said,
                "correction": m.correction,
                "refs": _refs(m.refs, numbers),
            }
            for m in out.misconceptions
        ],
        "questions": [
            {
                "question": q.question,
                "score": max(0.0, min(10.0, q.score)),
                "comment": q.comment,
                "refs": _refs(q.refs, numbers),
            }
            for q in out.questions
        ],
        "revise": out.revise,
    }
    sess.score = sess.result["score"]
    sess.status = "done"
    sess.finished_at = utcnow()


def finish(session_id: int) -> dict[str, Any]:
    """End early: the oral exam is evaluated on what was answered so far."""
    sess, subject = _load(session_id)
    if sess.status != "active":
        return session_dict(sess)
    answered = any(t["role"] == "student" for t in sess.turns)
    if sess.mode == "oral" and answered:
        try:
            _evaluate_oral(sess, subject)
        except LLMError as e:
            raise SpeakError(str(e)) from e
    else:
        sess.status = "done"
        sess.finished_at = utcnow()
    return _save(sess)


def listing(s: Session, subject_id: int | None = None, limit: int = 30) -> list[dict[str, Any]]:
    q = select(SpeakSession).order_by(col(SpeakSession.id).desc()).limit(limit)
    if subject_id:
        q = q.where(SpeakSession.subject_id == subject_id)
    names = {x.id: x.name for x in s.exec(select(Subject)).all()}
    return [
        {
            "id": x.id,
            "subject_id": x.subject_id,
            "subject_name": names.get(x.subject_id, ""),
            "mode": x.mode,
            "title": x.title,
            "status": x.status,
            "score": x.score,
            "grade": x.result.get("grade"),
            "created_at": x.created_at,
        }
        for x in s.exec(q).all()
    ]
