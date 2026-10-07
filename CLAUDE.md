# LectureLab: notes for Claude

Personal local-first study app: records Zoom lectures, transcribes, generates study material.
`SPEC.md` is the source of truth; `PROGRESS.md` tracks milestones; ideas go in `BACKLOG.md`.

## Run / test / lint

```powershell
.\start.ps1                                   # build frontend if stale, start app, open browser
uv run lecturelab serve --no-browser --no-tray
uv run lecturelab seed-subjects scripts\subjects_ws2026.json
uv run pytest                                 # backend tests (no network, no real audio devices)
uv run ruff check . ; uv run ruff format --check .
cd frontend; npm run lint; npm run format:check; npm run build
cd frontend; npm run dev                      # Vite on :5173, proxies /api to :8765
```

New migration after changing `backend/core/models.py`:
`uv run alembic -x db=<scratch>.db revision --autogenerate -m "msg"` against a fresh scratch DB
upgraded to head first (`uv run alembic -x db=<scratch>.db upgrade head`), then review the file.
Migrations run automatically on startup.

## Layout

```
backend/            Python package (import as `backend.*`), entry point backend/main.py
  app.py            FastAPI factory; serves frontend/dist + SPA fallback
  api/              routers: system (health, SSE /api/events), subjects, ...
  core/             config (pydantic-settings + <data>/config.toml), db (SQLite WAL, Alembic),
                    models (SQLModel tables), jobs (persistent queue + worker), events (SSE bus)
  capture/          sources (soundcard WASAPI capture) -> mixer (wall-clock alignment, silence
                    padding) -> session (raw PCM + session.json, fsync) ; recorder (service,
                    crash recovery) ; encode (job: PCM -> audio.m4a) ; hotkeys (pynput) ; tray
  pipeline/ ai/ study/ search/ exports/   later milestones
prompts/            prompt templates (M5)
frontend/           React + TS + Vite + Tailwind v4 + shadcn/ui (radix-nova), TanStack Query
  src/components/ui shadcn-generated, don't hand-edit (excluded from lint/format)
tests/              pytest; fixtures/ holds the sample lecture (M4+)
scripts/            seed data
```

Data lives in `%USERPROFILE%\LectureLab` (override: `LECTURELAB_DATA_DIR` or `--data-dir`):
`lecturelab.db`, `config.toml`, `logs/`, `subjects/<slug>/<YYYY-MM-DD>_<slug>/`, `trash/`.

Preview/dev instance that doesn't touch real data: `.claude/launch.json` runs
`lecturelab --data-dir data/dev serve --no-browser --no-tray --no-hotkeys --port 8766`.

## Conventions

- Python 3.12, type hints, ruff (line length 100). Paths via `pathlib`; must work with spaces.
- Datetimes are tz-aware UTC (`models.utcnow()`); sqlmodel's UTCDateTime rejects naive ones.
- Paths stored in the DB are relative to `settings.data_dir`.
- Subject `slug` = folder name; never changes after creation (renaming only changes `name`).
- Long work goes through `core.jobs` (`@handler("kind")`, `enqueue(...)`). Only one job runs at
  a time. GPU/CPU-heavy handlers must run the heavy part in a child process.
- Push state changes to the UI with `core.events.bus.publish(type, data)`; frontend listens via
  `useEvent(type, fn)` (one shared EventSource).
- Tests: never call real LLM APIs or real audio devices; use fakes. `client` fixture gives a
  TestClient over a temp data dir with the worker not started (`Worker().run_pending()` to drain).
- Recorder threading: capture threads push into `SourceBuffer`s; one mixer loop writes. Never
  block pynput hook callbacks (hand work to the hotkey worker thread).
- Ask before adding heavy deps or downloading models (SPEC §0.8).
