from __future__ import annotations

import asyncio
import json
from pathlib import Path

from sqlalchemy import inspect
from sqlmodel import select

from backend.core import jobs
from backend.core.config import Settings, load_settings
from backend.core.db import get_engine, init_db, session_scope
from backend.core.events import EventBus
from backend.core.models import Job, JobStatus, Subject
from backend.core.refs import SourceRefs
from backend.core.util import format_hms, slugify
from backend.seed import seed_subjects


def test_config_precedence(tmp_path: Path, monkeypatch) -> None:
    data = tmp_path / "d"
    data.mkdir()
    (data / "config.toml").write_text('port = 9001\n[recorder]\naac_bitrate = "96k"\n')
    s = load_settings(data)
    assert s.port == 9001 and s.recorder.aac_bitrate == "96k"
    monkeypatch.setenv("LECTURELAB_PORT", "9002")
    assert load_settings(data).port == 9002
    assert load_settings(data, port=9003).port == 9003


def test_config_template_written(settings: Settings) -> None:
    assert (settings.data_dir / "config.toml").exists()
    assert settings.subjects_dir.is_dir()


def test_migrations_create_tables(settings: Settings) -> None:
    init_db(settings.db_path)
    tables = set(inspect(get_engine()).get_table_names())
    assert {"subject", "lecture", "bookmark", "job", "slide", "material"} <= tables
    with get_engine().connect() as c:
        assert c.exec_driver_sql("PRAGMA journal_mode").scalar() == "wal"
    init_db(settings.db_path)  # idempotent


def test_job_queue_runs_and_records_failures(settings: Settings) -> None:
    init_db(settings.db_path)
    seen: list[dict] = []

    @jobs.handler("test.ok")
    def ok(ctx: jobs.JobContext) -> None:
        ctx.progress(0.5, "half")
        seen.append(ctx.payload)

    @jobs.handler("test.fail")
    def fail(ctx: jobs.JobContext) -> None:
        raise jobs.JobError("nope")

    with session_scope() as s:
        a = jobs.enqueue(s, "test.ok", payload={"x": 1})
        b = jobs.enqueue(s, "test.fail")
        c = jobs.enqueue(s, "test.unknown")
    assert jobs.Worker().run_pending() == 3
    with session_scope() as s:
        assert s.get(Job, a.id).status == JobStatus.done
        assert s.get(Job, b.id).error == "nope"
        assert "No handler" in s.get(Job, c.id).error
    assert seen == [{"x": 1}]


def test_interrupted_jobs_are_requeued(settings: Settings) -> None:
    init_db(settings.db_path)
    with session_scope() as s:
        s.add(Job(kind="x", status=JobStatus.running))
        s.commit()
    assert jobs.Worker.requeue_interrupted() == 1
    with session_scope() as s:
        assert s.exec(select(Job)).one().status == JobStatus.queued


def test_event_bus_fanout_from_thread() -> None:
    bus = EventBus()

    async def run() -> list[str]:
        async with bus.subscribe() as q1, bus.subscribe() as q2:
            await asyncio.to_thread(bus.publish, "ping", {"n": 1})
            return [(await q1.get()).type, (await q2.get()).type]

    assert asyncio.run(run()) == ["ping", "ping"]


def test_seed_is_idempotent(settings: Settings, tmp_path: Path) -> None:
    init_db(settings.db_path)
    f = tmp_path / "s.json"
    f.write_text(json.dumps([{"name": "NLP"}, {"name": "Formal Models in AI"}]))
    assert seed_subjects(f) == (2, 0)
    assert seed_subjects(f) == (0, 2)
    with session_scope() as s:
        assert len(s.exec(select(Subject)).all()) == 2


def test_repo_seed_file_is_valid(settings: Settings) -> None:
    init_db(settings.db_path)
    created, _ = seed_subjects(Path(__file__).parent.parent / "scripts" / "subjects_ws2026.json")
    assert created == 12


def test_source_refs_roundtrip() -> None:
    refs = SourceRefs.validate_python(
        [
            {"type": "lecture", "lecture_id": 1, "start_s": 1.5, "end_s": 9},
            {"type": "material", "material_id": 2, "page": 3},
        ]
    )
    assert refs[0].lecture_id == 1 and refs[1].page == 3


def test_util() -> None:
    assert slugify("Mathematik für KI III") == "mathematik-fuer-ki-iii"
    assert slugify("!!!") == "untitled"
    assert format_hms(754) == "12:34" and format_hms(3725) == "1:02:05"
