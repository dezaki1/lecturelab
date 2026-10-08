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
  - Still to confirm on a real 90-minute Zoom lecture.
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
  - Still to measure on a real 90-minute lecture: cost (estimate ~$0.40-0.60) and time.
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
- [~] 90-minute lecture transcribes on the GPU; CPU fallback works. Measured on the fixture:
  GPU ~15x real time, CPU 1.6x (fallback tested); real 90-minute timing still to report.
- [x] All seven output types generated; every note section, card and question links to a
  timestamp that seeks the audio.
- [x] Chat answers cite sources; grounded mode says when something isn't in the material.
- [ ] Everything works offline with Ollama selected. Ollama + bge-m3 (search) installed and
  verified; an offline LLM model (e.g. qwen3) is not installed yet, so offline generation/chat
  is untested.
- [x] Tests run the pipeline end-to-end on the fixture with the mock provider; README covers
  setup incl. CUDA/cuDNN on Windows.

## Decisions (from SPEC §13, 2026-10-08)

- LLM: tiered Claude (Sonnet for notes/cards/questions/chat, Haiku for cheap passes), Ollama offline.
- Generated material is always English; German terms kept in brackets. Per-subject override.
- Slide capture (Phase 2): Zoom window, fallback to a chosen monitor.
- Data folder: `%USERPROFILE%\LectureLab` (C: has ~30 GB free, so the recorder keeps a tight disk budget).
- Name: LectureLab. Mic: off by default, per-recording toggle.
- Audio capture: `soundcard` (WASAPI loopback, live default-device lookup, Windows-side resampling)
  instead of PyAudioWPatch, whose PortAudio device list is frozen at init.
- Subjects: one per course (VL/VO/UE of the same course share a subject; lecture `kind` records which).
