---
version: 2
---
Check study items against the lecture transcript. Each item below has an id, the item text (with its answer or worked solution), and the transcript excerpt it cites.

For each item decide whether it is supported:

- Flashcards and exam questions about lecture content: supported if the cited excerpt (or the transcript as a whole) backs the item and its answer as written.
- Practice exercises and calculation/code questions deliberately use new numbers, data or settings. They are supported if the method or concept they practise is taught in the lecture and the given solution and answer are correct. Check the arithmetic and the code.

Mark an item unsupported if it states something the lecture does not say, contradicts the lecture, or has a wrong answer or solution. Minor wording differences, and standard background knowledge needed to phrase a question, are fine. For unsupported items, say briefly what is wrong. Return one verdict per item id.

{{items}}
