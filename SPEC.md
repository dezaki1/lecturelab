# LectureLab — build spec for Claude Code

*Working name, rename freely.* A personal, local-first study app. It records my Zoom lectures (or imports recordings), transcribes them, and turns them into study material organized by subject. It also lets me chat with an AI about everything I've recorded. Every note, flashcard, question and chat answer links back to the exact moment in the lecture where it came from.

## 0. How to work on this (read first)

1. Read this whole file before doing anything. Don't write code yet.
2. Ask me the open questions in **§13** first, as multiple choice where possible.
3. Propose the architecture and a Phase 1 task list (milestones M1–M8 in §6), then wait for my OK.
4. Build **only Phase 1**, one milestone at a time, as working vertical slices. After each milestone, make sure tests and lint pass, tick the milestone off in `PROGRESS.md`, `git commit`, and tell me how to try it.
5. Create `CLAUDE.md` early (how to run, test and lint; a folder map; conventions) and keep it up to date.
6. Phases 2–3 are the roadmap. Don't build them yet, but design the data model and module boundaries so they fit in later without rewrites. Put new ideas in `BACKLOG.md` instead of building them.
7. Check the current docs instead of guessing library APIs or model IDs (faster-whisper, Anthropic SDK, Ollama, sqlite-vec, fsrs).
8. Ask me before adding heavy dependencies, changing the stack, or downloading multi-GB models.
9. Tests must make no real API calls. Use a mock LLM provider and a short fixture lecture.

## 1. Context

- I'm a final-year BSc Artificial Intelligence student at JKU Linz (Austria). My lectures are on Zoom, in English (some courses may be in German). My English is stronger than my German.
- This semester is heavy and my exams are in late January, so getting a **usable MVP quickly** matters more than polish. **The recorder comes first**, so I don't miss lectures while the rest is being built.
- Machine: Windows laptop (Dell Precision 5570) with an i7-12700H, 32 GB RAM and an **NVIDIA RTX A1000 with 4 GB VRAM**, plus two external monitors. Ollama is already installed. I use PowerShell.
- I also have an Android tablet (Galaxy Tab S7 with a pen) and I use **TickTick** for tasks.

## 2. Core workflow

1. **Capture.** I click Record (or press a global hotkey) and pick the subject. The app records Zoom's audio, and I press hotkeys to mark moments as *important* or *confused*. I can also drag in an existing recording instead.
2. **Process.** This runs automatically in the background: transcribe → clean up → generate study material → index for search. A Windows notification tells me when the lecture is ready.
3. **Study.** Each lecture has a page with the summary, notes, transcript with synced audio, flashcards, exercises and exam questions. Every item links to its timestamp, and clicking it plays that moment.
4. **Ask.** I chat with the AI about one lecture, one subject or everything, and get clickable source citations.
5. **Revise.** A daily spaced-repetition review across all subjects, practice quizzes, and later a full exam simulator.

## 3. Tech stack (defaults; challenge them if you have a better reason)

| Area | Default |
|---|---|
| Backend | Python 3.12, FastAPI, Pydantic v2, SQLModel/SQLAlchemy, SQLite (WAL mode) + Alembic |
| Env & tooling | `uv` with a project-local `.venv` (kept separate from my ML conda envs), ruff, pytest |
| Jobs | A persistent job table in SQLite with one background worker (no Redis or Celery). Jobs resume after a restart. Progress is pushed over SSE |
| Recording | WASAPI loopback for system audio (PyAudioWPatch or `soundcard`) plus an optional mic. ffmpeg for encoding |
| Tray & hotkeys | `pystray` + `keyboard` or `pynput`. Global hotkeys are configurable and avoid clashing with Zoom's own shortcuts |
| Transcription | `faster-whisper` with the `large-v3-turbo` model, running on CUDA with `int8_float16` on the 4 GB GPU. VAD on, word-level timestamps. Fallback: CPU `int8` or a smaller model |
| LLM | A provider interface with **Anthropic Claude** (default), **Ollama** (local/offline) and any OpenAI-compatible endpoint. The model for each task is set in config |
| Search | Local multilingual embeddings (`bge-m3` via Ollama or sentence-transformers), `sqlite-vec` and SQLite FTS5, combined as hybrid search with reciprocal rank fusion |
| Spaced repetition | FSRS via the `fsrs` package |
| Frontend | React + TypeScript + Vite, Tailwind + shadcn/ui, TanStack Query. Markdown rendering with KaTeX math and code highlighting. Mermaid for concept maps |
| Run | `start.ps1` starts everything with one command: the backend serves the built frontend on `127.0.0.1`, opens the browser and starts the tray icon |
| Secrets | API keys go in Windows Credential Manager via `keyring` (or a gitignored `.env`), and are never logged |

## 4. Architecture

```
lecturelab/
├─ backend/
│  ├─ api/          FastAPI routers
│  ├─ core/         config, db, migrations, job queue, events (SSE)
│  ├─ capture/      recorder, bookmarks/hotkeys, tray, (later) slide grabber
│  ├─ pipeline/     stages: ingest → transcribe → clean → generate → index
│  ├─ ai/           providers, prompt loader, output schemas, usage/cost tracking
│  ├─ study/        FSRS scheduling, quizzes, grading, (later) exam simulator
│  ├─ search/       embeddings, hybrid retrieval, chat
│  └─ exports/      markdown/obsidian, pdf, anki, ics
├─ prompts/         editable prompt templates (*.md), versioned
├─ frontend/
├─ tests/           incl. fixtures/ (~2-min sample lecture: audio + transcript)
└─ data/            default data dir (configurable, gitignored)
```

- **Pipeline stages are idempotent and can be re-run one at a time** (for example "regenerate flashcards only"). A failed stage never deletes earlier outputs. Generated outputs are versioned, and my manual edits are never silently overwritten.
- **Each lecture gets its own folder**: `data/subjects/<subject>/<YYYY-MM-DD>_<slug>/`, holding the audio, `transcript.json` (segments + word timings), slides and exports. Metadata, generated items and review state are stored in the database.
- A 90-minute lecture is roughly 15–20k tokens of transcript. That means generation and chat for a single lecture can put **the whole transcript in context**. Use **prompt caching** so the summary, notes, cards and question calls can share that transcript cheaply. Subject-wide features should use retrieval and the per-lecture notes, not the raw transcripts.
- Structured outputs (cards, questions, note sections) are produced via JSON schema or tool use, validated with Pydantic, and retried automatically if the output is invalid.
- Only one GPU job runs at a time. Unload the Whisper model after use so the VRAM is free again.

## 5. Data model (starting point)

- **Subject**: name, course code, language, lecturer, colour, exam date, custom instructions, vocabulary list (used for transcription and cleanup)
- **Lecture**: subject, title, date, duration, source (recording or import), media path, per-stage status, cost
- **TranscriptSegment**: start, end, text, speaker (optional), confidence
- **Bookmark**: lecture, time, kind (important / confused / question), optional note
- **Slide**: lecture, time, image, OCR text, source (screen capture or PDF page). *Phase 2, but plan for it now.*
- **Material**: subject or lecture, type (slides PDF, past exam, notes), file, extracted text
- **Note**: belongs to a lecture or a subject; kind (summary, study notes, glossary, announcements, cheat sheet, concept map), Markdown, version, model, prompt version, edited-by-me flag
- **Flashcard**: type (basic / cloze / reversed), front, back, tags, source refs, FSRS state, suspended
- **Question**: type (MCQ, multi-select, true/false, short, long, calculation, code), Bloom level, difficulty, points, options, answer, explanation, rubric, source refs
- **ReviewLog / Attempt**: card or question, rating or score, time taken, timestamp
- **ChatThread / ChatMessage**: scope (lecture / subject / all), citations
- **Job** and **UsageRecord** (provider, model, task, tokens, estimated cost)

`source_refs` is a list of `{lecture_id, start_s, end_s}` or `{material_id, page}` entries. Everything generated must carry them.

## 6. Phase 1: MVP (build in this order)

- **M1 Skeleton.** Repo, config, database and migrations, `start.ps1`, and an app shell with a subjects sidebar. Subjects can be created, edited and deleted.
- **M2 Recorder (top priority).**
  - Start and stop from the UI, the tray menu or a global hotkey, and pick the subject.
  - Capture system audio via WASAPI loopback, with an optional mic mixed in. Follow default-device changes (for example, headphones connecting).
  - Gotcha: loopback delivers no frames while nothing is playing. Pad those gaps with silence so the recording timeline matches wall-clock time.
  - Write to disk in chunks every few seconds, so a crash or power loss loses almost nothing. Check free disk space before starting.
  - Show a live level meter and a clear "recording" indicator.
  - Bookmark hotkeys (*important*, *confused*) store timestamps.
  - When recording stops, encode to compressed audio: Opus or AAC, whichever plays in all browsers, including mobile.
- **M3 Import.** Drag and drop audio or video files (mp4, m4a, mp3, wav, webm). Extract the audio with ffmpeg.
- **M4 Transcription.**
  - Run faster-whisper with progress reporting.
  - Auto-detect the language, with a per-subject override. Pass the subject's vocabulary to Whisper as `initial_prompt` / hotwords.
  - Transcript view with a synced player: the current line is highlighted, clicking a line seeks to it, and I can search inside the transcript.
  - Serve media with HTTP range support so seeking works.
- **M5 Generation.**
  - Input: the transcript, my bookmarks and the subject's instructions.
  - Outputs: summary, study notes, glossary, announcements, flashcards, exercises and exam questions (rules in §9).
  - The lecture page shows these in tabs. I can regenerate any section with optional extra instructions, and edit the notes in place.
- **M6 Search & chat.**
  - Index transcript chunks (about 60–90 s each, with timestamps) and note sections. Add a global search.
  - Chat can be scoped to a lecture, a subject or everything, and streams its answers (§10).
  - Answers include citation chips like `[L5 · 34:12]`. Clicking one opens the lecture and seeks the player to that point.
- **M7 Review & practice.**
  - A daily review queue across all subjects, using FSRS. Keys: Space flips the card, 1–4 rate it Again/Hard/Good/Easy.
  - A practice mode for questions. MCQs are graded automatically. For open questions I compare my answer with the model answer and rate myself.
- **M8 Jobs & costs.**
  - A job list with progress and a retry button.
  - A Windows notification when a lecture is ready.
  - A usage page showing tokens and estimated cost per lecture and per task.

**Phase 1 is done when:**

- [ ] One hotkey records a 90-minute Zoom lecture. If the app is killed partway through, there is still a playable recording up to that point.
- [ ] A 90-minute lecture transcribes on my GPU (report the actual speed), and the CPU fallback works.
- [ ] All seven output types are generated. Every note section, card and question links to a timestamp that seeks the audio.
- [ ] Chat answers cite their sources. In grounded mode, the chat clearly says when something isn't in my material.
- [ ] Everything works offline with Ollama selected (lower quality is acceptable).
- [ ] In the tests, the pipeline runs end-to-end on the fixture with the mock provider. The README covers setup, including CUDA/cuDNN on Windows.

## 7. Phase 2: study engine & richer capture

- **Slides.**
  - During recording, take a screenshot of the Zoom window or a chosen monitor every few seconds. Keep a frame only when the slide has changed (perceptual hash or SSIM).
  - OCR the slides locally (e.g. RapidOCR) or with a vision model.
  - Import lecture PDFs (e.g. from Moodle) and match each page to the right part of the transcript by text similarity.
  - Show slide thumbnails inline in the notes and the transcript.
- **Transcript cleanup pass.** Fix misheard technical terms using the slide text and the subject vocabulary. Keep the raw version and show the diff.
- **Subject hub.** One place per subject with:
  - a running summary that updates after each lecture
  - a cumulative glossary (English, with the German terms)
  - a concept map across lectures
  - a topic list with mastery levels
  - all materials
- **"Confused" bookmarks.** Each one gets an *Explain this part* section: a simpler explanation, an example and a check question.
- **Exam-hint detector.** Flag moments where the lecturer signals importance ("this will be in the exam", "remember this", "das ist wichtig", points they repeat). Mark them ★ in the notes and give them more weight when generating questions.
- **Exam simulator.**
  - I pick topics, duration, question mix and difficulty, then sit a timed exam.
  - The AI grades it using the rubric, with partial credit, feedback and links to the source.
  - Results are kept as a history.
- **Past exams.** Upload old exam PDFs for a subject. The generator then matches their format, style and topic weighting.
- **Weak spots.** Track accuracy per topic from reviews and quizzes. A "Practice my weak topics" button generates new questions aimed at those topics.
- **Card management.** Browse, edit and suspend cards, and merge duplicates. Export to Anki (`.apkg` via `genanki`) and CSV.
- **Dashboard.** Cards due today, streak, retention, mastery per subject, and days left until each exam.
- **Exports.**
  - A Markdown/Obsidian vault with wikilinks
  - PDF
  - a 1–2 page cheat sheet per subject

## 8. Phase 3: automation & extras

- **Timetable.**
  - Import an `.ics` file or link (from KUSSS if it offers an export, or from my calendar). New recordings are then assigned to the right subject automatically.
  - The tray shows a reminder before each lecture: "‹Subject› starts in 2 min. Record?"
  - Optionally, recording starts automatically when a Zoom meeting window appears during a scheduled slot.
- **Deadlines & announcements.** Collect them into a list I can review, then push them to TickTick (via its Open API) or export them as `.ics`.
- **Study planner.** Use exam dates, topics and mastery to build a day-by-day revision plan, and push it to TickTick or my calendar.
- **Oral exam practice.** The AI examiner asks questions out loud (TTS) and I answer by voice (Whisper). It asks follow-up questions and gives scored feedback.
- **Teach-back mode.** I explain a concept in my own words, and the AI points out gaps and misconceptions.
- **Audio recaps.** A 5–10 minute spoken summary per lecture or per week, to listen to while commuting. Use local TTS like Piper, or a cloud TTS.
- **Tablet/phone access.** An opt-in LAN mode protected by a token, with a responsive PWA for reviewing cards and reading notes on the Tab S7.
- **Live mode.** A near-real-time transcript during the lecture, plus quick questions like "what did they just say?".
- **Handwritten notes.** Import PDFs from the tablet, OCR them with a vision model and attach them to the lecture.
- **Backup.** A scheduled zip of the data folder to a folder I choose (for example, a cloud-synced one).
- **Evals.** A small hand-checked set for measuring retrieval hit rate and grounding. Rerun it whenever the prompts change.
- *Optional:*
  - Speaker diarization, so in-class student questions and answers become an FAQ.
  - Capturing only Zoom's audio via Windows per-process loopback, if feasible.
  - Notion export.

## 9. Study-content quality rules (bake these into `prompts/`)

**For all outputs**

- **Grounded.** Content comes from my material and carries `source_refs`. Anything added from general knowledge to clarify a point is labelled **Added context** and styled differently.
- **Faithful.** Keep the lecturer's notation, definitions and terminology. Don't silently "fix" what the lecturer said. Flag garbled or doubtful passages instead ("⚠ unclear at 41:20").
- Math in LaTeX, code in fenced blocks, algorithms as numbered steps or pseudocode.
- The output language is set per subject (default English). For German lectures: write in English, keep the key German terms in brackets, and add them to the glossary.
- The amount of content scales with how dense the lecture is (with a configurable min/max). Never use fixed counts.
- Each subject's custom instructions are appended to the prompt (for example, "math-heavy: show derivations step by step").

**Per output type**

- **Summary.** A TL;DR of 5–8 bullets, plus "If you remember only 3 things".
- **Study notes.**
  - Follow the lecture's structure, with one heading per topic and `[mm:ss]` links.
  - Include definitions, formulas with every symbol explained, intuition, worked examples and common mistakes.
  - Add ★ callouts for what the lecturer emphasised.
  - Link to earlier lectures ("builds on L3: …").
  - End with open questions to look up.
- **Glossary.** For each term: definition, German term, and the timestamp of its first mention.
- **Announcements.** Deadlines, exam info, assignments and admin, with dates. Keep these out of the content notes.
- **Flashcards.**
  - One idea per card, and the question makes sense on its own ("In k-means, …").
  - No yes/no cards. Mix definition cards with why/how/compare cards.
  - Use cloze cards for definitions and formulas. Split lists into separate cards.
  - Dedupe against the subject's existing cards. Add topic tags and a difficulty.
  - ✗ "What is PCA?" → answered with a whole paragraph
  - ✓ "In PCA, what do the eigenvectors of the covariance matrix represent?" → "The directions of maximum variance (the principal components)."
  - ✓ Cloze: "PCA projects the data onto the eigenvectors with the {{c1::largest eigenvalues}}."
- **Exercises.** Similar to the lecture's examples but with new numbers or settings. Include hints, and step-by-step solutions that stay hidden until I reveal them. For programming courses, add small coding tasks with the expected output.
- **Exam questions.**
  - Mix the types:
    - MCQ with plausible distractors and an explanation for each option
    - short answer
    - long answer
    - calculation/derivation
    - code
  - Spread them across Bloom levels.
  - Give each question points, a model answer and a grading rubric.
  - Give extra weight to ★ topics and match the style of past exams.
- **Verification pass.** A cheap second pass checks each card and question against its cited source, and drops or flags any that aren't supported.

## 10. Chat behaviour

- A scope picker (this lecture / this subject / everything) and a date filter.
- For a single lecture, put the full transcript and notes in context. For a subject or everything, use hybrid retrieval over transcripts, notes, slides and materials (reranking is optional).
- Every claim cites its sources as clickable chips. In grounded mode (the default), if something isn't in my material, the chat says so, then offers a general-knowledge answer that is clearly labelled as such.
- Quick actions on any answer: *Make flashcards*, *Quiz me on this*, *Explain simpler*, *Give an example*.
- Chat history is saved per subject.

## 11. Non-functional

- **Local-first and private.** All data stays on my disk, and the server is bound to `127.0.0.1`. Only LLM API calls leave the machine, and none at all when Ollama is selected.
- **Robust.** Recording is crash-safe, queued jobs resume after a restart, and errors are shown clearly with a retry option.
- **Responsive.** Heavy work runs outside the request thread or process, so the UI never freezes during transcription.
- **Cost control.** Model chosen per task, prompt caching, cached embeddings, and a cost estimate shown before big batch jobs.
- **Windows-first.** PowerShell scripts, `pathlib`, and paths with spaces handled. Automate or document the CUDA + cuDNN DLL setup for faster-whisper, and fall back to the CPU cleanly.
- **Code quality.** Type hints and ruff. Tests for the pipeline stages, schemas, retrieval and FSRS scheduling.
- **Docs.** A README with setup steps, an architecture diagram and screenshots, so the repo can double as a portfolio project.
- **UI.** Clean, calm and readable (think Notion or Obsidian), with dark mode, and keyboard-first for review.

## 12. Recording etiquette (built into the app)

Recording a lecture may need the lecturer's OK, and classmates may be audible on the recording. Show a one-time reminder for each subject, keep recordings local, and don't build any feature that shares raw recordings.

## 13. Ask me before you start

1. **LLM default.** The Claude API gives the best quality but needs an Anthropic API key, which is billed separately from my Claude subscription. Ollama would be the offline fallback. Is that OK, and which model should handle which task?
2. **Note language.** Always English, or the same language as the lecture?
3. **Slide capture (Phase 2).** Capture the Zoom window, or a whole monitor?
4. **Data folder.** Is the default `%USERPROFILE%\LectureLab` OK?
5. **Subjects.** I'll list this semester's courses so you can set them up.
6. **Name.** Keep "LectureLab"?
