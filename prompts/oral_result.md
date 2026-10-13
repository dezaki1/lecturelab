---
version: 1
---
The oral exam of "{{subject}}" is over. Evaluate the whole conversation against the lecture notes above, like a fair university examiner.

- `questions`: one entry per main question (with its follow-ups): the question, a 0-10 score, a short comment on what was good or missing, and `refs` to where the lecture covers it (lecture number + `[mm:ss]` from the notes).
- `score` 0-100 overall and `grade` on the Austrian scale: 1 Sehr gut (90+), 2 Gut (80+), 3 Befriedigend (65+), 4 Genügend (50+), 5 Nicht genügend.
- `strengths`, `gaps` (key points they couldn't explain, with refs) and `misconceptions` (what they said wrongly, the correction, refs).
- `summary`: 3-5 sentences in Markdown, honest and encouraging, with what to work on first; `revise`: topics to revise, most important first.

Answers come from speech recognition: ignore filler words and obvious mishearings. Judge only what the student said.

{{conversation}}
