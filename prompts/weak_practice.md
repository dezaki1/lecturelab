---
version: 1
---
The student is revising "{{subject}}" and keeps getting the topics below wrong. Write {{n}} new practice questions aimed at exactly these weaknesses, based on the lecture notes above.

{{weak}}

- Target the specific misunderstandings shown by the forgotten cards and wrong answers: ask about the same ideas from a different angle, contrast the concepts they confuse, and include one or two questions that build up step by step from the basics.
- Mix question types ({{types}}); prefer short answers and calculations that make the student produce the idea, with a few multiple-choice questions whose distractors are the actual mistakes.
- Medium difficulty overall; don't make them harder than the exam.
- Each question must be answerable from the lectures: cite the passage in `refs` (timestamps from that lecture's notes) and give the lecture number in `lecture` and the topic name in `topic`.
- Give a complete model `answer`, an `explanation` that addresses the likely mistake, and a `rubric` whose points add up to the question's points.
- Don't repeat these existing questions: {{existing}}
{{style}}
