# LectureLab

A personal, local-first study app. It records Zoom lectures (or imports recordings), transcribes
them, and turns them into study material organised by subject: summaries, notes, flashcards,
exercises and exam questions, each linked back to the exact moment in the lecture.

> Status: Phases 1-3 done (recording, transcription, study material, study engine, automation).
> See [PROGRESS.md](PROGRESS.md). Built for my own studies at JKU Linz; Windows only.

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
- LectureLab records **all system audio** (every app's sound, i.e. Zoom), plus your mic if you
  enable it. It takes the sound directly from the apps, so it records even when the laptop is
  muted or at volume 0, and whichever speakers or headphones you use. (On Windows older than
  10 version 2004 it records the default output device instead, which some drivers silence at
  volume 0. Set `system_capture = "device"` under `[recorder]` in `config.toml` to force that.)
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

If the timetable has a *different* course at the recording's time, the lecture page asks
whether it belongs there: **Move to …** (also fine while it is still being transcribed; it
moves before the notes are written) or **It belongs here**.

### Two lectures at the same time: record the second one on a tablet

On the Galaxy Tab (or any Android device), join the second Zoom there and use the built-in
**Screen recorder** (quick settings): set *Sound* to **Media sounds** (not the microphone)
and a lower *Video quality* (720p is plenty and keeps files small). Afterwards copy the
video to the PC (USB, cloud folder) or upload it straight from the tablet: with *Tablet &
phone access* paired, open the subject page there and use **Import**. Slides are taken from
the video automatically; the date comes from the file, so the timetable check above catches a
file imported into the wrong course.

### New Moodle recordings automatically

For courses that put their recordings on Moodle (Opencast videos), the browser extension can
fetch new ones by itself: open the course on Moodle, click the extension and **Watch this
course** (pick the subject; the type comes from the recording's title, or set VL/UE).

- The first check finds what is already online and lists it on the **Overview** to pick from
  (anything that looks like a lecture you already have is unticked). Everything uploaded after
  that is downloaded straight away into the right subject, notes and cards included.
- Checks run hourly, and every 15 minutes in the six hours after a timetable slot of the
  course. **Settings → Moodle recordings** shows each course's last check and has **Check now**.
- The browser does the looking, since only it is logged in to Moodle: Chrome or Edge has to be
  open (Edge: *Settings → System → Continue running background extensions* covers closed
  windows). If the Moodle login expired, Settings says so; log in again and it continues.
- Recordings are matched by their Opencast id, so nothing is downloaded twice, including links
  you sent with the extension before. If you recorded a lecture yourself, its Moodle version
  isn't downloaded but offered on the Overview ("You recorded this yourself"), in case your
  recording has gaps.

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

**Half price** (Settings, on by default): notes, cards, questions and course overviews go through
Anthropic's batch service at 50% of the price. They're usually ready a few minutes later than
otherwise (rarely up to an hour); chat, exam grading and oral practice stay instant. While Claude
works on one lecture's notes, the next lecture is already being transcribed.

**Offline mode:** pick **Ollama** in Settings (requires [Ollama](https://ollama.com) with
`ollama pull qwen3:8b`). Nothing leaves your computer. On a 4 GB GPU the 8B model runs mostly
on the CPU: chat answers take ~30-40 s, which is fine, but generating study material is very
slow (~40 min for a 2-minute test lecture) and noticeably less accurate. Use offline mode for
chat and as a fallback, and let Claude generate the study material. `[llm] ollama_model` in config.toml can point to a smaller model (e.g.
`qwen3:4b`, which fits in 4 GB of VRAM) for speed.

## Exam simulator

**Exam simulator** (sidebar): pick a subject, the duration, question types, topics (with your
mastery shown) and difficulty, and **Build exam**. Questions come from the lectures' exam
questions, favouring weak topics and ones you haven't answered; if there aren't enough, new
ones are written in the style of the course's past exams. The exam is timed (the timer keeps
running if you leave the page; it submits itself when time is up) and answers save as you type.
Choice questions are marked automatically; open answers are graded by Claude against the rubric,
with partial credit, feedback per answer, links to the lecture moment and topics to revise
(about $0.02-0.10 per exam). Results stay in the history with a score trend.

**Past exams**: add old exam PDFs on the simulator page (scans work: they are read with OCR).
Each is analysed once (questions, types, points, topics, style), and both the simulator and the
lectures' generated exam questions then match their format, style and topic weighting.

## Weak spots

Each subject has a **Weak spots** tab (and the Overview shows the weakest topics overall). A
topic's strength combines how well you will still remember its cards in a week, how often you
forgot them in reviews, and your scores on its practice and exam questions over the last 60
days; topics you haven't practised are "untested", not weak. Open **What went wrong** to see
the cards you keep forgetting and the answers that lost points. **Practice my weak topics**
writes new questions aimed at exactly those mistakes (about $0.03-0.06 with Claude) and opens
them on the Practice page.

## Sleep

While LectureLab records or has work queued, Windows doesn't go to sleep after inactivity (on
battery that would otherwise happen after a few minutes). During a recording the screen stays on
too, for slide capture; during processing it may turn off. Closing the lid or choosing Sleep
still sleeps the laptop and pauses LectureLab, so keep the lid open until the queue is done.
Turn it off with `keep_awake = false` in `config.toml`.

## Start without PowerShell

Run once:

```powershell
uv run lecturelab shortcut
```

This puts a **LectureLab** shortcut on the Desktop and in the Start menu (pin it to the taskbar
if you like). It starts LectureLab without a terminal window, updates the interface if the code
changed, and opens it in its own app window (Edge, no tabs or address bar). Clicking it while
LectureLab is running just opens the window again. Quit from the tray icon (**Quit**), which also
stops a running recording safely. Startup problems show a message; details are in
`logs\launcher.log`. `uv run lecturelab shortcut --remove` removes the shortcuts.
Set `app_window = false` in `config.toml` to use a normal browser tab instead.

## Backup

**Settings → Backup**: choose a folder (a folder inside OneDrive syncs to the cloud) and turn on
automatic backups (daily by default; **Back up now** any time). Each backup writes a dated
snapshot of the database and settings (the newest per day for a week and per week for a month
are kept) and copies new or changed lecture files (audio, slides, PDFs, transcripts); files
you delete in LectureLab stay in the backup. Whisper models, temp files and your API key are
not included.

Restore into a new data folder (then start LectureLab with `--data-dir` pointing at it):

```powershell
uv run lecturelab restore "C:\Users\you\OneDrive\LectureLab backup" --to "C:\Users\you\LectureLab-restored"
```

## Timetable

**Settings → Timetable**: paste your calendar link (KUSSS: *Kalender → Abonnieren*, an
`https://` or `webcal://` link, re-read every 6 hours) or upload an exported `.ics` file.
Courses are matched to your subjects by course number or name (the LVA number and type such as
VL/UE are ignored); fix a match with the dropdown, or switch a course off (office hours, Mensa).
Then:

- **Overview** shows today's lectures, the running one marked *now*.
- Starting a recording without picking a subject (hotkey, tray, or the panel, which preselects
  it) uses the course that is on (from 10 minutes before it starts) and its type (VL/UE).
- A reminder pops up 2 minutes before each lecture (configurable) with a **Record** button.
- **Record automatically when Zoom is open during a lecture** (off by default): starts recording
  when a Zoom meeting window is open during a slot and stops once the slot is over and Zoom has
  been closed for a minute. Each slot is recorded at most once automatically.

## Oral practice: teach-back and oral exam

**Oral practice** (sidebar). Answer by voice (**Speak**, then check the transcript and fix
misheard words) or by typing (Ctrl+Enter sends).

- **Teach-back**: pick a topic (weak ones are marked ●) and explain it as if teaching a
  classmate. You get a score, what you covered, what's missing and what was wrong, each linked
  to the lecture moment, plus one follow-up question about the biggest gap. Your answer to it
  is judged together with the first explanation.
- **Oral exam**: pick a subject (and optionally topics) and how many questions. The examiner
  asks one question at a time, read aloud, and follows up when an answer is thin. It doesn't
  give away answers during the exam. At the end (or **End exam**) you get an Austrian grade
  (1-5), feedback per question, gaps and misconceptions with lecture links, and what to revise.
  With Claude a 3-question exam costs a few cents.

The examiner's voice is generated locally by Piper: download one in **Settings → Voice**
(about 60 MB; English US/UK or German), test it, and set the speed. Spoken answers are
transcribed locally by Whisper. The model loads with the first answer (about 10 s), then each
answer takes about a second or two, and it unloads after 5 idle minutes. While a lecture is being
transcribed, answers use the CPU. Voice answers need the microphone, which browsers only allow
on this PC or on a trusted address (see *Tablet & phone access*). Typing always works.

## Live lecture

**Live lecture** (sidebar, or the link in the recorder panel while recording): the lecture as text
about 15-20 seconds behind the lecturer, transcribed on your GPU by the same Whisper model as
spoken answers. Switch it on per recording with **Live transcript**, or for every new recording
on the page when nothing is recording. It starts from the last half minute when switched on
mid-lecture. Quick questions (**What did they just say?**, **Explain the last part more
simply**, an example, the formula, or your own question) are answered from the last 10 minutes
in a few seconds and cite the moments they used. It also works on the tablet (Tablet & phone
access). The full, accurate transcript is still made from the recording after it stops.

## Evals

**Evals** (sidebar, bottom): check that search and the generated material stay good when
prompts or settings change.

- **Search questions**: **Make 20 more from my notes** writes search questions from sections of
  your notes; the section's place in the lecture is the right answer. Approve the realistic
  ones, fix the wording, reject the rest. **Run search eval** (free, no AI) reports how often
  the right moment is the first hit, in the top 5 and in the top 10 (hit@1/5/10, MRR).
- **Grounding**: **Run grounding eval** has the cheap model check a fixed sample of 40 flashcards
  and exam questions against the transcript passage each one cites (supported / partly /
  unsupported / cites nothing), for about $0.01.
- Each run records the prompt versions in use; the history shows the change against the previous
  run and which prompts changed in between. Also from a terminal: `lecturelab eval make`,
  `lecturelab eval retrieval`, `lecturelab eval grounding`.

## Handwritten notes

The **My notes** tab of a lecture: **Upload PDF or photos** (Samsung Notes or GoodNotes exports,
scans, photos of paper; several at once) or **Take a photo** on the tablet. Claude reads each
page into Markdown with formulas in LaTeX and drawings as short [sketch: ...] descriptions;
unreadable words are marked [?] and listed. Correct a page with **Correct**. Your notes are
searchable and are added to the lecture's material for the next generation (regenerate to use
them). About $0.01 per page with Claude. With a local model, pages go through local OCR, which
reads handwriting poorly.

## Audio recaps

**Audio recaps** (sidebar), or the **Audio recap** tab of a lecture: a spoken summary of one
lecture or of a week (all subjects, or one), about 5-15 minutes, to listen to on the way. The
AI writes a spoken script from your notes: the main ideas in order, what the lecturer flagged
for the exam, a wrap-up, and self-test questions with a pause before each answer. Your local
Piper voice reads it (a 7-minute recap takes about half a minute on the CPU). With Claude it
costs about $0.03-0.06.

The player has chapters (each links to the lecture moment), speeds up to 1.75×, the script,
and **.m4a** download. The file has chapter marks, so podcast and music apps show them. On a
phone or tablet (Tablet & phone access) the lock screen and headphones can pause and skip
chapters. Turn on **Make a recap of every new lecture automatically** to get one after each
lecture's study material is ready. Making a new recap of the same lecture or week replaces
the old one.

## Plan: deadlines and the study planner

**Plan** (sidebar) has three parts:

- **Deadlines.** Each lecture's announcements (assignments, deadlines, exam logistics, dated
  admin items) show up under *From your lectures: to review*, linked to the moment they were
  said. **Accept** one (fix the date first if needed), **Dismiss** it, or for exam info
  **Set as exam date** on the subject. You can also add your own. A regenerated lecture never
  brings back what you dismissed.
- **Study plan.** A day-by-day revision plan up to each exam, built from your exam dates,
  your weak topics and the cards due. It has a daily review block and topic blocks: the
  weakest topics first, and studied topics come back after 2, 4, 7, then 14 days. Two study
  days before an exam there's a mock exam, and the day before, the cheat sheet. Set your time
  per day and your study days below the plan. Tick blocks off as you go (also on Overview).
  The plan rebuilds itself each day and whenever exam dates, deadlines or settings change.
- **Add to calendar.** Download a `.ics` (exams, accepted deadlines with reminders, one study
  event per day at your chosen time) and import it into Outlook or Google Calendar, or copy
  the subscription link for Outlook desktop or Thunderbird so it stays up to date. Events keep
  stable IDs, so importing again updates them instead of duplicating them.

## Tablet & phone access

Review cards, read notes and tick off your plan on a tablet or phone on the same Wi-Fi while
the PC runs LectureLab.

1. **Settings → Tablet & phone access → Allow devices on my network.** The first time, Windows
   may ask whether Python can use the network: allow it on **private networks**.
2. Scan the QR code with the tablet's camera (or open the link shown). The device is now paired
   and opens `http://<your PC>:8770/` directly from then on.
3. In Chrome: **⋮ → Add to home screen** for an app icon.

Only paired devices get in. **New pairing code** unpairs every device. Settings, API keys,
starting or stopping recordings and calendar links stay PC-only. The connection is plain
HTTP: fine at home, don't turn it on in public Wi-Fi. LAN mode stays on across restarts
until you switch it off.

*Full-screen app with offline start-up (optional):* browsers only install apps from secure
addresses. On the tablet, open `chrome://flags/#unsafely-treat-insecure-origin-as-secure`, add
`http://<your PC's address>:8770` (shown in Settings), enable it and relaunch Chrome. After that,
"Add to home screen" installs LectureLab as an app.

### Away from home (Tailscale)

Tablet access only works on the same network as the PC. To use it anywhere, install
[Tailscale](https://tailscale.com) (free) on the PC and the tablet and sign in with the same
account: **Settings → Tablet & phone access** then shows an *Away from home* pairing code with the
PC's Tailscale address. Only your own devices can reach it, encrypted; nothing is opened to the
internet. If the tablet loses its internet while Tailscale is on, turn off *Use Tailscale DNS* in
the Tailscale app. Speaking answers (microphone) needs a secure connection, which this isn't.

## Exports and the cheat sheet

- **Obsidian vault**: **Obsidian** on a subject page (or **Settings → Export** for everything)
  downloads a zip of Markdown notes: one note per lecture (summary, notes, exam hints,
  explanations, glossary, flashcards for the Spaced Repetition plugin, exam questions with
  collapsible answers), a course overview with topics as wikilinks to lecture sections and the
  concept map as a Mermaid diagram, the glossary and the cheat sheet. Timestamps link back to
  LectureLab.
- **PDF**: **PDF** on a lecture page; the cheat sheet has its own PDF button. PDFs are printed
  by Microsoft Edge in the background (no extra software); without Edge or Chrome the print
  view opens so you can use "Save as PDF".
- **Cheat sheet**: the **Cheat sheet** tab on a subject page writes a dense 1-2 page sheet for
  the last revision before the exam (formulas, definitions, algorithms, exam hints ★), printed
  in two columns. Edit it like any note, or rewrite it with extra instructions (about
  $0.05-0.15 with Claude).

## Subject hub

Each subject page has tabs next to the lecture list:

- **Course overview**: the course so far and a recap per lecture, updated automatically after
  each lecture gets its study material (about $0.05-0.10 per update with Claude; **Update** to
  run it by hand).
- **Topics**: the course's topics with the lectures and note sections that teach them and your
  **mastery** per topic (recall of the topic's flashcards today), sortable "weakest first".
- **Concept map**: the key ideas and how they relate; click a concept to open its lecture.
- **Glossary**: every term of every lecture in one searchable list, with the German term.
- **Exam hints** and **Materials** (all PDFs of the subject; add scripts or exercise sheets here).

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

## Use and recordings

LectureLab is meant for personal study. Lecture recordings and slides belong to the lecturers
and the university; the app keeps them on your own computer and shares nothing. Follow your
university's rules on recordings: record or download only lectures you are allowed to, ask the
lecturer when in doubt, and don't redistribute recordings or material generated from them. The
Moodle/Opencast features only fetch what your own login can already watch.

## License

[MIT](LICENSE). The license covers the code, not lecture recordings or material you make with it.
