---
version: 1
---
The student is practising the Feynman technique: they explain "{{topic}}" from the course "{{subject}}" in their own words, as if teaching a classmate. Judge the explanation against the lecture notes above, which are the source of truth.

- `covered`: the key points they got right, in a few words each.
- `missing`: the important points of this topic in the notes that they left out, most important first. Each has `refs` to where the lecture covers it (lecture number and the `[mm:ss]` timestamp from the notes). Leave out minor details.
- `misconceptions`: anything wrong, confused or imprecise (wrong definition, swapped terms, a formula that is off). Quote or paraphrase what they said and give the correction, with refs.
- `score` 0-100: correctness and completeness for the core of the topic. A clear, correct explanation of the essentials scores 80+ even if details are missing. Wrong statements cost more than omissions.
- `follow_up`: one probing question that makes them think about the biggest gap (not a hint that gives the answer away). Empty if nothing important is missing.
- `feedback`: 2-4 sentences, concrete and encouraging. Point out what to look at first.

Spoken explanations come from speech recognition: ignore filler words, false starts and misheard words that are obviously meant as a course term. Judge only what they said, not what they might know.

{{conversation}}
