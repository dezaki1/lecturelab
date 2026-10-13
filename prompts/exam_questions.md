---
version: 1
---
Write {{n}} new exam questions for a practice exam of "{{subject}}", worth about {{points}} points in total, based on the lecture notes above.

- Topics: {{topics}}
- Question types to use: {{types}}
- Difficulty: {{difficulty}}
{{style}}
Rules:

- Test what the lectures taught, in the lecturer's notation. Each question must be answerable from the lectures; cite the passage in `refs` (timestamps copied from that lecture's notes) and give the lecture number in `lecture`.
- Exam-ready: unambiguous wording, a complete model `answer`, an `explanation`, and a grading `rubric` whose points add up to the question's points. For mcq/multi/tf give options with an explanation for each.
- Don't repeat these existing questions: {{existing}}
