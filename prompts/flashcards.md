---
version: 2
---
Write spaced-repetition flashcards for this lecture. Aim for roughly {{min}}-{{max}} cards, depending on how much there is to learn; fewer is fine for a light lecture.

- One fact per card. If a question needs "and" to ask for two things, make two cards. The question must make sense on its own months later, so include the context ("In PCA, ...", "In k-means, ...").
- No yes/no cards. Mix definition cards with why/how/compare cards.
- Use cloze cards (`type: "cloze"`, with one {{c1::...}} deletion in `front`) for definitions and formulas; use a second deletion only when both parts form one inseparable fact. Split lists into separate cards rather than asking for a whole list.
- Use `reversed` only when both directions are worth learning (term <-> definition).
- Answers are short: one fact or one sentence, not a paragraph.
- Tags: 1-3 short topic tags. Difficulty: easy, medium or hard. Cite the source passage in `refs`.

Bad: "What is PCA?" answered with a whole paragraph.
Good: "In PCA, what do the eigenvectors of the covariance matrix represent?" -> "The directions of maximum variance (the principal components)."
Good (cloze): "PCA projects the data onto the eigenvectors with the {{c1::largest eigenvalues}}."
{{existing_cards}}
