# Backlog

Ideas that came up while building. Not scheduled; Phase 2/3 items live in SPEC.md.

- Subject delete with lectures: offer "move lectures to another subject" instead of refusing.
- Archive subjects at semester end (the `archived` flag exists; hide them from the sidebar).
- Course codes / lecturers / exam dates could be pulled from a KUSSS export.
- Auto-title lectures from the generated summary (M5), e.g. "Policy gradients & REINFORCE".
- Optional: a small always-on-top recording pill (time + bookmark buttons) for the Zoom monitor.
- Per-process loopback (Zoom only) is in SPEC Phase 3; current capture records all system audio,
  so mute notification sounds during lectures.
- Transcription speed: try faster-whisper's BatchedInferencePipeline (2-4x faster) if VRAM allows.
- Word-level karaoke highlight in the transcript (word timings are already in transcript.json).
- Inline transcript corrections (keep raw + edited, like the Phase 2 cleanup diff).
- Cost: use the Message Batches API (50% off) for non-urgent overnight generation.
- Show a per-lecture cost breakdown (usage_record already stores it; M8 adds the page).
- Card/question inline editing (Phase 2 card management).
- Search snippets: render light Markdown/LaTeX instead of raw `**`/`$` characters.
- Offline generation: try qwen3:4b (fits in 4 GB VRAM) and compare speed/quality; maybe
  generate only summary + notes + flashcards offline and leave questions for Claude.
- "Lecture ready" notification click could open the lecture (pystray balloons can't; would
  need a WinRT toast library).
- Chat: optional reranking with the cheap model for subject/all scope (SPEC 10 says optional).
- Chat: show the retrieved sources under each answer (they are streamed as a `sources` event).
- Slides: collapse progressive builds (bullet-by-bullet reveals) into the final slide; mark
  revisited slides (same dhash) instead of storing them again.
- Slides: a "share area only" crop (Zoom's shared-content region) would drop the toolbar and
  participant strip from saved images.
- Slides: send slide images to Claude (vision) for formula-heavy slides where OCR is rough.
- PDF placement: add bge-m3 embedding similarity for cross-language decks (German slides,
  English speech); upload one course PDF to the subject and place pages across all lectures.
- Cleanup: optional Sonnet for cleanup on lectures with many low-confidence lines.
- Cards: remember "not duplicates" decisions; AnkiConnect live sync instead of file export;
  optionally export FSRS state into Anki's scheduling.
- Dashboard: per-lecture weak cards ("most forgotten this week") linking into a filtered review.
