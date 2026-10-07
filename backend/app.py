"""FastAPI app factory. Serves the API under /api and the built frontend for everything else."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from backend.api import imports, lectures, recorder, subjects, system
from backend.capture import encode  # noqa: F401  (registers the encode job handler)
from backend.capture.recorder import (
    RecorderService,
    SourceFactory,
    default_sources,
    recover_interrupted,
    set_recorder,
)
from backend.core import jobs
from backend.core.config import Settings, set_settings
from backend.core.db import init_db
from backend.pipeline import ingest

log = logging.getLogger(__name__)

FRONTEND_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"


def create_app(
    settings: Settings,
    *,
    start_worker: bool = True,
    source_factory: SourceFactory = default_sources,
) -> FastAPI:
    set_settings(settings)
    init_db(settings.db_path)
    rec = RecorderService(settings, source_factory)
    set_recorder(rec)
    ingest.clean_tmp()
    if n := recover_interrupted():
        log.warning("Recovered %d interrupted recording(s)", n)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if start_worker:
            jobs.start_worker()
        try:
            yield
        finally:
            rec.shutdown()  # finalises an in-progress recording; encoding resumes next start
            jobs.stop_worker()

    app = FastAPI(title="LectureLab", version="0.1.0", lifespan=lifespan)
    app.include_router(system.router)
    app.include_router(subjects.router)
    app.include_router(lectures.router)
    app.include_router(recorder.router)
    app.include_router(imports.router)

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
        return FileResponse(FRONTEND_DIST / "index.html")
