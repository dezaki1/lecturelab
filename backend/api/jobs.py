"""Jobs (list, retry, cancel), usage/cost reporting and batch generation."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, col, func, select

from backend.ai.providers import is_configured
from backend.ai.types import PRICING
from backend.core import jobs
from backend.core.db import get_session
from backend.core.events import bus
from backend.core.models import (
    Job,
    JobStatus,
    Lecture,
    LectureStatus,
    Subject,
    TranscriptSegment,
    UsageRecord,
    utcnow,
)
from backend.pipeline.generate import ALL_KINDS, estimate
from backend.pipeline.stages import GENERATE_JOB
from backend.search.retrieve import lecture_numbers

router = APIRouter(prefix="/api", tags=["jobs"])

ACTIVE = [JobStatus.queued, JobStatus.running]


def _job_read(
    job: Job, lectures: dict[int, Lecture], subjects: dict[int, Subject]
) -> dict[str, Any]:
    lec = lectures.get(job.lecture_id) if job.lecture_id else None
    sub = subjects.get(lec.subject_id) if lec else subjects.get(job.payload.get("subject_id", 0))
    end = job.finished_at or (utcnow() if job.started_at else None)
    return {
        **job.model_dump(exclude={"payload"}),
        "label": jobs.LABELS.get(job.kind, job.kind),
        "detail": ", ".join(job.payload.get("kinds", [])) if job.kind == "generate" else "",
        "lecture_title": lec.title if lec else None,
        "subject_name": sub.name if sub else None,
        "subject_color": sub.color if sub else None,
        "duration_s": (end - job.started_at).total_seconds() if job.started_at and end else None,
    }


@router.get("/jobs")
def list_jobs(
    status: str = "all", limit: int = 100, session: Session = Depends(get_session)
) -> list[dict[str, Any]]:
    q = select(Job).order_by(col(Job.id).desc()).limit(min(limit, 500))
    if status == "active":
        q = q.where(col(Job.status).in_(ACTIVE))
    elif status == "failed":
        q = q.where(Job.status == JobStatus.failed)
    rows = session.exec(q).all()
    lectures = {
        lec.id: lec
        for lec in session.exec(
            select(Lecture).where(col(Lecture.id).in_({j.lecture_id for j in rows if j.lecture_id}))
        ).all()
    }
    subjects = {s.id: s for s in session.exec(select(Subject)).all()}
    return [_job_read(j, lectures, subjects) for j in rows]  # type: ignore[arg-type]


@router.get("/jobs/counts")
def job_counts(session: Session = Depends(get_session)) -> dict[str, int]:
    rows = dict(session.exec(select(Job.status, func.count()).group_by(Job.status)).all())
    since = utcnow() - timedelta(days=7)
    recent_failed = session.exec(
        select(func.count())
        .select_from(Job)
        .where(Job.status == JobStatus.failed, Job.created_at >= since)
    ).one()
    return {
        "queued": rows.get(JobStatus.queued, 0),
        "running": rows.get(JobStatus.running, 0),
        "failed_recent": recent_failed,
    }


@router.post("/jobs/{job_id}/retry")
def retry_job(job_id: int, session: Session = Depends(get_session)) -> dict[str, Any]:
    job = session.get(Job, job_id)
    if job is None:
        raise HTTPException(404, "Job not found")
    if job.status not in (JobStatus.failed, JobStatus.cancelled):
        raise HTTPException(409, "Only failed or cancelled jobs can be retried")
    if job.lecture_id:
        busy = session.exec(
            select(Job).where(
                Job.lecture_id == job.lecture_id, col(Job.status).in_(ACTIVE), Job.kind != "index"
            )
        ).first()
        if busy:
            raise HTTPException(409, "This lecture is already being processed")
        lecture = session.get(Lecture, job.lecture_id)
        if lecture is not None:
            lecture.status = LectureStatus.processing
            lecture.error = None
            session.add(lecture)
    job.status = JobStatus.queued
    job.error = None
    job.progress = 0.0
    job.message = "Retrying"
    job.started_at = job.finished_at = None
    session.add(job)
    session.commit()
    if jobs.worker is not None:
        jobs.worker.wake()
    bus.publish("job", {"id": job.id, "status": "queued", "lecture_id": job.lecture_id})
    return {"id": job.id, "status": "queued"}


@router.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: int, session: Session = Depends(get_session)) -> dict[str, Any]:
    job = session.get(Job, job_id)
    if job is None:
        raise HTTPException(404, "Job not found")
    if job.status != JobStatus.queued:
        raise HTTPException(409, "Only waiting jobs can be cancelled")
    job.status = JobStatus.cancelled
    job.finished_at = utcnow()
    session.add(job)
    if job.lecture_id:
        lecture = session.get(Lecture, job.lecture_id)
        if lecture is not None and lecture.status == LectureStatus.processing:
            lecture.status = LectureStatus.ready if lecture.media_path else LectureStatus.failed
            stage = {"transcribe": "transcribe", "generate": "generate"}.get(job.kind)
            if stage and lecture.stages.get(stage) == "pending":
                lecture.stages = {k: v for k, v in lecture.stages.items() if k != stage}
            session.add(lecture)
    session.commit()
    bus.publish("job", {"id": job.id, "status": "cancelled", "lecture_id": job.lecture_id})
    return {"id": job.id, "status": "cancelled"}


# Usage -----------------------------------------------------------------------------------------
@router.get("/usage")
def usage_summary(days: int = 30, session: Session = Depends(get_session)) -> dict[str, Any]:
    since = utcnow() - timedelta(days=days)
    rows = session.exec(select(UsageRecord).where(UsageRecord.created_at >= since)).all()
    all_time = session.exec(select(func.sum(UsageRecord.cost_usd))).one() or 0.0

    def bucket() -> dict[str, float]:
        return {"cost": 0.0, "input": 0, "output": 0, "cache_read": 0, "cache_write": 0, "calls": 0}

    by_task: dict[str, dict] = defaultdict(bucket)
    by_model: dict[str, dict] = defaultdict(bucket)
    by_day: dict[str, float] = defaultdict(float)
    by_lecture: dict[int, dict] = defaultdict(bucket)
    by_subject: dict[int, float] = defaultdict(float)
    saved = 0.0
    for r in rows:
        for b in (by_task[r.task], by_model[r.model]):
            b["cost"] += r.cost_usd
            b["input"] += r.input_tokens
            b["output"] += r.output_tokens
            b["cache_read"] += r.cache_read_tokens
            b["cache_write"] += r.cache_write_tokens
            b["calls"] += 1
        by_day[r.created_at.astimezone().date().isoformat()] += r.cost_usd
        if r.lecture_id:
            by_lecture[r.lecture_id]["cost"] += r.cost_usd
            by_lecture[r.lecture_id]["calls"] += 1
        if r.subject_id:
            by_subject[r.subject_id] += r.cost_usd
        price = PRICING.get(r.model)
        if price:  # what the cache reads would have cost as normal input
            saved += r.cache_read_tokens * (price[0] - price[3]) / 1e6

    lectures = {
        lec.id: lec
        for lec in session.exec(select(Lecture).where(col(Lecture.id).in_(list(by_lecture)))).all()
    }
    subjects = {s.id: s for s in session.exec(select(Subject)).all()}
    numbers = lecture_numbers(session, {lec.subject_id for lec in lectures.values()})
    today = datetime.now().astimezone().date()
    days_list = [(today - timedelta(days=i)).isoformat() for i in range(days - 1, -1, -1)]

    def rnd(d: dict) -> dict:
        return {k: round(v, 4) if isinstance(v, float) else v for k, v in d.items()}

    return {
        "days": days,
        "total_cost": round(sum(r.cost_usd for r in rows), 4),
        "all_time_cost": round(all_time, 4),
        "calls": len(rows),
        "cache_savings": round(saved, 4),
        "by_task": sorted(
            ({"task": k, **rnd(v)} for k, v in by_task.items()), key=lambda x: -x["cost"]
        ),
        "by_model": sorted(
            ({"model": k, **rnd(v)} for k, v in by_model.items()), key=lambda x: -x["cost"]
        ),
        "by_day": [{"day": d, "cost": round(by_day.get(d, 0.0), 4)} for d in days_list],
        "by_subject": sorted(
            (
                {
                    "subject_id": sid,
                    "name": subjects[sid].name if sid in subjects else "?",
                    "color": subjects[sid].color if sid in subjects else "#888",
                    "cost": round(c, 4),
                }
                for sid, c in by_subject.items()
            ),
            key=lambda x: -x["cost"],
        ),
        "by_lecture": sorted(
            (
                {
                    "lecture_id": lid,
                    "title": lectures[lid].title if lid in lectures else "(deleted)",
                    "number": numbers.get(lid),
                    "subject": subjects[lectures[lid].subject_id].name if lid in lectures else "",
                    "duration_s": lectures[lid].duration_s if lid in lectures else None,
                    **rnd(v),
                }
                for lid, v in by_lecture.items()
            ),
            key=lambda x: -x["cost"],
        )[:100],
    }


# Batch generation ------------------------------------------------------------------------------
def _pending_generation(session: Session) -> list[Lecture]:
    out = []
    with_speech = set(session.exec(select(TranscriptSegment.lecture_id).distinct()).all())
    for lec in session.exec(select(Lecture).order_by(col(Lecture.started_at))).all():
        if (
            lec.stages.get("transcribe") == "done"
            and lec.stages.get("generate") in (None, "failed")
            and lec.id in with_speech  # recordings without speech have nothing to study
        ):
            out.append(lec)
    return out


@router.get("/generate/pending")
def pending_generation(session: Session = Depends(get_session)) -> dict[str, Any]:
    lectures = _pending_generation(session)
    usd = sum(estimate(session, lec)["usd"] for lec in lectures)
    return {
        "count": len(lectures),
        "usd": round(usd, 2),
        "configured": is_configured(),
        "lectures": [{"id": lec.id, "title": lec.title} for lec in lectures][:50],
    }


@router.post("/generate/pending")
def generate_pending(session: Session = Depends(get_session)) -> dict[str, int]:
    if not is_configured():
        raise HTTPException(409, "Add an Anthropic API key on the Settings page first")
    queued = 0
    for lec in _pending_generation(session):
        busy = session.exec(
            select(Job).where(
                Job.lecture_id == lec.id, col(Job.status).in_(ACTIVE), Job.kind != "index"
            )
        ).first()
        if busy:
            continue
        lec.stages = {**lec.stages, "generate": "pending"}
        lec.status = LectureStatus.processing
        session.add(lec)
        session.commit()
        jobs.enqueue(session, GENERATE_JOB, lecture_id=lec.id, payload={"kinds": list(ALL_KINDS)})
        bus.publish("lecture", {"id": lec.id, "subject_id": lec.subject_id})
        queued += 1
    return {"queued": queued}
