"""Evals: hand-checked search questions, runs and their history."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlmodel import Session, col, select

from backend.core.db import get_session
from backend.core.models import EvalCase, EvalRun, Lecture, TranscriptSegment
from backend.core.util import format_hms
from backend.evals import runner

router = APIRouter(prefix="/api/evals", tags=["evals"])

RUN_FIELDS = ("id", "kind", "status", "error", "metrics", "config", "cost_usd", "created_at")


class MakeRequest(BaseModel):
    n: int = Field(default=20, ge=1, le=50)
    subject_id: int | None = None


class CasePatch(BaseModel):
    status: Literal["candidate", "approved", "rejected"] | None = None
    question: str | None = Field(default=None, min_length=3, max_length=300)


class RunRequest(BaseModel):
    kind: Literal["retrieval", "grounding"]


def _case(s: Session, c: EvalCase, lectures: dict[int, Lecture]) -> dict[str, Any]:
    segs = s.exec(
        select(TranscriptSegment)
        .where(
            TranscriptSegment.lecture_id == c.lecture_id,
            TranscriptSegment.end >= c.gold_start_s,
            TranscriptSegment.start <= c.gold_end_s,
        )
        .order_by(col(TranscriptSegment.idx))
        .limit(6)
    ).all()
    lec = lectures.get(c.lecture_id)
    return {
        **c.model_dump(),
        "lecture_title": lec.title if lec else "",
        "gold": f"{format_hms(c.gold_start_s)}-{format_hms(c.gold_end_s)}",
        "passage": " ".join(x.text for x in segs)[:600],
    }


@router.get("")
def overview(session: Session = Depends(get_session)) -> dict[str, Any]:
    cases = session.exec(select(EvalCase).order_by(col(EvalCase.id))).all()
    lectures = {x.id: x for x in session.exec(select(Lecture)).all()}
    runs = session.exec(select(EvalRun).order_by(col(EvalRun.id).desc()).limit(30)).all()
    return {
        "cases": [_case(session, c, lectures) for c in cases if c.status != "rejected"],
        "counts": {
            st: sum(1 for c in cases if c.status == st)
            for st in ("candidate", "approved", "rejected")
        },
        "runs": [{k: getattr(r, k) for k in RUN_FIELDS} for r in runs],
    }


@router.post("/cases/make")
def make(req: MakeRequest, session: Session = Depends(get_session)) -> dict[str, int]:
    return {"made": runner.make_candidates(session, req.n, req.subject_id)}


@router.patch("/cases/{case_id}")
def patch_case(
    case_id: int, req: CasePatch, session: Session = Depends(get_session)
) -> dict[str, Any]:
    c = session.get(EvalCase, case_id)
    if c is None:
        raise HTTPException(404, "Eval case not found")
    for k, v in req.model_dump(exclude_none=True).items():
        setattr(c, k, v.strip() if isinstance(v, str) else v)
    session.add(c)
    session.commit()
    return {"id": c.id, "status": c.status, "question": c.question}


@router.post("/runs", status_code=202)
def run(req: RunRequest, session: Session = Depends(get_session)) -> dict[str, Any]:
    try:
        r = runner.queue_run(session, req.kind)
    except runner.EvalError as e:
        raise HTTPException(422, str(e)) from e
    return {"id": r.id}


@router.get("/runs/{run_id}")
def get_run(run_id: int, session: Session = Depends(get_session)) -> dict[str, Any]:
    r = session.get(EvalRun, run_id)
    if r is None:
        raise HTTPException(404, "Run not found")
    return r.model_dump()
