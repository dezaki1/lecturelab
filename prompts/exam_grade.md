---
version: 1
---
Grade each student answer above. Return one grade per item id.

- With a rubric: for every criterion (by its index) award 0 up to its maximum points. Give partial points when a criterion is partly met. Add a one-sentence comment per criterion. `score` is the sum.
- Without a rubric: judge against the model answer and set `score` (0 up to the item's points).
- The model answer is a guide, not the only correct answer: credit a different correct approach, notation or wording. The lecture excerpt shows what was taught; use it to judge correctness.
- No credit for content the answer doesn't contain, for statements that contradict themselves, or for vague answers that only repeat the question. Wrong statements next to right ones can cost the criterion they contradict.
- `feedback`: 2-4 sentences addressed to the student ("You…"): what was right, what was missing or wrong, and the key idea they need. Use Markdown and LaTeX where helpful.
- `overall`: a short verdict on the open answers as a whole, the strengths, and the 2-4 things to revise before the real exam.
