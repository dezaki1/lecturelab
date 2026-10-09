---
version: 1
---
The student bookmarked the passages below while listening because they were confused or had a question. For each bookmark write an "Explain this part" section.

The bookmark time is when the student pressed the key, usually a little after the confusing statement, so look at the transcript shortly before it (the excerpt is given) and use the whole lecture for context. If the bookmark has a note, answer that note.

For each bookmark:

- `title`: what the passage is about, in a few words.
- `explanation`: explain it again more simply than the lecturer did: intuition first, then the precise statement in the lecture's notation. Fill gaps in the lecturer's explanation, marking anything beyond the lecture with "**Added context:**".
- `example`: one small concrete example (numbers, a tiny dataset, a short code snippet) that makes it click.
- `check_question` and `check_answer`: one question the student can answer if they understood, with a short model answer.
- `refs`: the passage that was confusing.

Return one explanation per bookmark id.

{{bookmarks}}
