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
- [ ] **M6 Search & chat**
- [ ] **M7 Review & practice**
- [ ] **M8 Jobs & costs**

## Decisions (from SPEC §13, 2026-10-08)

- LLM: tiered Claude (Sonnet for notes/cards/questions/chat, Haiku for cheap passes), Ollama offline.
- Generated material is always English; German terms kept in brackets. Per-subject override.
- Slide capture (Phase 2): Zoom window, fallback to a chosen monitor.
- Data folder: `%USERPROFILE%\LectureLab` (C: has ~30 GB free, so the recorder keeps a tight disk budget).
- Name: LectureLab. Mic: off by default, per-recording toggle.
- Audio capture: `soundcard` (WASAPI loopback, live default-device lookup, Windows-side resampling)
  instead of PyAudioWPatch, whose PortAudio device list is frozen at init.
- Subjects: one per course (VL/VO/UE of the same course share a subject; lecture `kind` records which).
