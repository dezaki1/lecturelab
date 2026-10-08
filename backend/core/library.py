"""Lecture folders on disk: ``data/subjects/<subject>/<YYYY-MM-DD>_<slug>/``."""

from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path

from sqlmodel import Session

from backend.core.config import get_settings
from backend.core.models import Lecture, LectureSource, LectureStatus, Subject, utcnow
from backend.core.util import slugify


def abs_path(rel: str) -> Path:
    return get_settings().data_dir / rel


def rel_path(path: Path) -> str:
    return path.relative_to(get_settings().data_dir).as_posix()


def lecture_dir(lecture: Lecture) -> Path:
    return abs_path(lecture.folder)


def default_title(local_start: datetime) -> str:
    return f"{local_start:%a} {local_start.day} {local_start:%b %Y, %H:%M}"


def _unique_dir(parent: Path, name: str) -> Path:
    candidate, n = parent / name, 2
    while candidate.exists():
        candidate, n = parent / f"{name}-{n}", n + 1
    return candidate


def create_lecture(
    session: Session,
    subject: Subject,
    *,
    source: LectureSource,
    status: LectureStatus,
    title: str | None = None,
    kind: str = "",
    started_at: datetime | None = None,
    title_is_default: bool | None = None,
) -> Lecture:
    started_at = started_at or utcnow()
    local = started_at.astimezone()
    slug = slugify(title) if title else f"lecture-{local:%H%M}"
    folder = _unique_dir(get_settings().subjects_dir / subject.slug, f"{local:%Y-%m-%d}_{slug}")
    folder.mkdir(parents=True)
    lecture = Lecture(
        subject_id=subject.id,  # type: ignore[arg-type]
        title=title or default_title(local),
        kind=kind,
        started_at=started_at,
        source=source,
        status=status,
        folder=rel_path(folder),
        stages={"capture": "running"},
        # A default title gets replaced by the generated topic title (M5).
        meta={"title_is_default": title is None if title_is_default is None else title_is_default},
    )
    session.add(lecture)
    session.commit()
    session.refresh(lecture)
    return lecture


def move_lecture(lecture: Lecture, subject: Subject) -> None:
    """Move the lecture folder under another subject (caller commits)."""
    src = lecture_dir(lecture)
    dest = _unique_dir(get_settings().subjects_dir / subject.slug, src.name)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if src.exists():
        shutil.move(src, dest)
    if lecture.media_path:
        lecture.media_path = rel_path(dest / Path(lecture.media_path).name)
    lecture.folder = rel_path(dest)
    lecture.subject_id = subject.id  # type: ignore[assignment]


def trash_lecture_folder(lecture: Lecture) -> Path | None:
    """Move the folder to data/trash (recoverable) instead of deleting it."""
    src = lecture_dir(lecture)
    if not src.exists():
        return None
    stamp = utcnow().strftime("%Y%m%dT%H%M%S")
    dest = _unique_dir(get_settings().trash_dir, f"{stamp}_{src.parent.name}_{src.name}")
    shutil.move(src, dest)
    return dest
