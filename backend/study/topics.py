"""Subject hub read side: cumulative glossary, topics with mastery, concept map.

Topics come from the course overview (``pipeline.subject_hub``), each listing the note
sections that teach it. A section's refs give its time range in the lecture; the flashcards and
questions whose sources fall inside those ranges belong to the topic. Mastery is the mean FSRS
recall probability of those cards (unseen cards count as 0), as on the dashboard.
"""

from __future__ import annotations

import difflib
import re
from datetime import datetime
from typing import Any

from fsrs import Scheduler
from sqlmodel import Session, col, select

from backend.core.config import get_settings
from backend.core.models import Attempt, Flashcard, Lecture, Note, Question
from backend.pipeline.generate import parse_ts
from backend.pipeline.subject_hub import lectures_with_notes
from backend.study.dashboard import retrievability
from backend.study.review import now_utc

Range = tuple[float, float]
WHOLE: Range = (0.0, float("inf"))
SLACK_S = 5.0


def _norm(term: str) -> str:
    return re.sub(r"[^\w]+", " ", term.casefold()).strip()


def _current(s: Session, lecture_id: int, kind: str) -> Note | None:
    return s.exec(
        select(Note).where(Note.lecture_id == lecture_id, Note.kind == kind, col(Note.is_current))
    ).first()


def cumulative_glossary(s: Session, subject_id: int) -> list[dict[str, Any]]:
    """All lectures' glossary terms merged by name, with where each appears."""
    merged: dict[str, dict[str, Any]] = {}
    for number, lec in lectures_with_notes(s, subject_id):
        note = _current(s, lec.id, "glossary")  # type: ignore[arg-type]
        if note is None:
            continue
        for t in note.data.get("terms") or []:
            term = (t.get("term") or "").strip()
            if not term:
                continue
            entry = merged.setdefault(
                _norm(term),
                {"term": term, "german": "", "definition": t.get("definition", ""), "lectures": []},
            )
            if not entry["german"] and t.get("german"):
                entry["german"] = t["german"]
            entry["lectures"].append(
                {
                    "lecture_id": lec.id,
                    "number": number,
                    "title": lec.title,
                    "t_s": parse_ts(t.get("first_mention") or ""),
                }
            )
    return sorted(merged.values(), key=lambda e: e["term"].casefold())


def _section_ranges(note: Note | None, headings: list[str]) -> list[Range]:
    if note is None:
        return [WHOLE]
    sections = note.data.get("sections") or []
    by_heading = {sec.get("heading", ""): sec for sec in sections}
    ranges: list[Range] = []
    for h in headings:
        sec = by_heading.get(h)
        if sec is None:
            close = difflib.get_close_matches(h, list(by_heading), n=1, cutoff=0.6)
            sec = by_heading[close[0]] if close else None
        if sec is None:
            continue
        times = [
            (parse_ts(r.get("start", "")), parse_ts(r.get("end", "")))
            for r in sec.get("refs") or []
        ]
        for a, b in times:
            if a is not None:
                ranges.append((a - SLACK_S, (b if b is not None and b >= a else a) + SLACK_S))
    return ranges or [WHOLE]


def _inside(refs: list[dict[str, Any]], lecture_id: int, ranges: list[Range]) -> bool:
    for r in refs:
        if r.get("lecture_id") != lecture_id or r.get("start_s") is None:
            continue
        if any(a <= r["start_s"] <= b for a, b in ranges):
            return True
    return False


def topics_with_mastery(
    s: Session, subject_id: int, hub: Note, now: datetime | None = None
) -> list[dict[str, Any]]:
    now = now or now_utc()
    sch = Scheduler(desired_retention=get_settings().study.desired_retention)
    ids = {int(k): v for k, v in (hub.data.get("lecture_ids") or {}).items()}
    lectures = {
        lec.id: lec for lec in s.exec(select(Lecture).where(Lecture.subject_id == subject_id)).all()
    }
    numbers = {lec.id: n for n, lec in lectures_with_notes(s, subject_id)}
    cards_by_lecture: dict[int, list[Flashcard]] = {}
    for c in s.exec(
        select(Flashcard).where(
            Flashcard.subject_id == subject_id,
            col(Flashcard.is_current),
            col(Flashcard.suspended).is_(False),
        )
    ).all():
        cards_by_lecture.setdefault(c.lecture_id or 0, []).append(c)
    questions_by_lecture: dict[int, list[Question]] = {}
    for q in s.exec(
        select(Question).where(Question.subject_id == subject_id, col(Question.is_current))
    ).all():
        questions_by_lecture.setdefault(q.lecture_id or 0, []).append(q)
    scores: dict[int, tuple[float, float]] = {}
    for qid, score, mx in s.exec(select(Attempt.question_id, Attempt.score, Attempt.max_points)):
        a, b = scores.get(qid, (0.0, 0.0))
        scores[qid] = (a + score, b + mx)

    out = []
    for t in hub.data.get("topics") or []:
        cards: dict[int, Flashcard] = {}
        questions: dict[int, Question] = {}
        places = []
        for p in t.get("places") or []:
            lid = ids.get(int(p.get("lecture", 0)))
            if lid is None or lid not in lectures:
                continue
            ranges = _section_ranges(_current(s, lid, "notes"), p.get("sections") or [])
            for c in cards_by_lecture.get(lid, []):
                if ranges == [WHOLE] or _inside(c.source_refs, lid, ranges):
                    cards[c.id] = c  # type: ignore[index]
            for q in questions_by_lecture.get(lid, []):
                if ranges == [WHOLE] or _inside(q.source_refs, lid, ranges):
                    questions[q.id] = q  # type: ignore[index]
            start = min(a for a, _ in ranges) if ranges != [WHOLE] else 0.0
            places.append(
                {
                    "lecture_id": lid,
                    "number": numbers.get(lid),
                    "title": lectures[lid].title,
                    "start_s": max(0.0, start + SLACK_S) if ranges != [WHOLE] else 0.0,
                    "sections": p.get("sections") or [],
                }
            )
        recall = [retrievability(sch, c, now) for c in cards.values()]
        got = [scores[q] for q in questions if q in scores]
        out.append(
            {
                "name": t.get("name", ""),
                "summary": t.get("summary", ""),
                "exam_relevant": bool(t.get("exam_relevant")),
                "places": places,
                "cards": len(recall),
                "new": sum(1 for c in cards.values() if c.due is None),
                "due": sum(1 for c in cards.values() if c.due is not None and c.due <= now),
                "mastery": round(sum(recall) / len(recall), 3) if recall else None,
                "questions": len(questions),
                "practice": round(sum(a for a, _ in got) / sum(b for _, b in got), 3)
                if got and sum(b for _, b in got)
                else None,
                "card_ids": sorted(cards),
                "question_ids": sorted(questions),
            }
        )
    return out


def concept_map(hub: Note) -> dict[str, Any]:
    ids = {int(k): v for k, v in (hub.data.get("lecture_ids") or {}).items()}
    nodes = [
        {**n, "lecture_id": ids.get(int(n.get("lecture") or 0))}
        for n in hub.data.get("concepts") or []
    ]
    known = {n["id"] for n in nodes}
    edges = [
        e
        for e in hub.data.get("relations") or []
        if e.get("source") in known and e.get("target") in known and e["source"] != e["target"]
    ]
    return {"nodes": nodes, "edges": edges}
