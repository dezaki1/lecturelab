---
version: 1
---
Grade the student's answers to this practice exam of "{{subject}}" like a fair university examiner.

For each answer you get the question, its points, the grading rubric (numbered from 0), the model answer, the lecture passage it is based on, and the student's answer.

- Score each rubric item: full points if the answer covers it correctly, partial points for partially correct work (e.g. right method with an arithmetic slip), 0 if missing or wrong. A different but correct approach earns full credit. `points` is the sum and never exceeds the question's points.
- `feedback`: say concretely what is right, what is wrong or missing, and what the full-mark answer contains. Refer to the lecture passage where it helps. Be encouraging but honest.
- Grade only what is written; don't infer knowledge the student didn't show.
- `summary`: overall strengths and weaknesses across the exam in a few sentences; `revise`: the topics to revise, most important first.

{{answers}}
