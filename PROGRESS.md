# Progress

## Phase 1 (MVP)

- [x] **M1 Skeleton**: uv project, config, SQLite (WAL) + Alembic, job queue, SSE bus, subjects CRUD, app shell, `start.ps1`
- [x] **M2 Recorder**: WASAPI loopback (+ optional mic) via `soundcard`, wall-clock silence padding,
  crash-safe PCM (fsync every 3 s) with recovery on next start, disk-space guard, live meters,
  bookmarks, UI/tray/hotkey control, AAC encode job, lecture list + lecture page with player
  - Verified on this laptop: Razer headset loopback + Realtek mic, tone captured at -27 dBFS,
    file length = wall-clock length; hotkeys verified with injected key events.
  - Not yet verified with a real default-device switch (covered by a unit test with a fake device).
- [x] **M3 Import**: drag & drop anywhere (or Import button) for mp4/m4a/mp3/wav/webm (+ mkv, mov,
  ogg, opus, flac, aac, wma); raw-body streaming upload with progress, ffprobe validation (rejects
  files without audio), recording date from the container's creation_time or file mtime − duration,
  `import_media` job extracts mono AAC and deletes the uploaded copy after verifying duration
- [x] **M4 Transcription**: faster-whisper `large-v3-turbo` in a child process (VRAM freed on exit),
  CUDA int8_float16 with automatic CPU int8 fallback, VAD, word timestamps, subject vocabulary as
  hotwords, language auto-detect / per-subject / per-run override, ffmpeg decoding, auto-queued
  after recording/import, re-run endpoint, sentence-level lines from word timings, transcript view
  with synced highlight, follow mode, click-to-seek, search (/), bookmark markers, low-confidence hints
  - Measured on this laptop (114 s TTS fixture): **GPU 14–16× real time** (~6–7 min per 90-min
    lecture + ~7 s model load), **CPU 1.6× real time** (~55 min per 90-min lecture).
    Word accuracy vs. script: 99.1 % (`LECTURELAB_TEST_WHISPER=1 uv run pytest -k real`).
  - Real lecture (69 min, imported): **GPU 18.4× real time**, 3.8 min including model load.
- [x] **M5 Generation**: summary, study notes, glossary, announcements, flashcards, exercises, exam
  questions (prompts/*.md, versioned) via structured outputs validated with Pydantic + retries;
  shared prompt-cached prefix (system + lecture context); refs from cited timestamps on every item;
  Haiku verification pass flags unsupported cards/questions; card dedupe across the subject;
  versioned notes with in-place editing, history and restore; regenerate any section with extra
  instructions; auto-generation after transcription; cost estimate + usage records; providers:
  Anthropic (Sonnet 5.5 / Haiku 5.5, refusal fallback), Ollama, OpenAI-compatible, mock;
  Settings page with key in Windows Credential Manager
  - Verified end to end with the mock provider (tests + UI) and with real Claude on the 2-min
    fixture: all 7 sections correct (exercise arithmetic checked by hand), 27-31 s, **$0.09**.
    Prompt caching: 1 cache write (tools + rules + transcript) and 6 cache reads per lecture.
    Verifier tested with planted errors (wrong fact, wrong eigenvalues): both flagged.
  - Real lecture (69 min): **$0.24** for all seven sections incl. verification (≈ $0.31 per
    90 min), 1 cache write of 16k tokens and 6 cache reads.
- [x] **M6 Search & chat**: lectures indexed as 60-90 s transcript chunks + note sections into
  SQLite FTS5 and sqlite-vec (bge-m3 via Ollama, embeddings cached); hybrid search with reciprocal
  rank fusion, keyword-only fallback when Ollama is down; global search (Ctrl+K); chat scoped to a
  lecture (full transcript in cached context), a subject or everything (retrieval + follow-up
  query rewriting), optional date filter, grounded mode, streaming over SSE, citation chips
  ([L5 · 34:12]) that open the lecture at that moment, quick actions (make flashcards, explain
  simpler, example, quiz me), chat history per thread
  - Installed Ollama 0.40.1 (winget) + bge-m3. Verified on real data: German query finds the
    English passage (semantic rank 1, no keyword match); subject chat answers cite exact moments;
    lecture chat follow-ups read the prompt cache ($0.009 first turn, $0.0026 follow-up).
- [x] **M7 Review & practice**: FSRS (`fsrs` 6.3) daily queue across subjects (due first, then
  up to 30 new cards/day in lecture order, "learn 10 more"), keyboard review (Space flip/Good,
  1-4 rate, U undo), interval previews on the buttons, reversed cards alternate direction, cloze
  blanks, suspend, review logs with undo, streak; practice mode (random or weakest-first, exam
  questions or exercises): MCQ/TF auto-graded, multi-select partial credit, open questions scored
  by ticking rubric items after seeing the model answer, attempts stored; sidebar review badge
- [x] **M8 Jobs & costs**: jobs page (running/waiting, failed, all) with live progress, errors,
  retry and cancel; sidebar indicator for active and recently failed jobs; one "Lecture ready"
  Windows notification per lecture (tray) plus one notice per failed job; usage page with cost
  per day (bar chart), per task, per model, per subject and per lecture, cost per lecture hour
  and cache savings; "Generate all" for transcribed lectures without study material, with a
  total cost estimate first

## Phase 1 acceptance (SPEC §6)

- [~] One hotkey records a 90-minute lecture; killing the app leaves a playable recording.
  Hotkeys, recording and crash recovery verified (kill test in the suite); a full 90-minute
  Zoom lecture not yet recorded.
- [x] Lecture transcribes on the GPU; CPU fallback works. Real 69-minute lecture: GPU 18.4x
  real time (3.8 min); fixture: CPU 1.6x (fallback tested). Pipeline confirmed end to end by
  the user (2026-10-09). Recordings without speech finish as "No speech detected" instead of
  queuing a generation that fails.
- [x] All seven output types generated; every note section, card and question links to a
  timestamp that seeks the audio.
- [x] Chat answers cite sources; grounded mode says when something isn't in the material.
- [x] Everything works offline with Ollama selected (qwen3:8b + bge-m3), with caveats.
  Measured on the 2-min fixture (RTX A1000 4 GB: qwen3:8b runs ~70% on CPU, ~3.4 tok/s):
  - Chat: first words after ~21 s, full answer ~36 s, correct with correct citations.
  - Study material: all 7 sections generated, but ~40 min of model time for a 2-minute lecture
    (a 90-minute lecture would take hours), weaker content (one wrong citation, one wrong
    definition, a wrong eigenvalue calculation). Local fact-checking passed wrong answers, so
    it is off for local models by default (items stay "unchecked").
  - Conclusion: Claude for generation; Ollama for offline chat/search and as an emergency
    fallback (or a smaller model such as qwen3:4b for speed).
- [x] Tests run the pipeline end-to-end on the fixture with the mock provider; README covers
  setup incl. CUDA/cuDNN on Windows.

## Phase 2 (study engine & richer capture)

Order agreed 2026-10-09: slides first (they can't be captured after the fact), then study features.

- [x] **M9 Slide capture**: during recording the Zoom meeting window (matched by window class or
  title; else an optional fallback monitor) is grabbed every 2 s via PrintWindow (works when
  covered); a change detector on 64x36 thumbnails learns and ignores speaker-video areas, skips
  transitions/blank frames and keeps one JPEG per new slide (written immediately, crash-safe);
  imported videos are scanned the same way; RapidOCR (local, CPU, child process) reads slide
  text, queued before transcription; slide text is indexed for search/chat; Slides tab with
  viewer (← →, play from here, delete, OCR text), thumbnails above transcript lines and next to
  note sections; recorder panel toggle + live count; Settings card (default, fallback monitor,
  window titles, test capture)
  - Verified: 2-min demo video with 4 slides → 4 slides found within 2 s of each change, OCR
    correct incl. λ, Σ, ≥; window capture of a GPU-rendered window ~35 ms.
  - Still to verify with a real Zoom meeting (window class names `ConfMultiTabContentWndClass`
    / `ZPContentViewWndClass` are from Zoom's docs/community, not tested here): use Test capture.
- [x] **M10 Slide PDFs + transcript cleanup**: add slide PDFs to a lecture (pypdfium2: page
  images + text layer); pages placed on the timeline by TF-IDF similarity + monotonic alignment
  (captured slides as exact anchors, transcript for the rest, refined to the sentence; pages of
  other lectures left out, gaps interpolated); PDF pages become the lecture's primary slides;
  slide text (PDF or OCR) goes into the generation context (system prompt v3); transcript
  cleanup job with the cheap model before generation (only near-identical line fixes accepted,
  Whisper's line kept in `raw_text`), diff view, per-line and full undo, re-run; failed cleanup
  never blocks generation
  - Verified on the 2-min demo: 6 of 7 PDF pages placed (off-topic page dropped), anchored pages
    at the captured slides' exact times, transcript-placed pages within ~1 s of the right
    sentence; alignment test on the fixture script within ~5 s.
  - Still to see on real lectures: German slides with English speech (TF-IDF needs shared
    words; embeddings would help, see BACKLOG) and the real cost/benefit of cleanup.
- [x] **M11 Cards + dashboard**: card browser (subject/state/search filters, sort by due or
  most forgotten, paging), edit text/type/tags, suspend/unsuspend/reset/delete singly or in bulk;
  duplicate groups within a subject (difflib with cheap upper bounds first) and merge (sources
  and tags united, most-practised schedule kept, review logs moved over); export to Anki .apkg
  (genanki, own note types with a Source link back to the lecture, stable GUIDs, subdeck per
  lecture, Markdown/math converted for MathJax) and CSV; dashboard with today's work, streak,
  30-day true retention, next exam, 30-day activity, 7-day forecast, per-subject cards
  (new/young/mature), mastery (mean FSRS retrievability) and practice score
  - Also: index.html is now served with `Cache-Control: no-cache` (a cached copy showed the
    previous build after an update).
- [x] **M12 Confused bookmarks + exam hints**: keyword exam-hint detector (EN + DE, exam vs
  emphasis, merged spans) at transcription + backfill for old lectures; generation's new first
  call "hints" confirms them, finds repeated points and writes a topic line (replacing keyword
  hints); the hint list is appended after the cache breakpoint to the notes/summary/glossary/
  announcements/cards/exercises/questions instructions; Exam hints tab, ★ in the transcript,
  subject-wide hint list; "explanations" call for confused/question bookmarks (simpler
  explanation, example, check question, refs), on demand for new bookmarks, check question can
  become a flashcard
  - Also fixed: in-place JSON mutations of lecture.stages were lost after an autoflush (now
    reassigned); materials refresh on lecture events; regenerating a single section no longer
    sends "Lecture ready".
- [x] **M13 Subject hub**: course overview job (built from per-lecture TL;DR, note headings,
  glossary terms and exam hints, not transcripts; queued once per subject after each
  generation, skipped for local models by default; versioned subject-level Note): overview +
  per-lecture recaps, topics (with the note sections teaching them; names kept stable across
  updates), concept map (nodes/relations); topic mastery from cards whose sources fall in the
  topics' section time ranges; cumulative glossary merged by term; subject-level PDFs; subject
  page tabs (Lectures, Course overview, Topics, Concept map, Glossary, Exam hints, Materials)
  - Concept map drawn with @dagrejs/dagre + SVG instead of Mermaid (SPEC default): ~16 KB
    gzipped on demand vs. ~120 MB of Mermaid deps, and nodes link to their lectures.
- [x] **M14 Past exams + exam simulator**: past exam PDFs (text layer, or OCR for scans) analysed
  once by the main model (questions with type/points/topic, duration, total, style notes) into a
  per-subject exam profile that steers the simulator and is appended (after the cache
  breakpoint) to lecture exam-question generation; exam builder (question bank filtered by type,
  difficulty and hub topics, weighted towards weak topics, emphasised and unanswered questions,
  topped up with new questions in the past exams' style, stored with generation 0); timed exam
  with server-side deadline, autosaved answers, auto-submit, late flag; grading job (choice
  questions automatic, open answers by Sonnet against the rubric with per-item points clamped,
  feedback, summary, topics to revise); answers recorded as Attempts (practice stats, topic
  mastery); history with score trend
  - Verified in the browser on dev data (mock model): build, start, autosave across a reload,
    timer, submit, graded review.
  - Also fixed (found during a live recording): only the first recording per app session
    captured audio (COM initialised in the first capture thread only); quitting hung on open
    browser tabs while the job worker kept starting jobs.
- [x] **M15 Weak spots**: per-topic strength (hub topics, or lectures without a hub) from the
  last 60 days: week-ahead FSRS memory of reviewed cards (not instant retrievability, which is
  ~100% right after any review), review recall (share not rated Again, from 3 reviews) and
  answer scores from practice and exams; weak / ok / untested with the forgotten cards and
  lost-point answers; subject Weak spots tab and dashboard card; "Practice my weak topics"
  job writes new questions aimed at those mistakes (main model, past-exam style) as a
  PracticeSet opened on the Practice page
  - Verified in the browser on dev data: forgotten reviews -> weak topic -> practice set of 6
    new questions -> Practice page.
- [x] **M16 Exports**: Obsidian vault zip (per-subject folders; lecture notes with frontmatter,
  prev/next and overview links, timestamps linking back to LectureLab, headings cleaned for
  wikilink anchors, Spaced Repetition flashcards incl. cloze, exam questions as collapsible
  callouts; course overview with topic wikilinks to lecture sections and the concept map as
  Mermaid; glossary; cheat sheet); PDFs of lectures and cheat sheets printed by headless Edge
  (Chrome fallback) from print pages, with a print-view fallback; per-subject cheat sheet job
  (main model, 1-2 pages, two-column print layout, editable, versioned)
  - Verified: Edge printed the lecture print page (3 pages, math rendered) and the cheat sheet
    (1 page) from the dev instance; vault export of the dev data (20 files).

Phase 2 complete (2026-10-13).

## Phase 3 (automation & extras)

Agreed 2026-10-13: all of M17-M24 in this order; Piper (local) for text-to-speech; study plan
and deadlines as .ics (no TickTick account yet); backups to a folder the user chooses.

- [x] **M17 Backup**: backup folder + schedule in Settings (stored in state.json); each run
  writes a consistent DB snapshot zip (SQLite backup API, with config.toml and state.json)
  rotated to the newest per day (7) and per week (4), and mirrors new/changed files of the
  lecture folders (skipping raw PCM/temp); scheduler thread checks every 10 min (not a queue
  job, so it never blocks transcription); failure notifications; `lecturelab backup` and
  `lecturelab restore <folder> --to <dir>` CLI
  - Verified in the browser on dev data (21 files, snapshot written), restore in tests.
- [x] **M18 Timetable**: calendar sources (subscribed link re-synced every 6 h, or an uploaded
  .ics) parsed with icalendar + recurring-ical-events (RRULE/time zones, 30 days back / 200
  ahead, all-day and cancelled events skipped); course key = title without LVA number and type,
  type -> lecture kind; auto-match to subjects (course code, name similarity, token containment)
  with user override / ignore; `/api/timetable/now|week`; dashboard Today card; recorder start
  without a subject uses the current slot (also preselected in the panel); reminders N min
  before with a Record action; opt-in auto-record when the Zoom meeting window is open during a
  slot (stops after the slot once Zoom has been gone 60 s; once per slot)
  - Verified in the browser on dev data (KUSSS-style sample calendar: matching, Today, ignore).
- [x] **M19 Deadlines & study planner**: Deadline table filled from each announcements note
  (deadline/exam/assignment always, admin/other only with a date; past dates skipped) as suggestions keyed by
  lecture + normalised text (decisions survive regeneration, stale suggestions dropped; startup
  backfill); accept/edit/dismiss/done, manual items, "set as exam date". Deterministic planner
  (no LLM): daily review block from the FSRS due forecast, topic blocks by remaining need x
  1/sqrt(days left) with per-day sharing, weakest eligible topic with expanding gaps
  (2/4/7/14 days), mock exam two study days and cheat sheet the last study day before each
  exam; minutes/day, study weekdays, calendar start time; rebuilt on a new day or when exams,
  open deadlines or settings change, done blocks kept. `/api/calendar.ics` (exams, open
  deadlines with alarms, one study event per day, stable UIDs); Plan page, Overview card
  - Verified in the browser on dev data (accept, add, tick off, .ics with 24 events).
- [x] **M20 Tablet access (LAN + PWA)**: opt-in second uvicorn server (thread, lifespan
  off) on 0.0.0.0:8770 serving the same app through `LanGuard` (pure ASGI): pairing token in
  Credential Manager, `/?token=` sets an HttpOnly SameSite=Lax cookie and redirects, Bearer also
  accepted, /api 401 unpaired, PC-only 403 (settings writes, /api/lan, recorder writes,
  timetable writes), unpaired pages get a "not paired" notice, PWA files public; toggle without
  restart, port-in-use error, new pairing code unpairs all; QR via segno; `/api/system.remote`
  hides the recorder and settings on devices. PWA: manifest, icons, network-first service
  worker (never /api), responsive shell (drawer below md, 16 px gutters on phones)
  - Verified in the browser through the guarded listener (dev instance bound to loopback):
    unpaired 401, pairing link -> cookie -> Review/Cards/lecture at phone and tablet size,
    remote settings notice. The pane can't register service workers on unmanaged ports, even
    without the guard, so that part is untested on a device.
- [x] **M21 Teach-back & oral exam**: SpeakSession (turns JSON); teach-back = main model judges
  the explanation against the topic's lecture notes (covered / missing with refs /
  misconceptions with corrections + refs / follow-up, max 2 rounds); oral exam = examiner turns
  (main model, effort "low" via new `LLMRequest.effort`, cached notes prefix, conversation after
  the breakpoint; hard caps on main questions and 2 follow-ups each) + final evaluation (score,
  Austrian grade, per question, gaps, misconceptions, revise). Voice: `whisper_worker --serve`
  child kept warm (unloads after 5 idle min, CPU while a transcription job runs, stderr to a log
  file), Piper TTS (piper-tts 1.8, voices downloaded on request into models/piper, WAV cache,
  fake engine for tests); browser MediaRecorder answers, editable transcript; Settings → Voice
  - Verified: real Piper sample -> real Whisper round trip exact (10.6 s cold, 1.4 s warm);
    teach-back and oral exam in the browser with the mock provider (audio streamed, feedback,
    refs, grade). Not run with a real LLM or a real microphone in the pane.
- [x] **M22 Audio recaps**: Recap table (scope lecture | week with optional subject);
  `recap` job: main model writes a chaptered spoken script (`prompts/recap.md`: notes +
  summary + exam hints per lecture, [#k] keys for refs, ~160 words/min) ->
  `voice.tts_worker` child renders one WAV with pauses and section start times -> ffmpeg mono
  AAC with ffmetadata chapters. Lecture recaps in the lecture folder, week recaps in
  `<data>/recaps/`; a new recap replaces the old one of the same scope (queued job
  cancelled); opt-in auto recap after each full generation; Recaps page, lecture tab (lecture
  tabs now in `?tab=`), player with chapters, speed, Media Session, script, download
  - Verified: real Piper on ~580 words = 3.5 min audio in 12 s (17× real time); week and
    lecture recaps built in the dev instance (mock script, real voice), ffprobe shows the AAC
    chapters; chapter seek and 1.5× in the browser.
- [x] **M23 Handwritten notes**: Material kind `handwritten` (PDF or JPEG/PNG/WebP, EXIF
  orientation applied); pages rendered/resized and read one by one by the main model with
  vision (new `LLMRequest.images`; local providers raise `LLMNoVision` -> RapidOCR fallback);
  pages in `Material.analysis["pages"]`, editable; indexed as chunk source `handwritten`; in
  the generation context (system prompt v4); My notes tab, search icon/link
  - Verified in the browser with the mock provider (photo + 2-page PDF, correction, re-index,
    search hit). Not run with real Claude vision on real handwriting.
- [ ] **M24 Live mode & evals**

## Decisions (from SPEC §13, 2026-10-08)

- LLM: tiered Claude (Sonnet for notes/cards/questions/chat, Haiku for cheap passes), Ollama offline.
- Generated material is always English; German terms kept in brackets. Per-subject override.
- Slide capture (Phase 2): Zoom window, fallback to a chosen monitor.
- Data folder: `%USERPROFILE%\LectureLab` (C: has ~30 GB free, so the recorder keeps a tight disk budget).
- Name: LectureLab. Mic: off by default, per-recording toggle.
- Audio capture: `soundcard` (WASAPI loopback, live default-device lookup, Windows-side resampling)
  instead of PyAudioWPatch, whose PortAudio device list is frozen at init.
- Subjects: one per course (VL/VO/UE of the same course share a subject; lecture `kind` records which).
- Phase 2 (2026-10-09): OCR with RapidOCR (local; rapidocr 3.9 + onnxruntime, models bundled in
  the wheel), PDFs with pypdfium2, exam-simulator grading with Sonnet.
