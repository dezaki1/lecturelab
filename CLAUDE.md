# LectureLab: notes for Claude

Personal local-first study app: records Zoom lectures, transcribes, generates study material.
`SPEC.md` is the source of truth; `PROGRESS.md` tracks milestones; ideas go in `BACKLOG.md`.

## Run / test / lint

```powershell
.\start.ps1                                   # build frontend if stale, start app, open browser
uv run lecturelab serve --no-browser --no-tray
uv run lecturelab seed-subjects scripts\subjects_ws2026.json
uv run pytest                                 # backend tests (no network, no real audio devices,
                                              #   fake transcription engine)
$env:LECTURELAB_TEST_WHISPER=1; uv run pytest -k real   # opt-in: real Whisper on the fixture
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
                    models (SQLModel tables), jobs (persistent queue + worker), events (SSE bus),
                    media (ffprobe/ffmpeg helpers; all audio ends up as mono AAC audio.m4a),
                    library (lecture folders), state (last-used subject)
  capture/          sources (soundcard WASAPI capture) -> mixer (wall-clock alignment, silence
                    padding) -> session (raw PCM + session.json, fsync) ; recorder (service,
                    crash recovery) ; encode (job: PCM -> audio.m4a) ; hotkeys (pynput) ; tray ;
                    slides (change detector on 64x36 thumbnails with learned video mask, slide
                    storage, SlideGrabber thread) ; screens (ctypes: find Zoom window,
                    PrintWindow/ImageGrab, monitors)
  pipeline/         ingest (import job), stages (stage bookkeeping + auto-queue transcription),
                    transcribe (job: runs whisper_worker child process, CPU fallback, re-segments
                    into sentence lines), whisper_worker (standalone child: ffmpeg decode +
                    faster-whisper or the "fake" engine; JSON-lines progress on stdout)
                    generate (job: study material; context building, refs, versioning, dedupe,
                    verification), slides (job `slides`: OCR via ocr_worker child process
                    (RapidOCR, CPU); slide scan of imported videos), ocr_worker,
                    cleanup (job: cheap-model fix of misheard terms -> TranscriptSegment.raw_text
                    keeps Whisper's line; then generate/ready/index), documents (job: slide PDFs
                    via pypdfium2 -> page JPEGs + text -> Slide rows source "pdf"), align (TF-IDF
                    + monotonic DP placing PDF pages; captured slides are exact anchors),
                    hints (keyword exam-hint detector EN/DE at transcription; hint list
                    appended to section instructions after the cache breakpoint),
                    subject_hub (job: course overview/topics/concept map from per-lecture notes,
                    stored as subject-level Note kind "course"; queued after each generation)
  ai/               providers/ (anthropic, ollama + openai-compatible, mock), schemas (structured
                    outputs), prompts (loader for prompts/*.md), types (LLMRequest/Usage/PRICING),
                    keys (keyring)
  search/           embeddings (Ollama bge-m3, cache table, mock), index (chunking + `index` job,
                    chunk/chunk_fts/chunk_vec share rowids), retrieve (hybrid FTS5 + vec0 + RRF),
                    chat (scoped context, retrieval, citation labels -> lecture/time)
  study/            review (FSRS scheduling, queue, undo, stats), practice (grading, picking),
                    cards (browse/filter, bulk actions, duplicate groups, merge), dashboard
                    (retention, activity, forecast, mastery = mean FSRS retrievability per subject),
                    topics (hub read side: cumulative glossary, topic -> note sections -> time
                    ranges -> cards/questions -> mastery, concept map)
  exports/          cards (Anki .apkg via genanki with stable GUIDs per card id, CSV)
prompts/            prompt templates with `version:` front matter; bump it when changing a prompt
                    (stored on every generated note as prompt_version)
frontend/           React + TS + Vite + Tailwind v4 + shadcn/ui (radix-nova), TanStack Query
  src/components/ui shadcn-generated, don't hand-edit (excluded from lint/format)
tests/              pytest; fixtures/lecture.m4a + lecture.txt = 2-min TTS sample lecture
                    (regenerate with scripts/make_fixture.ps1)
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
- While the user's own instance is running, `uv run` can't replace .venv\Scripts\lecturelab.exe:
  use `uv run --no-sync ...` / `uv sync --inexact --no-install-project` and don't kill their app.
- faster-whisper gets audio as a NumPy array decoded by our ffmpeg (PyAV 19 broke its av.open).
- LLM calls: only through `backend.ai.providers.get_provider()`. Tests use `llm.provider = "mock"`
  (conftest) and must never reach a real API. Every generated item needs source_refs.
- Anthropic: claude-sonnet-5-5 (main) / claude-haiku-5-5 (cheap), adaptive thinking via effort,
  streaming, cache breakpoint after the lecture context, `fallbacks: "default"` (beta
  server-side-fallback-2026-07-01) on Sonnet only. The 7 generation calls send ALL output schemas
  as (non-strict) tools `submit_<kind>` so the cached prefix is identical; a per-call
  `output_format` sits in the prefix and kills cache sharing, and one combined strict schema
  is rejected ("compiled grammar is too large"). Single-schema calls (verify) use output_format.
- Virtual tables (chunk_fts, chunk_vec) live in hand-written migration SQL; env.py's
  include_object keeps autogenerate from dropping them. sqlite-vec loads on every connection.
- Pipeline: capture -> transcribe -> generate (if an LLM is configured) -> index. Index jobs
  never block the busy checks (Job.kind != "index").
- Don't write multi-line Python/TS edits through bash heredocs with backslash escapes (they get
  mangled on this machine); use the file edit tools or a script file.
- Ask before adding heavy deps or downloading models (SPEC §0.8).
- Slides: `<lecture>/slides/<ms>.jpg` + Slide rows written immediately during capture (crash-safe).
  Tests run with `slides.enabled = False` (conftest) so nothing grabs the real screen; pass a
  fake `screen_factory` to `create_app`. OCR is queued before transcription (FIFO queue) and a
  failed OCR never fails the lecture. `ocr_text == " "` means "OCR ran, no text".
- UI-editable settings that aren't secrets (LLM provider, slide capture) live in state.json and
  override config.toml at startup.
- Generation kinds run in ALL_KINDS order: "hints" first (alone; it writes the prompt cache and
  later calls read its result), then the 7 sections + "explanations" (confused/question
  bookmarks) in parallel. All 9 output schemas are the shared tool set.
- JSON columns (stages, meta, ...) don't track in-place mutation: always reassign
  (`lecture.stages = {**lecture.stages, k: v}`); an autoflush in between loses in-place edits.
- Pipeline order: capture -> [slides OCR] -> transcribe -> [slide PDFs placed] -> cleanup ->
  generate -> index. Cleanup is skipped for local providers unless `generation.cleanup_local`;
  tests default to `generation.cleanup = False` (conftest). A failed cleanup still continues.
- "Primary" slides (generation context, transcript/notes thumbnails): PDF pages if the lecture
  has any, else captured slides (`pipeline.slides.primary_slides`).
- Test PDFs: `tests/pdfgen.make_pdf` writes real text PDFs (Pillow PDFs have no text layer).
- Frontend graphs: concept map = @dagrejs/dagre layout (lazy-loaded) + own SVG, not Mermaid
  (Mermaid is ~120 MB of deps; our data is already structured and nodes need links).
- GitHub: private repo dezaki1/lecturelab; push after each milestone commit.
