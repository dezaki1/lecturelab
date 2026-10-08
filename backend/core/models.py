"""Database tables.

Phase 1 adds tables milestone by milestone through Alembic migrations. ``Slide`` and ``Material``
exist already so Phase 2 (slide capture, PDF import) slots in without reshaping ``Lecture``.
Generated content (notes, cards, questions) will carry ``source_refs`` (see ``core.refs``).
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel


def utcnow() -> datetime:
    return datetime.now(UTC)


class LectureSource(StrEnum):
    recording = "recording"
    import_ = "import"


class LectureStatus(StrEnum):
    recording = "recording"
    processing = "processing"  # encoding / importing / pipeline stages running
    ready = "ready"
    failed = "failed"


class StageStatus(StrEnum):
    pending = "pending"
    running = "running"
    done = "done"
    failed = "failed"
    skipped = "skipped"


# Order matters: the pipeline runs these left to right. Phase 2 inserts "slides"/"cleanup".
PIPELINE_STAGES: tuple[str, ...] = ("capture", "transcribe", "generate", "index")


class BookmarkKind(StrEnum):
    important = "important"
    confused = "confused"
    question = "question"


class JobStatus(StrEnum):
    queued = "queued"
    running = "running"
    done = "done"
    failed = "failed"
    cancelled = "cancelled"


class Subject(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(index=True, unique=True)
    slug: str = Field(unique=True)  # folder name under data/subjects; stable after creation
    course_code: str = ""
    # Spoken language: "auto" lets Whisper detect it; "en"/"de" force it.
    language: str = "auto"
    # Language for generated study material (§13: always English unless overridden).
    output_language: str = "en"
    lecturer: str = ""
    color: str = "#6366f1"
    exam_date: date | None = None
    instructions: str = ""
    vocabulary: list[str] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    recording_notice_ack: bool = False
    archived: bool = False
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class Lecture(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    subject_id: int = Field(foreign_key="subject.id", index=True)
    title: str
    # Session type at JKU: VL/VO (lecture), UE (exercise), or empty.
    kind: str = ""
    started_at: datetime = Field(default_factory=utcnow)  # UTC; UI shows local time
    duration_s: float | None = None
    source: LectureSource = LectureSource.recording
    # Paths are relative to settings.data_dir so the data dir can be moved.
    folder: str
    media_path: str | None = None
    status: LectureStatus = LectureStatus.processing
    stages: dict[str, str] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    error: str | None = None
    # Detected (or forced) spoken language, e.g. "en", once transcribed.
    language: str | None = None
    # Free-form per-lecture metadata, e.g. {"transcription": {"model", "device", "speed_x", ...}}
    meta: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    cost_usd: float = 0.0
    created_at: datetime = Field(default_factory=utcnow)


class TranscriptSegment(SQLModel, table=True):
    """One Whisper segment. Word timings live in the lecture folder's transcript.json."""

    __tablename__ = "transcript_segment"

    id: int | None = Field(default=None, primary_key=True)
    lecture_id: int = Field(foreign_key="lecture.id", index=True, ondelete="CASCADE")
    idx: int
    start: float
    end: float
    text: str
    speaker: str | None = None
    confidence: float | None = None


class Bookmark(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    lecture_id: int = Field(foreign_key="lecture.id", index=True, ondelete="CASCADE")
    t_s: float
    kind: BookmarkKind = BookmarkKind.important
    note: str = ""
    created_at: datetime = Field(default_factory=utcnow)


class Material(SQLModel, table=True):
    """Uploaded files: slide PDFs, past exams, handwritten notes (Phase 2)."""

    id: int | None = Field(default=None, primary_key=True)
    subject_id: int = Field(foreign_key="subject.id", index=True)
    lecture_id: int | None = Field(
        default=None, foreign_key="lecture.id", index=True, ondelete="SET NULL"
    )
    kind: str = "slides"  # slides | past_exam | notes | other
    title: str = ""
    file_path: str  # relative to data_dir
    extracted_text: str = ""
    created_at: datetime = Field(default_factory=utcnow)


class Slide(SQLModel, table=True):
    """A distinct slide seen in a lecture, from screen capture or a matched PDF page (Phase 2)."""

    id: int | None = Field(default=None, primary_key=True)
    lecture_id: int = Field(foreign_key="lecture.id", index=True, ondelete="CASCADE")
    t_s: float
    image_path: str  # relative to data_dir
    ocr_text: str = ""
    source: str = "screen"  # screen | pdf
    material_id: int | None = Field(default=None, foreign_key="material.id")
    page: int | None = None
    phash: str = ""
    window_title: str = ""


class Job(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    kind: str = Field(index=True)
    lecture_id: int | None = Field(
        default=None, foreign_key="lecture.id", index=True, ondelete="SET NULL"
    )
    payload: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    status: JobStatus = Field(default=JobStatus.queued, index=True)
    progress: float = 0.0
    message: str = ""
    error: str | None = None
    attempts: int = 0
    created_at: datetime = Field(default_factory=utcnow)
    started_at: datetime | None = None
    finished_at: datetime | None = None
