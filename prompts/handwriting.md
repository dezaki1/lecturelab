---
version: 1
---
This is page {{page}} of {{pages}} of the student's handwritten notes for the lecture above. Transcribe it.

- `markdown`: everything written on the page, in reading order, as Markdown. Keep the student's structure: headings for titles they underlined or boxed, lists for bullet points, numbered steps where they numbered. Math in LaTeX ($...$ inline, $$...$$ for formulas on their own line), matrices and fractions included. Arrows that connect ideas as "→".
- Transcribe faithfully: their words, abbreviations and language (German stays German). Don't correct, complete or explain anything, and don't add content from the lecture topics: those are only there to help you decode hard-to-read technical terms.
- Drawings, plots and diagrams: one line `[sketch: what it shows, with its labels]`.
- Write `[?]` for a word you can't read and list it in `unclear` (with its surrounding words). Crossed-out text is left out.
- `summary`: what the page is about, in a few words (e.g. "PCA derivation and an example").
- An empty or unreadable page: empty `markdown` and say so in `summary`.
