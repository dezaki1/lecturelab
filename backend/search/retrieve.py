"""Hybrid retrieval: FTS5 keyword ranking + sqlite-vec semantic ranking, fused with RRF.

Reciprocal rank fusion (score = sum of 1 / (60 + rank)) needs no score calibration between
BM25 and cosine distance. If the embedding backend is down, results are keyword-only.
"""

from __future__ import annotations

import logging
import re
from dataclasses import asdict, dataclass
from datetime import date, datetime, time
from typing import Any

from sqlalchemy import text
from sqlmodel import Session, col, select

from backend.core.models import Chunk, Lecture, Subject
from backend.pipeline.study_docs import is_document, page_of
from backend.search.embeddings import EmbeddingError, embed, pack

log = logging.getLogger(__name__)

RRF_K = 60
CANDIDATES = 200


@dataclass
class Hit:
    chunk_id: int
    lecture_id: int | None
    subject_id: int
    subject_name: str
    lecture_number: int | None
    lecture_title: str
    started_at: str | None
    source: str
    note_kind: str
    heading: str
    text: str
    snippet: str
    start_s: float | None
    end_s: float | None
    score: float
    keyword_rank: int | None
    semantic_rank: int | None
    page: int | None = None  # a document lecture (PDF): the page instead of a time

    def label(self, with_subject: bool = False) -> str:
        """Citation label, e.g. "L5 · 34:12", "L7 · p. 3" for a PDF (or "NLP L5 · 34:12")."""
        from backend.core.util import format_hms

        prefix = f"{short_name(self.subject_name)} " if with_subject else ""
        if self.page is not None:
            ts = f" · p. {self.page}"
        else:
            ts = f" · {format_hms(self.start_s)}" if self.start_s is not None else ""
        return f"{prefix}L{self.lecture_number}{ts}"

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def short_name(name: str) -> str:
    """ "Natural Language Processing" -> "NLP", "Mathematics for AI III" -> "MfAI III"."""
    words = re.findall(r"[A-Za-z0-9]+", name)
    roman = [w for w in words if re.fullmatch(r"[IVX]+|\d+", w)]

    def abbrev(w: str) -> str:
        if w.isupper() and len(w) > 1:  # keep acronyms whole: "AI"
            return w
        return w[0].lower() if w.lower() in ("for", "and", "of", "in") else w[0].upper()

    initials = "".join(abbrev(w) for w in words if w not in roman)
    return (initials + (" " + " ".join(roman) if roman else "")).strip() or name


def fts_query(q: str) -> str | None:
    tokens = re.findall(r"\w+", q.lower())
    tokens = [t for t in tokens if len(t) > 1][:16]
    if not tokens:
        return None
    # OR of quoted tokens (recall), prefix match on the last one (search-as-you-type).
    parts = [f'"{t}"' for t in tokens[:-1]] + [f'"{tokens[-1]}"*']
    return " OR ".join(parts)


def lecture_numbers(s: Session, subject_ids: set[int]) -> dict[int, int]:
    out: dict[int, int] = {}
    for sid in subject_ids:
        ids = s.exec(
            select(Lecture.id)
            .where(Lecture.subject_id == sid)
            .order_by(col(Lecture.started_at), col(Lecture.id))
        ).all()
        out.update({lid: i + 1 for i, lid in enumerate(ids)})  # type: ignore[misc]
    return out


def search(
    s: Session,
    query: str,
    *,
    lecture_id: int | None = None,
    subject_id: int | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    limit: int = 10,
    semantic: bool = True,
) -> list[Hit]:
    # Which chunks are in scope.
    q = select(Chunk.id).join(Lecture, col(Lecture.id) == col(Chunk.lecture_id))
    if lecture_id is not None:
        q = q.where(Chunk.lecture_id == lecture_id)
    if subject_id is not None:
        q = q.where(Chunk.subject_id == subject_id)
    if date_from is not None:
        q = q.where(Lecture.started_at >= datetime.combine(date_from, time.min).astimezone())
    if date_to is not None:
        q = q.where(Lecture.started_at <= datetime.combine(date_to, time.max).astimezone())
    allowed = set(s.exec(q).all())
    if not allowed:
        return []

    conn = s.connection()
    keyword: list[int] = []
    snippets: dict[int, str] = {}
    match = fts_query(query)
    if match:
        rows = conn.execute(
            text(
                "SELECT rowid, snippet(chunk_fts, 1, '<<', '>>', '…', 28) FROM chunk_fts "
                "WHERE chunk_fts MATCH :q ORDER BY bm25(chunk_fts) LIMIT :n"
            ),
            {"q": match, "n": CANDIDATES},
        ).all()
        for rowid, snip in rows:
            if rowid in allowed:
                keyword.append(rowid)
                snippets[rowid] = snip

    semantic_ids: list[int] = []
    if semantic and query.strip():
        try:
            [vec] = embed([query])
            rows = conn.execute(
                text(
                    "SELECT chunk_id, distance FROM chunk_vec WHERE embedding MATCH :v "
                    "AND k = :k ORDER BY distance"
                ),
                {"v": pack(vec), "k": min(CANDIDATES * 4, 4096)},
            ).all()
            semantic_ids = [cid for cid, _ in rows if cid in allowed][:CANDIDATES]
        except EmbeddingError as e:
            log.info("Semantic search unavailable, keyword only: %s", e)

    scores: dict[int, float] = {}
    for ranks in (keyword, semantic_ids):
        for r, cid in enumerate(ranks):
            scores[cid] = scores.get(cid, 0.0) + 1.0 / (RRF_K + r + 1)
    top = sorted(scores, key=lambda c: -scores[c])[:limit]
    if not top:
        return []

    chunks = {c.id: c for c in s.exec(select(Chunk).where(col(Chunk.id).in_(top))).all()}
    lectures = {
        lec.id: lec
        for lec in s.exec(
            select(Lecture).where(col(Lecture.id).in_({c.lecture_id for c in chunks.values()}))
        ).all()
    }
    subjects = {
        sub.id: sub
        for sub in s.exec(
            select(Subject).where(col(Subject.id).in_({c.subject_id for c in chunks.values()}))
        ).all()
    }
    numbers = lecture_numbers(s, set(subjects))
    k_rank = {cid: i + 1 for i, cid in enumerate(keyword)}
    v_rank = {cid: i + 1 for i, cid in enumerate(semantic_ids)}
    hits = []
    for cid in top:
        c = chunks[cid]
        lec = lectures.get(c.lecture_id)
        hits.append(
            Hit(
                chunk_id=cid,
                lecture_id=c.lecture_id,
                subject_id=c.subject_id,
                subject_name=subjects[c.subject_id].name,
                lecture_number=numbers.get(c.lecture_id),  # type: ignore[arg-type]
                lecture_title=lec.title if lec else "",
                started_at=lec.started_at.isoformat() if lec else None,
                source=c.source,
                note_kind=c.note_kind,
                heading=c.heading,
                text=c.text,
                snippet=snippets.get(cid) or (c.text[:240] + ("…" if len(c.text) > 240 else "")),
                start_s=c.start_s,
                end_s=c.end_s,
                score=round(scores[cid], 5),
                keyword_rank=k_rank.get(cid),
                semantic_rank=v_rank.get(cid),
                page=page_of(c.start_s) if is_document(lec) and c.start_s is not None else None,
            )
        )
    return hits
