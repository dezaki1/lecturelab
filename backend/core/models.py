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
    raw_text: str | None = None  # Whisper's text before the cleanup pass changed it


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
    score: float | None = None  # PDF pages: how well the page matched the lecture (0-1)


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


class NoteKind(StrEnum):
    summary = "summary"
    notes = "notes"
    glossary = "glossary"
    announcements = "announcements"
    # Phase 2: cheat_sheet, concept_map, subject-level running summary


class Note(SQLModel, table=True):
    """A versioned Markdown document for a lecture (or, later, a subject).

    Each generation or first manual edit creates a new version; exactly one version per
    (lecture, kind) is current. Old versions are kept so edits are never silently lost.
    """

    id: int | None = Field(default=None, primary_key=True)
    subject_id: int = Field(foreign_key="subject.id", index=True)
    lecture_id: int | None = Field(
        default=None, foreign_key="lecture.id", index=True, ondelete="CASCADE"
    )
    kind: str = Field(index=True)
    version: int = 1
    is_current: bool = True
    content_md: str = ""
    # Structured form as generated (sections, glossary terms...), used by the UI and exports.
    data: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    source_refs: list[dict[str, Any]] = Field(
        default_factory=list, sa_column=Column(JSON, nullable=False)
    )
    edited_by_me: bool = False
    model: str = ""
    prompt_version: str = ""
    instructions: str = ""  # extra instructions given when regenerating
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class Flashcard(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    subject_id: int = Field(foreign_key="subject.id", index=True)
    lecture_id: int | None = Field(
        default=None, foreign_key="lecture.id", index=True, ondelete="CASCADE"
    )
    type: str = "basic"  # basic | cloze | reversed
    front: str
    back: str = ""
    tags: list[str] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    difficulty: str = "medium"
    source_refs: list[dict[str, Any]] = Field(
        default_factory=list, sa_column=Column(JSON, nullable=False)
    )
    generation: int = 1  # which generation run of this lecture created it
    is_current: bool = True
    verified: str = "unchecked"  # unchecked | ok | flagged
    verify_note: str = ""
    edited_by_me: bool = False
    suspended: bool = False
    # FSRS scheduling state (M7)
    fsrs: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    due: datetime | None = Field(default=None, index=True)
    model: str = ""
    created_at: datetime = Field(default_factory=utcnow)


class Question(SQLModel, table=True):
    """Exam question or practice exercise (``kind``)."""

    id: int | None = Field(default=None, primary_key=True)
    subject_id: int = Field(foreign_key="subject.id", index=True)
    lecture_id: int | None = Field(
        default=None, foreign_key="lecture.id", index=True, ondelete="CASCADE"
    )
    kind: str = Field(default="exam", index=True)  # exam | exercise
    type: str = "short"  # mcq | multi | tf | short | long | calculation | code | (exercise kinds)
    title: str = ""
    bloom: str = ""
    difficulty: str = "medium"
    points: float = 1.0
    prompt: str
    options: list[dict[str, Any]] = Field(
        default_factory=list, sa_column=Column(JSON, nullable=False)
    )
    answer: str = ""
    explanation: str = ""
    rubric: list[dict[str, Any]] = Field(
        default_factory=list, sa_column=Column(JSON, nullable=False)
    )
    hints: list[str] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    solution_steps: list[str] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    expected_output: str = ""
    emphasized: bool = False
    source_refs: list[dict[str, Any]] = Field(
        default_factory=list, sa_column=Column(JSON, nullable=False)
    )
    generation: int = 1
    is_current: bool = True
    verified: str = "unchecked"
    verify_note: str = ""
    edited_by_me: bool = False
    model: str = ""
    created_at: datetime = Field(default_factory=utcnow)


class UsageRecord(SQLModel, table=True):
    __tablename__ = "usage_record"

    id: int | None = Field(default=None, primary_key=True)
    lecture_id: int | None = Field(
        default=None, foreign_key="lecture.id", index=True, ondelete="SET NULL"
    )
    subject_id: int | None = Field(default=None, foreign_key="subject.id", index=True)
    task: str = Field(index=True)
    provider: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cost_usd: float = 0.0
    created_at: datetime = Field(default_factory=utcnow, index=True)


class Chunk(SQLModel, table=True):
    """A searchable piece of a lecture: ~60-90 s of transcript or one section of the notes.

    Mirrored into ``chunk_fts`` (FTS5, keyword search) and ``chunk_vec`` (sqlite-vec, semantic
    search) with the same rowid. Phase 2 adds slide/material chunks (``material_id``/``page``).
    """

    id: int | None = Field(default=None, primary_key=True)
    subject_id: int = Field(foreign_key="subject.id", index=True)
    lecture_id: int | None = Field(
        default=None, foreign_key="lecture.id", index=True, ondelete="CASCADE"
    )
    material_id: int | None = Field(default=None, foreign_key="material.id", ondelete="CASCADE")
    page: int | None = None
    source: str = Field(index=True)  # transcript | note | (phase 2) slide | material
    note_kind: str = ""
    heading: str = ""
    text: str
    start_s: float | None = None
    end_s: float | None = None
    embedded: bool = False
    created_at: datetime = Field(default_factory=utcnow)


class EmbeddingCache(SQLModel, table=True):
    __tablename__ = "embedding_cache"

    key: str = Field(primary_key=True)  # sha256(model + text)
    model: str
    dim: int
    vector: bytes


class ChatThread(SQLModel, table=True):
    __tablename__ = "chat_thread"

    id: int | None = Field(default=None, primary_key=True)
    scope: str = "subject"  # lecture | subject | all
    subject_id: int | None = Field(
        default=None, foreign_key="subject.id", index=True, ondelete="CASCADE"
    )
    lecture_id: int | None = Field(
        default=None, foreign_key="lecture.id", index=True, ondelete="CASCADE"
    )
    title: str = "New chat"
    grounded: bool = True
    date_from: date | None = None
    date_to: date | None = None
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow, index=True)


class ChatMessage(SQLModel, table=True):
    __tablename__ = "chat_message"

    id: int | None = Field(default=None, primary_key=True)
    thread_id: int = Field(foreign_key="chat_thread.id", index=True, ondelete="CASCADE")
    role: str  # user | assistant
    content: str
    # [{"label": "L5 · 34:12", "lecture_id": 7, "start_s": 2052.0}, ...]
    citations: list[dict[str, Any]] = Field(
        default_factory=list, sa_column=Column(JSON, nullable=False)
    )
    model: str = ""
    cost_usd: float = 0.0
    error: str | None = None
    created_at: datetime = Field(default_factory=utcnow)


class ReviewLog(SQLModel, table=True):
    """One flashcard review. ``card_before`` holds the FSRS state before it, for undo."""

    __tablename__ = "review_log"

    id: int | None = Field(default=None, primary_key=True)
    card_id: int = Field(foreign_key="flashcard.id", index=True, ondelete="CASCADE")
    rating: int  # 1 again, 2 hard, 3 good, 4 easy
    reviewed_at: datetime = Field(default_factory=utcnow, index=True)
    duration_ms: int | None = None
    card_before: dict[str, Any] = Field(
        default_factory=dict, sa_column=Column(JSON, nullable=False)
    )
    due_before: datetime | None = None
    state_after: int | None = None
    stability_after: float | None = None


class Attempt(SQLModel, table=True):
    """One answer to a practice/exam question or exercise."""

    id: int | None = Field(default=None, primary_key=True)
    question_id: int = Field(foreign_key="question.id", index=True, ondelete="CASCADE")
    selected: list[int] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    answer_text: str = ""
    rubric_checked: list[int] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    auto_graded: bool = False
    correct: bool | None = None
    score: float = 0.0
    max_points: float = 0.0
    duration_ms: int | None = None
    created_at: datetime = Field(default_factory=utcnow, index=True)
