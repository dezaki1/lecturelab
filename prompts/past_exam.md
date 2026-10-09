---
version: 1
---
The text above is an old exam of "{{subject}}" (extracted from a PDF; scanned pages were read with OCR, so expect some noise). Describe it so that new practice exams can copy its format and style.

- `title`, `date`, `duration_min`, `total_points`, `aids`: as printed on the paper (0 or empty if not given).
- `format`: how the paper is built: number of tasks and sub-tasks, sections, how answers are expected (prose, derivation, code, ticking boxes).
- `style`: 3-8 concrete observations a question writer should copy, e.g. "every task starts with a short scenario", "calculations use small integer matrices", "theory questions ask to compare two methods", "MCQ with 4 options, wrong answers cost points".
- `topics`: the course topics the exam covers, with the points on each (0 if the paper prints no points).
- `questions`: every task and sub-task, with its type, points and topic, and the task as written (shorten long setups, keep formulas in LaTeX). Leave out cover-page instructions.

Use English for your descriptions; keep the questions in the paper's language.
