---
version: 1
---
Find the moments in this lecture where the lecturer signals that something is important for the exam or for understanding the course. These become "exam hints" for the student.

Look for:

- explicit exam references: "this will be in the exam", "typical exam question", "you should be able to derive this", "das kommt in der Prüfung", "prüfungsrelevant";
- explicit emphasis: "remember this", "this is the key idea", "make sure you understand", "das ist wichtig", "merken Sie sich";
- points the lecturer repeats or comes back to several times (`repeated`; cite the first time and mention the repetition in `topic`).

Rules:

- Only real signals from the lecturer. Not every definition is a hint; ordinary teaching is not. A short lecture may have none.
- `start`/`end` are timestamps copied from the transcript and cover the flagged explanation (not just the signal phrase).
- `topic` says what exactly to know, e.g. "Derive the PCA objective as an eigenvalue problem".
- `quote` is the lecturer's signalling words, verbatim and short.
- Order hints by time.

{{candidates}}
