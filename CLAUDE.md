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
cd frontend; npm run dev:preview              # Vite on :5174, proxies /api to the dev instance :8766
```

New migration after changing `backend/core/models.py`:
`uv run alembic -x db=<scratch>.db revision --autogenerate -m "msg"` against a fresh scratch DB
upgraded to head first (`uv run alembic -x db=<scratch>.db upgrade head`), then review the file.
Migrations run automatically on startup.

## Layout

```
backend/            Python package (import as `backend.*`), entry point backend/main.py;
                    launcher.py = windowless start for the shortcut (`pythonw -m backend.launcher`,
                    `lecturelab shortcut`), core/window.open_ui = Edge/Chrome --app window
  app.py            FastAPI factory; serves frontend/dist + SPA fallback
  api/              routers: system (health, SSE /api/events), subjects, ...
  core/             config (pydantic-settings + <data>/config.toml), db (SQLite WAL, Alembic),
                    models (SQLModel tables), jobs (persistent queue + worker), events (SSE bus),
                    media (ffprobe/ffmpeg helpers; all audio ends up as mono AAC audio.m4a),
                    library (lecture folders), state (last-used subject), backup (DB snapshot
                    zips with day/week rotation + incremental mirror of subjects/; scheduler
                    thread outside the job queue; `lecturelab backup` / `restore` CLI)
                    lan (LAN mode: LanGuard ASGI token wrapper + second uvicorn server thread),
                    awake (KeepAwake thread: SetThreadExecutionState while recording (screen on
                    too) or while jobs are queued/running; `keep_awake` setting)
  capture/          live (LiveTranscriber: tails recording.pcm, windows -> voice.stt), sources (system audio = process loopback of every app except our own process
                    tree via process_loopback (plain ctypes COM), works muted; falls back to
                    soundcard WASAPI device loopback; mic via soundcard) -> mixer (wall-clock alignment, silence
                    padding) -> session (raw PCM + session.json, fsync) ; recorder (service,
                    crash recovery) ; encode (job: PCM -> audio.m4a) ; hotkeys (pynput) ; tray ;
                    slides (change detector on 64x36 thumbnails with learned video mask, slide
                    storage, SlideGrabber thread) ; screens (ctypes: find Zoom window,
                    PrintWindow/ImageGrab, monitors)
  pipeline/         ingest (import job), download (job `download_media`: yt-dlp child process
                    for links/streams, cookies.txt from the extension -> then the import job; `save_media` =
                    just save to the Downloads folder, no lecture), stages (stage bookkeeping + auto-queue transcription),
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
                    stored as subject-level Note kind "course"; queued after each generation),
                    cheatsheet (job: subject-level Note kind "cheatsheet"), recap (job:
                    spoken script -> tts_worker -> chaptered AAC)
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
                    ranges -> cards/questions -> mastery, concept map), exams (past-exam analysis
                    + profile, exam build/take/grade jobs; graded answers also become Attempts),
                    weak (topic strength = week-ahead FSRS memory + review recall + answer
                    scores; `weak_practice` job writes PracticeSets for weak topics)
                    deadlines (suggestions from announcements notes, review states), planner
                    (deterministic day-by-day plan in study_plan_item, rebuilt when the day /
                    exam dates / open deadlines / settings change), speak (teach-back + oral
                    exam conversations, synchronous LLM turns)
  timetable/        ics (parse + expand recurring events, course key/kind from titles), service
                    (sources, sync, subject matching, now/next, TimetableScheduler thread:
                    sync, reminders, opt-in auto-record when Zoom is open during a slot)
  voice/            stt (warm `whisper_worker --serve` child for spoken answers, outside the job
                    queue, idle unload), tts (Piper voices: catalogue, download, WAV cache),
                    tts_worker (child: sections -> one WAV + chapter starts; used by recaps)
  evals/            runner (eval cases from note sections, retrieval + grounding runs, `eval` job)
  exports/          cards (Anki .apkg via genanki with stable GUIDs per card id, CSV), vault
                    (Obsidian zip: lecture notes, course overview with wikilinks + Mermaid,
                    glossary, cheat sheet), pdf (headless Edge/Chrome prints /print/... pages)
                    calendar (.ics of exams, deadlines, study plan; stable UIDs)
prompts/            prompt templates with `version:` front matter; bump it when changing a prompt
                    (stored on every generated note as prompt_version)
frontend/           React + TS + Vite + Tailwind v4 + shadcn/ui (radix-nova), TanStack Query
  src/components/ui shadcn-generated, don't hand-edit (excluded from lint/format)
tests/              pytest; fixtures/lecture.m4a + lecture.txt = 2-min TTS sample lecture
                    (regenerate with scripts/make_fixture.ps1)
scripts/            seed data
extension/          MV3 browser extension (Chrome/Edge, no build step): sniffs media requests per
                    tab, sends page/stream links + cookies to POST /api/downloads
```

Data lives in `%USERPROFILE%\LectureLab` (override: `LECTURELAB_DATA_DIR` or `--data-dir`):
`lecturelab.db`, `config.toml`, `logs/`, `subjects/<slug>/<YYYY-MM-DD>_<slug>/`, `trash/`.

Preview/dev instance that doesn't touch real data: `.claude/launch.json` runs
`python -m backend.main --data-dir data/dev serve --no-browser --no-tray --no-hotkeys --port 8766`
(not lecturelab.exe: a running exe can't be replaced, and the user's `start.ps1` then failed to
start; start.ps1 now also falls back to `uv run --no-sync` when `uv sync` can't replace it).

## Conventions

- Python 3.12, type hints, ruff (line length 100). Paths via `pathlib`; must work with spaces.
- Datetimes are tz-aware UTC (`models.utcnow()`); sqlmodel's UTCDateTime rejects naive ones.
- Paths stored in the DB are relative to `settings.data_dir`.
- Subject `slug` = folder name; never changes after creation (renaming only changes `name`).
- Long work goes through `core.jobs` (`@handler("kind")`, `enqueue(...)`). Two lanes: "main"
  (everything else, one at a time) and "side" (`jobs.SIDE_LANE`: slide OCR, CPU only), so OCR of
  one lecture runs while another is transcribed. Never two jobs on the same lecture at once (24
  places write `lecture.stages`), and lecture jobs not in `BEFORE_SLIDE_TEXT` wait while that
  lecture's OCR is queued or running. Tests' `Worker()` (no lane) still runs everything in id
  order. GPU/CPU-heavy handlers must run the heavy part in a child process.
- Claude jobs (`jobs.LLM_LANE`: cleanup, generate, subject_hub, cheatsheet) run on a third lane
  "llm" when the provider is Anthropic (Ollama uses the GPU: they stay on "main"), so a long
  wait for Claude never holds up a transcription. Batch mode (`llm.anthropic_batch`, Settings
  switch -> state.json `llm_batch`, default on): background tasks (`BATCH_TASKS`) become
  one-request Message Batches at half price; no `fallbacks` there (rejected), so a refusal or a
  batch older than `batch_timeout_s` is re-sent streamed; 1-hour cache TTL so the 8 parallel
  calls of a lecture still share the cached context; shutdown cancels open batches
  (`jobs.shutting_down()`). `Usage.batch` halves the price, `cache_write_1h_tokens` = 2x input.
- Moodle watch: a new episode whose subject already has a non-Opencast lecture that day (same
  type, >= 5 min: the user's own recording) goes to review with "You recorded this yourself"
  instead of downloading (`watch._own_recording`).
- Whisper on the GPU runs batched (`transcription.batch_size`, faster-whisper's
  BatchedInferencePipeline; halves itself on CUDA out-of-memory). OCR groups pictures of the same
  slide (only additions, `ocr_worker.runs`) and reads the last of each group, 3 at a time.
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
- While the user's app is running (and especially recording): don't restart it, don't run
  `uv sync`, don't rebuild `frontend/dist` (it serves that folder; a new UI would call endpoints
  its old backend lacks) and don't edit `whisper_worker.py`/`ocr_worker.py` (child processes
  start from the current source). Check the UI via `lecturelab-dev` + `lecturelab-ui-preview`
  in `.claude/launch.json`; test migrations on a `sqlite3` backup-API snapshot of the live DB.
- Capture threads must join the COM MTA themselves, after importing soundcard (see
  `sources._co_initialize`): soundcard's own COM init dies with the first capture thread.
- Job child processes (Whisper, OCR) are registered with `jobs.track_child` so shutdown can end
  them; a job interrupted by shutdown is re-queued, not failed.
- Print pages (`/print/lectures/:id`, `/print/subjects/:id/cheatsheet`) live outside the app
  shell, force the light theme and must not open the SSE stream (LiveUpdates skips /print/):
  an open stream keeps headless Edge from ever printing.
- Exam-simulator questions are stored with `generation = 0`; lecture regeneration never retires
  them.
- GitHub: private repo dezaki1/lecturelab; push after each milestone commit.
- Background schedulers (backup, timetable) are threads started in the app lifespan only when
  `start_worker`; tests call their methods directly with a fixed `now` (and a fake `find_zoom`).
- Recorder start without subject_id: timetable slot first, then the last-used subject.
- LAN mode: anything reachable through `LanGuard` is reachable from the network. New PC-only
  endpoints (settings, secrets, things that fetch URLs or touch devices) go into
  `lan.BLOCKED`. Tests keep the token in memory (autouse conftest fixture) and bind LAN
  servers to 127.0.0.1 only. The dev instance's config.toml pins `[lan] host = "127.0.0.1"`.
- Voice: tests use `voice.tts_engine = "fake"` (conftest) and the fake Whisper engine, which
  answers in serve mode too. Never download Piper voices in tests.
- Writing a file twice in quick succession sometimes fails on this machine (OSError 22, a
  brief lock): retry instead of assuming the content is wrong.
- Import from link: /api/downloads fetches any URL, so it is in `lan.BLOCKED` and refuses
  browser requests whose Origin is another website (extension origins and our own host pass).
  Tests run real yt-dlp against a local http.server, never the internet.
- Tests can't reach the network: conftest patches httpx/httpx2 HTTPTransport to refuse
  anything but loopback (and Ollama's port). The CLI and anything else that loads its own
  settings would otherwise pick the real provider and the developer's key from the keyring.
- Moving a lecture to another subject or deleting it goes through `pipeline.lecture_ops`
  (re-homes every row with a lecture_id + subject_id, rebases stored file paths, rebuilds both
  subjects' hub/cheat sheet/plan, flags week recaps). New tables with both columns belong in
  `lecture_ops.LECTURE_ROWS`; new stored paths under the lecture folder need a rebase there.
  `lecture_ops.repair` runs at startup and fixes lectures moved by older versions (idempotent).
  A subject change while the lecture is processing queues `lecture_ops.MOVE_JOB` (FIFO, so it
  runs before the pending transcribe/generate) and sets `meta.move_queued`.
- Watched Moodle courses (`pipeline.watch`, endpoints under `/api/downloads/watch`, so PC-only
  with the origin check): the extension (`extension/watch.js`, alarm every minute) asks
  `state()` which courses are due and `report()`s Opencast episodes (id, title, length, signed
  mp4 link of the `mainAudio` stream, ~720p). LectureLab keeps the schedule and decides: first
  scan = backlog to review, later finds download at once. Episode ids dedupe (also old
  `engage-player_<id>` links); the lecture stores the Moodle page URL, never the signed link
  (`queue_download(stored_url=...)`). A failed download is retried with the next fresh link
  (`retry_download`); a deleted lecture is never re-imported. Tests use the local http.server
  from test_download (`/private/` = expired link).
- Imports and finished captures store `meta.slot_suggestion` when the timetable has another
  subject's slot at that time (`timetable.service.suggest_subject`); `dismiss_slot` sets
  `meta.slot_dismissed` so it never comes back.
- uv's `.venv\Scripts\pythonw.exe` is a CONSOLE trampoline (it starts the base python.exe):
  never point a shortcut at it directly. Shortcuts run `wscript scripts\LectureLab.vbs`, which
  starts it with window style 0 (hidden).
- Never start the user's app from Claude's own shell (`start`, `wscript ...`): it then lives in
  Claude's process tree and died mid-lecture when the Claude app restarted its service
  (2026-10-20). Start it as the user would: `explorer.exe "%USERPROFILE%\Desktop\LectureLab.lnk"`.
- One app per data folder: `serve` (and the launcher, before updating/building) holds
  `<data>/.lecturelab.lock` (`util.instance_lock`). Two copies started by a double-click once
  ran the same encode job twice and broke a recording.
- Windows Controlled Folder Access can refuse new files (FileNotFoundError on create). A failed
  session.json save never ends a recording (`_save_session`); recorder start and the launcher
  check `util.write_problem` first and show `write_blocked`'s explanation.
