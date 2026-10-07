"""Import subjects from a JSON file (list of objects with SubjectCreate fields). Idempotent."""

from __future__ import annotations

import json
from pathlib import Path

from sqlmodel import func, select

from backend.api.subjects import SubjectCreate, create_subject
from backend.core.db import session_scope
from backend.core.models import Subject


def seed_subjects(path: Path) -> tuple[int, int]:
    items = json.loads(path.read_text(encoding="utf-8"))
    created = skipped = 0
    with session_scope() as s:
        for raw in items:
            data = SubjectCreate.model_validate(raw)
            exists = s.exec(
                select(Subject).where(func.lower(Subject.name) == data.name.strip().lower())
            ).first()
            if exists:
                skipped += 1
                continue
            create_subject(s, data)
            created += 1
    return created, skipped
