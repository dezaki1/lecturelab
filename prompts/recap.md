---
version: 1
---
Write the script of a spoken audio recap of {{scope}} for a student who listens on the way to university. It is read aloud by text-to-speech, about {{minutes}} minutes long (roughly {{words}} words in total).

Style:
- Like a friendly, clear tutor talking to one student. Natural spoken English, short sentences, contractions are fine. No Markdown, no lists, no bullet characters, no URLs, no emoji, no parentheses for asides.
- Say formulas and symbols in words ("x hat equals the sum over i of w i times x i"). Spell out abbreviations the first time ("principal component analysis, P C A").
- Follow the notes faithfully: the lecturer's terms, definitions and notation. Don't add facts the notes don't support. Give the German term once where the notes have one.

Structure, as `sections` (each becomes a chapter):
1. A short intro: what this covers and why it matters (2-3 sentences).
2. The main ideas in the lecture's order, one section per topic. Explain intuition first, then the precise definition or method, with one concrete example where the notes have one. Point out what the lecturer stressed or flagged for the exam ("the lecturer said this will be in the exam").
3. {{closing}}
4. A final section with three or four quick self-test questions. After each question say "Pause and think." then give the short answer.

Use `refs` to point each section to the lecture moments it covers (the lecture number in [#n] and the [mm:ss] timestamp from the notes). Spend the time on the important material; skip administrative announcements.

{{hints}}
