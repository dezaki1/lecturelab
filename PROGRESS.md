# Progress

## Phase 1 (MVP)

- [x] **M1 Skeleton**: uv project, config, SQLite (WAL) + Alembic, job queue, SSE bus, subjects CRUD, app shell, `start.ps1`
- [ ] **M2 Recorder**
- [ ] **M3 Import**
- [ ] **M4 Transcription**
- [ ] **M5 Generation**
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
