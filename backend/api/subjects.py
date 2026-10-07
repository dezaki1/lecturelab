from __future__ import annotations

from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field, field_validator
from sqlmodel import Session, col, func, select

from backend.core.db import get_session
from backend.core.models import Lecture, Subject, utcnow
from backend.core.util import slugify

router = APIRouter(prefix="/api/subjects", tags=["subjects"])

LANGS = {"auto", "en", "de"}


def clean_vocabulary(terms: list[str]) -> list[str]:
    """Strip blanks and duplicates, keeping first-seen order."""
    return list(dict.fromkeys(t.strip() for t in terms if t.strip()))


class SubjectBase(BaseModel):
    course_code: str = ""
    language: str = "auto"
    output_language: str = "en"
    lecturer: str = ""
    color: str = "#6366f1"
    exam_date: date | None = None
    instructions: str = ""
    vocabulary: list[str] = Field(default_factory=list)

    @field_validator("language")
    @classmethod
    def _lang(cls, v: str) -> str:
        if v not in LANGS:
            raise ValueError(f"language must be one of {sorted(LANGS)}")
        return v

    @field_validator("vocabulary")
    @classmethod
    def _vocab(cls, v: list[str]) -> list[str]:
        return clean_vocabulary(v)


class SubjectCreate(SubjectBase):
    name: str = Field(min_length=1, max_length=200)


class SubjectUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    course_code: str | None = None
    language: str | None = None
    output_language: str | None = None
    lecturer: str | None = None
    color: str | None = None
    exam_date: date | None = None
    instructions: str | None = None
    vocabulary: list[str] | None = None
    recording_notice_ack: bool | None = None
    archived: bool | None = None

    @field_validator("language")
    @classmethod
    def _lang(cls, v: str | None) -> str | None:
        if v is not None and v not in LANGS:
            raise ValueError(f"language must be one of {sorted(LANGS)}")
        return v


class SubjectRead(SubjectBase):
    id: int
    name: str
    slug: str
    recording_notice_ack: bool
    archived: bool
    lecture_count: int = 0
    created_at: datetime
    updated_at: datetime


def _read(s: Subject, lecture_count: int = 0) -> SubjectRead:
    return SubjectRead.model_validate({**s.model_dump(), "lecture_count": lecture_count})


def _unique_slug(session: Session, name: str) -> str:
    base = slugify(name)
    slug, n = base, 2
    while session.exec(select(Subject).where(Subject.slug == slug)).first():
        slug, n = f"{base}-{n}", n + 1
    return slug


def create_subject(session: Session, data: SubjectCreate) -> Subject:
    name = data.name.strip()
    if session.exec(select(Subject).where(func.lower(Subject.name) == name.lower())).first():
        raise HTTPException(409, f"A subject named {name!r} already exists")
    subject = Subject(**data.model_dump(exclude={"name"}), name=name)
    subject.slug = _unique_slug(session, name)
    session.add(subject)
    session.commit()
    session.refresh(subject)
    return subject


def _counts(session: Session) -> dict[int, int]:
    rows = session.exec(
        select(Lecture.subject_id, func.count(col(Lecture.id))).group_by(Lecture.subject_id)
    ).all()
    return dict(rows)


@router.get("", response_model=list[SubjectRead])
def list_subjects(session: Session = Depends(get_session)) -> list[SubjectRead]:
    counts = _counts(session)
    subjects = session.exec(select(Subject).order_by(func.lower(Subject.name))).all()
    return [_read(s, counts.get(s.id, 0)) for s in subjects]  # type: ignore[arg-type]


@router.post("", response_model=SubjectRead, status_code=201)
def post_subject(data: SubjectCreate, session: Session = Depends(get_session)) -> SubjectRead:
    return _read(create_subject(session, data))


def _get(session: Session, subject_id: int) -> Subject:
    subject = session.get(Subject, subject_id)
    if subject is None:
        raise HTTPException(404, "Subject not found")
    return subject


@router.get("/{subject_id}", response_model=SubjectRead)
def get_subject(subject_id: int, session: Session = Depends(get_session)) -> SubjectRead:
    return _read(_get(session, subject_id), _counts(session).get(subject_id, 0))


@router.patch("/{subject_id}", response_model=SubjectRead)
def patch_subject(
    subject_id: int, data: SubjectUpdate, session: Session = Depends(get_session)
) -> SubjectRead:
    subject = _get(session, subject_id)
    changes = data.model_dump(exclude_unset=True)
    if "name" in changes:
        name = changes["name"].strip()
        clash = session.exec(
            select(Subject).where(
                func.lower(Subject.name) == name.lower(), Subject.id != subject_id
            )
        ).first()
        if clash:
            raise HTTPException(409, f"A subject named {name!r} already exists")
        changes["name"] = name  # slug (= folder name) intentionally stays stable
    if "vocabulary" in changes:
        changes["vocabulary"] = clean_vocabulary(changes["vocabulary"])
    for key, value in changes.items():
        setattr(subject, key, value)
    subject.updated_at = utcnow()
    session.add(subject)
    session.commit()
    session.refresh(subject)
    return _read(subject, _counts(session).get(subject_id, 0))


@router.delete("/{subject_id}", status_code=204)
def delete_subject(subject_id: int, session: Session = Depends(get_session)) -> Response:
    subject = _get(session, subject_id)
    n = _counts(session).get(subject_id, 0)
    if n:
        raise HTTPException(
            409, f"{subject.name} still has {n} lecture(s). Delete or move them first."
        )
    session.delete(subject)
    session.commit()
    return Response(status_code=204)
