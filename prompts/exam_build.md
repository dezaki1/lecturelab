---
version: 1
---
Write a written exam from the study notes above. The student sits it under exam conditions: {{duration}} minutes, no notes.

- {{topics}} Cover them in proportion to how much the notes teach about them; give ★ sections and exam hints extra weight.
- Total points: about {{points}}. Size the tasks so a well-prepared student can finish in {{duration}} minutes (reading and writing time included).
- {{mix}}
- Difficulty: {{difficulty}}.
- Every question needs points, a model `answer`, an `explanation` and a grading `rubric` whose points add up to the question's points. Make rubric criteria checkable ("states that the eigenvectors are orthogonal", "correct value 2/3 with working"), so partial credit can be given.
- mcq: one correct option and plausible distractors, with an explanation for every option. multi: several correct options. tf: two options, "True" and "False".
- Calculation and code tasks use new numbers, data or settings, not the lecture's own examples.
- Questions must stand on their own (no "as in the lecture"), and must not overlap.
- Set `topic` to the course topic each question tests, and cite the passage it is based on in `refs` (lecture number and the timestamps shown in the note headings).
- `title`: a few words naming what the exam covers.

Write the questions in {{output_language}}; keep German terms in brackets where the notes do.
