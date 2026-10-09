# LectureLab

A personal, local-first study app. It records Zoom lectures (or imports recordings), transcribes
them, and turns them into study material organised by subject: summaries, notes, flashcards,
exercises and exam questions, each linked back to the exact moment in the lecture.

> Status: Phase 1 in progress. See [PROGRESS.md](PROGRESS.md).

## Setup (Windows)

Requirements: [uv](https://docs.astral.sh/uv/), Node.js 20+, and ffmpeg on `PATH`
(`winget install Gyan.FFmpeg`).

```powershell
git clone <this repo>; cd <repo>
uv sync                         # creates .venv with Python 3.12
.\start.ps1                     # builds the frontend on first run, starts the app, opens the browser
```

### GPU transcription (NVIDIA, Windows)

`uv sync` installs faster-whisper plus NVIDIA's cuBLAS and cuDNN 9 (CUDA 12) as Python wheels
(~2 GB) inside `.venv`, so no system-wide CUDA Toolkit or cuDNN install is needed. The
transcription process adds `.venv\Lib\site-packages\nvidia\*\bin` to its DLL search path itself.
You only need a recent NVIDIA driver (`nvidia-smi` should work).

The Whisper model (`large-v3-turbo`, ~1.6 GB) downloads to `%USERPROFILE%\LectureLab\models`
on first use. If the GPU run fails (old driver, out of VRAM, no NVIDIA GPU) LectureLab falls
back to the CPU automatically and shows that on the lecture page. Measured on an RTX A1000
(4 GB): ~15x real time on GPU, ~1.6x on CPU (i7-12700H). Settings are under `[transcription]`
in `config.toml` (e.g. `device = "cpu"`, `cpu_model = "small"`, `models_dir = "D:/models"`).

Optional, to pre-create this semester's subjects:

```powershell
uv run lecturelab seed-subjects scripts\subjects_ws2026.json
```

The app listens on `http://127.0.0.1:8765` only. Data is stored in `%USERPROFILE%\LectureLab`
(change with `LECTURELAB_DATA_DIR` or `--data-dir`); settings are in `config.toml` there.

## Recording lectures

- **Record**: pick the subject in the sidebar panel and press **Record**, or use the tray icon
  menu, or press **Ctrl+Shift+F9** anywhere (starts with the last-used subject; press again to stop).
- **Bookmarks** while recording: **Ctrl+Shift+F10** = important, **Ctrl+Shift+F11** = confused
  (also buttons in the panel / tray). Clicking a bookmark later plays from 5 s before it.
- LectureLab records **all system audio** (whatever you hear, i.e. Zoom), plus your mic if you
  enable it. It follows the default output device when you switch headphones/speakers.
- Audio is written to disk every few seconds. If the app or PC crashes, the recording up to that
  point is recovered automatically the next time you start LectureLab.
- When you stop, the recording is encoded to AAC (`audio.m4a`, ~30 MB per 90 min).
- **Slides**: while recording, LectureLab looks at the Zoom meeting window every 2 s and saves a
  picture whenever the slide changes (speaker videos are ignored; it works even when other
  windows cover Zoom). Toggle it with the slides button next to the mic. Slide text is read
  locally (RapidOCR) and becomes searchable; slides appear in the **Slides** tab, above the
  transcript lines and next to the matching note sections. Use **Settings → Slide capture →
  Test capture** during a Zoom meeting to check it finds the window, or pick a monitor to
  capture when slides are shown somewhere else.
- Quit via the tray icon (**Quit**) so an in-progress recording is finalised cleanly.

## Slide PDFs

On a lecture's **Slides** tab, **Add PDF** attaches the slide deck (e.g. from Moodle). Each page
is placed at the moment it was discussed, using the transcript and any captured slides; pages
that belong to other lectures are left out, so you can add the same chapter PDF to several
lectures. PDF pages then replace the captured screenshots in the transcript and notes (switch
between the two on the Slides tab), and their text is used for search, chat and study material.
PDFs without a text layer (scans) can't be placed.

## Importing recordings

Drag audio or video files (mp4, m4a, mp3, wav, webm, …) anywhere onto the LectureLab window, or
use **Import** on a subject page. Pick the subject, adjust titles, and import. Only the audio is
kept (as `audio.m4a`) plus one picture per slide found in a video; your original files are
left untouched. The lecture date is taken from the file's metadata.

## Transcripts

After transcription, a quick pass with the cheap Claude model fixes misheard technical terms
(e.g. "eigen vectors" → "eigenvectors", "numb pie" → "NumPy") using the slide text and the
subject's vocabulary. It never rephrases. **Show changes** on the Transcript tab shows each fix
as a diff; undo single lines or all of them, or **Check again** after adding a PDF or vocabulary.
Costs about $0.01-0.03 per lecture. Not run automatically with local models.


Every recording and import is transcribed automatically (Whisper, on the GPU when available).
The lecture page shows the transcript next to the player: the current sentence is highlighted
and followed, clicking a line plays from there, `/` searches, Space plays/pauses, ←/→ skips 5 s.
Add technical terms to a subject's **Vocabulary** so Whisper spells them correctly. A subject's
language can be forced (English/German) and any lecture can be re-transcribed from its page.

## Study material (AI)

After transcription, LectureLab generates for each lecture: a summary, structured study notes,
a glossary (with German terms), announcements/deadlines, flashcards, practice exercises with
hints and worked solutions, and exam-style questions with rubrics. Everything links back to the
moment in the lecture it came from, and a cheap second pass flags cards/questions that the cited
passage doesn't support. Notes can be edited in place (old versions are kept), and any section
can be regenerated with extra instructions.

Setup: open **Settings**, paste an Anthropic API key (stored in Windows Credential Manager). The
lecture page shows a cost estimate before generating, typically a few tens of cents for a
90-minute lecture with Claude Sonnet 5.5.

**Offline mode:** pick **Ollama** in Settings (requires [Ollama](https://ollama.com) with
`ollama pull qwen3:8b`). Nothing leaves your computer. On a 4 GB GPU the 8B model runs mostly
on the CPU: chat answers take ~30-40 s, which is fine, but generating study material is very
slow (~40 min for a 2-minute test lecture) and noticeably less accurate. Use offline mode for
chat and as a fallback, and let Claude generate the study material. `[llm] ollama_model` in config.toml can point to a smaller model (e.g.
`qwen3:4b`, which fits in 4 GB of VRAM) for speed.

## Subject hub

Each subject page has tabs next to the lecture list:

- **Course overview**: the course so far and a recap per lecture, updated automatically after
  each lecture gets its study material (about $0.05-0.10 per update with Claude; **Update** to
  run it by hand).
- **Topics**: the course's topics with the lectures and note sections that teach them and your
  **mastery** per topic (recall of the topic's flashcards today), sortable "weakest first".
- **Concept map**: the key ideas and how they relate; click a concept to open its lecture.
- **Glossary**: every term of every lecture in one searchable list, with the German term.
- **Exam hints** and **Materials** (all PDFs of the subject; add scripts or exercise sheets here,
  and **past exams**, see below).

## Cards and the dashboard

**Overview** shows what to review today, your streak, 30-day retention, the next exam, review
activity, a 7-day forecast and, per subject, cards (new / learning / mature) and **mastery**:
how much of the deck you would recall today (FSRS recall probability, unseen cards count as 0).

**Cards** lists every flashcard with filters (subject, state, search, sort by most forgotten),
editing (text, type, tags), suspend, reset and delete, also for many cards at once.
**Duplicates** finds near-identical cards within a subject; merging keeps the card you pick,
adds the others' lecture links and tags, and keeps the review progress of the most-practised one.
**Export** writes an Anki deck (`.apkg`, one subdeck per lecture, math rendered by Anki, a link
back to the lecture moment on every card; importing again updates the same notes) or a CSV.
Anki starts exported cards as new; review progress stays in LectureLab.

## Exam hints and confused moments

When the lecturer says something like "this will be in the exam", "remember this" or "das ist
wichtig", or keeps coming back to a point, LectureLab marks it: a ★ in the transcript, an
**Exam hints** tab per lecture, and an exam-prep list of all hints on each subject page. The
notes mark these points ★ and cards and exam questions cover them first. (A keyword search finds
them right after transcription, even offline; generating study material checks them and adds a
one-line "what to know".)

Every **confused** or **question** bookmark gets an *Explain this part* section on the Bookmarks
tab: a simpler explanation, a small example and a check question, which you can keep as a
flashcard. Bookmarks added later get one with **Explain this part**.

## Review and practice

**Review** (sidebar, with a badge for cards due) runs a daily spaced-repetition session across
all subjects (FSRS): Space shows the answer, 1-4 rates it (Again/Hard/Good/Easy), U undoes.
New cards are introduced 30 per day by default (`[study] new_cards_per_day` in config.toml).
**Practice** quizzes you on the generated exam questions or exercises; multiple choice is graded
automatically, open questions by ticking the rubric points you got.

## Past exams and the exam simulator

**Add past exam** on a subject's Materials tab takes an old exam PDF (scans work: pages without
a text layer are read with the local OCR). Claude describes its format, working time, points,
style and topic weighting; from then on the subject's exam questions are written to match it,
and the exam simulator sizes its exams like the real one.

**Exam simulator** (sidebar): pick a subject, topics (none = the whole course), duration,
question types (none = like the past exams) and difficulty. **Write a new exam** has Claude write
fresh questions from your notes of those topics (about a minute); **Use my question bank** picks
from the questions generated with your lectures, unseen and weakest first (instant, offline).
The clock starts with **Start** and keeps running if you close the tab; answers save as you type,
you can flag questions, and the exam is handed in automatically when time is up. Multiple choice
is marked at once; Claude grades open answers against each question's rubric with partial
credit, comments per criterion and feedback, and each question links to the lecture moment it
is based on. Results (score, an approximate Austrian grade, score per topic) stay in the history
and count towards your practice stats. Rough cost with Claude Sonnet: a few tens of cents to
write and grade a 90-minute exam.

## Jobs, notifications and costs

Recording, import, transcription, study material and search indexing run as background jobs,
one at a time, and resume after a restart. **Jobs** (sidebar) shows progress and errors, with
Retry and Cancel; a red dot means something failed recently. When a lecture is fully
processed you get one Windows notification ("Lecture ready"). **Usage** shows estimated API
costs per day, task, model, subject and lecture, and how much prompt caching saved. If you
transcribed lectures before adding an API key, **Jobs → Generate all** processes them, with a
cost estimate first.

## Search and chat

Press **Ctrl+K** to search all lectures (keywords and meaning, across English and German).
**Ask your lectures** (sidebar) chats over everything or one subject; the **Ask** button on a
lecture or subject page starts a chat scoped to it. Answers cite moments like `[L5 · 34:12]`;
click one to jump there. With "Only from my lectures" on, the chat says when something isn't
covered and labels any general-knowledge answer.

Semantic search uses the `bge-m3` model in [Ollama](https://ollama.com) (`ollama pull bge-m3`).
Without Ollama running, search falls back to keywords only.

Hotkeys and other settings can be changed in `%USERPROFILE%\LectureLab\config.toml`.

## Development

```powershell
uv run pytest; uv run ruff check .
cd frontend; npm run dev        # hot-reloading UI on :5173 (run the backend separately)
```

See [CLAUDE.md](CLAUDE.md) for the folder map and conventions.

## Privacy

Recordings and slide pictures never leave your machine. The server binds to `127.0.0.1`. Only
LLM API calls go out, and none when Ollama is selected. Recording lectures may need the lecturer's permission.
