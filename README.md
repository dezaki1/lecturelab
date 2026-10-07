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
