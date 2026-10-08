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
- Quit via the tray icon (**Quit**) so an in-progress recording is finalised cleanly.

## Importing recordings

Drag audio or video files (mp4, m4a, mp3, wav, webm, …) anywhere onto the LectureLab window, or
use **Import** on a subject page. Pick the subject, adjust titles, and import. Only the audio is
kept (as `audio.m4a`); your original files are left untouched. The lecture date is taken from
the file's metadata.

## Transcripts

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
90-minute lecture with Claude Sonnet 5.5. For offline use, pick **Ollama** in Settings (install
Ollama and `ollama pull qwen3:8b`); quality is lower.

Hotkeys and other settings can be changed in `%USERPROFILE%\LectureLab\config.toml`.

## Development

```powershell
uv run pytest; uv run ruff check .
cd frontend; npm run dev        # hot-reloading UI on :5173 (run the backend separately)
```

See [CLAUDE.md](CLAUDE.md) for the folder map and conventions.

## Privacy

Recordings never leave your machine. The server binds to `127.0.0.1`. Only LLM API calls go out,
and none when Ollama is selected. Recording lectures may need the lecturer's permission.
