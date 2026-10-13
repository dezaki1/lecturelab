"""FastAPI app factory. Serves the API under /api and the built frontend for everything else."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from backend.api import (
    cards,
    chat,
    documents,
    exams,
    exports,
    hub,
    imports,
    lectures,
    materials,
    recorder,
    slides,
    study,
    subjects,
    system,
    timetable,
    weak,
)
from backend.api import (
    jobs as jobs_api,
)
from backend.api import (
    planner as planner_api,
)
from backend.api import settings as settings_api
from backend.capture import encode  # noqa: F401  (registers the encode job handler)
from backend.capture.recorder import (
    RecorderService,
    SourceFactory,
    default_sources,
    recover_interrupted,
    set_recorder,
)
from backend.capture.slides import ScreenFactory, default_screen
from backend.core import jobs
from backend.core.backup import BackupScheduler
from backend.core.config import Settings, set_settings
from backend.core.db import init_db, session_scope
from backend.pipeline import cheatsheet as cheatsheet_jobs  # noqa: F401
from backend.pipeline import (  # noqa: F401  (job handlers)
    cleanup,
    generate,
    ingest,
    transcribe,
)
from backend.pipeline import documents as document_jobs  # noqa: F401
from backend.pipeline import slides as slide_jobs  # noqa: F401
from backend.pipeline import subject_hub as hub_jobs  # noqa: F401
from backend.pipeline.hints import backfill as hint_backfill
from backend.search import index as search_index
from backend.study import exams as exam_jobs  # noqa: F401
from backend.study import planner
from backend.study import weak as weak_jobs  # noqa: F401
from backend.study.deadlines import backfill as deadline_backfill
from backend.timetable.service import TimetableScheduler

log = logging.getLogger(__name__)

FRONTEND_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"


def create_app(
    settings: Settings,
    *,
    start_worker: bool = True,
    source_factory: SourceFactory = default_sources,
    screen_factory: ScreenFactory = default_screen,
) -> FastAPI:
    set_settings(settings)
    slides.apply_saved_settings(settings)
    settings_api.apply_saved_backup(settings)
    timetable.apply_saved_settings(settings)
    planner.apply_saved_settings(settings)
    init_db(settings.db_path)
    rec = RecorderService(settings, source_factory, screen_factory)
    set_recorder(rec)
    ingest.clean_tmp()
    with session_scope() as s:
        if n := search_index.backfill(s):
            log.info("Queued search indexing for %d lecture(s)", n)
        if n := hint_backfill(s):
            log.info("Scanned %d lecture(s) for exam hints", n)
        if n := deadline_backfill(s):
            log.info("Collected %d deadline suggestion(s) from announcements", n)
    if n := recover_interrupted():
        log.warning("Recovered %d interrupted recording(s)", n)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        schedulers = [BackupScheduler(), TimetableScheduler()] if start_worker else []
        if start_worker:
            jobs.start_worker()
        for sch in schedulers:
            sch.start()
        try:
            yield
        finally:
            rec.shutdown()  # finalises an in-progress recording; encoding resumes next start
            for sch in schedulers:
                sch.stop()
            jobs.stop_worker()

    app = FastAPI(title="LectureLab", version="0.1.0", lifespan=lifespan)
    app.include_router(system.router)
    app.include_router(subjects.router)
    app.include_router(lectures.router)
    app.include_router(recorder.router)
    app.include_router(imports.router)
    app.include_router(materials.router)
    app.include_router(settings_api.router)
    app.include_router(chat.router)
    app.include_router(study.router)
    app.include_router(jobs_api.router)
    app.include_router(slides.router)
    app.include_router(documents.router)
    app.include_router(cards.router)
    app.include_router(hub.router)
    app.include_router(exams.router)
    app.include_router(weak.router)
    app.include_router(exports.router)
    app.include_router(timetable.router)
    app.include_router(planner_api.router)

    _mount_frontend(app)
    return app


def _mount_frontend(app: FastAPI) -> None:
    if not (FRONTEND_DIST / "index.html").exists():
        log.warning("frontend/dist not found; run `npm run build` in frontend/ (API still works)")
        return
    assets = FRONTEND_DIST / "assets"
    if assets.exists():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str) -> FileResponse:
        if path.startswith("api/"):
            raise HTTPException(404)
        file = (FRONTEND_DIST / path).resolve()
        if path and file.is_file() and FRONTEND_DIST in file.parents:
            return FileResponse(file)
        # Revalidate every time: a cached index.html would point at the previous build's
        # assets after an update (the hashed /assets files themselves can be cached).
        return FileResponse(FRONTEND_DIST / "index.html", headers={"Cache-Control": "no-cache"})
