"""Deterministic offline provider for tests and UI work. Never calls a network.

It reads the "[mm:ss] text" transcript lines from the context and builds schema-valid output
that cites real timestamps, so the whole pipeline (refs, rendering, verification) is exercised.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from itertools import pairwise
from typing import Any

from backend.ai.types import ChatEvent, ChatRequest, LLMRequest, LLMResult, Usage

LABEL = re.compile(r"\[((?:[A-Za-z][\w ]*? )?L\d+(?: · \d+:\d{2}(?::\d{2})?)?)\]")
LINE = re.compile(r"^\[(\d+:\d{2}(?::\d{2})?)\] (.+)$", re.M)
ITEM_ID = re.compile(r"^- id=(\d+)", re.M)


class MockProvider:
    name = "mock"
    max_parallel = 3

    def model_for(self, task: str, tier: str) -> str:
        return f"mock-{tier}"

    def complete(self, req: LLMRequest) -> LLMResult:
        lines = LINE.findall(req.context) or [("00:00", "Empty lecture.")]
        builder = getattr(self, f"_{req.output.__name__}")
        parsed = req.output.model_validate(builder(lines, req))
        tokens = len(req.context) // 4
        usage = Usage(
            input_tokens=len(req.instruction) // 4, output_tokens=500, cache_read_tokens=tokens
        )
        return LLMResult(parsed, self.model_for(req.task, req.tier), self.name, usage)

    def chat_stream(self, req: ChatRequest) -> Iterator[ChatEvent]:
        """Echoes the question and cites the first source it was given."""
        material = req.context + "\n".join(m["content"] for m in req.messages)
        labels = re.findall(r'label="([^"]+)"', material) or LABEL.findall(material)
        scope = re.search(r"Cite this lecture as \[L(\d+) ", req.context)
        lines = LINE.findall(req.context)
        if scope and lines:  # lecture-scoped chat: cite the second transcript line
            labels = [f"L{scope.group(1)} · {lines[min(1, len(lines) - 1)][0]}"]
        question = req.messages[-1]["content"].strip().splitlines()[-1]
        if labels:
            answer = f"Mock answer to: {question} See [{labels[0]}]."
        else:
            answer = "Your lectures don't cover this. **General knowledge:** mock answer."
        for word in answer.split(" "):
            yield ChatEvent("text", word + " ")
        usage = Usage(input_tokens=len(material) // 4, output_tokens=30)
        yield ChatEvent("done", model="mock-main", usage=usage)

    @staticmethod
    def _ref(lines: list[tuple[str, str]], i: int) -> dict[str, str]:
        i = min(i, len(lines) - 1)
        return {"start": lines[i][0], "end": lines[min(i + 1, len(lines) - 1)][0]}

    def _pick(self, lines: list[tuple[str, str]], n: int) -> list[int]:
        step = max(1, len(lines) // max(n, 1))
        return list(range(0, len(lines), step))[:n]

    def _SummaryOut(self, lines, req) -> dict[str, Any]:  # type: ignore[no-untyped-def]
        idx = self._pick(lines, 6)
        bullets = [{"text": lines[i][1], "refs": [self._ref(lines, i)]} for i in idx]
        return {"topic_title": "Mock lecture topic", "tldr": bullets, "remember": bullets[:3]}

    def _NotesOut(self, lines, req) -> dict[str, Any]:  # type: ignore[no-untyped-def]
        idx = self._pick(lines, 3)
        return {
            "sections": [
                {
                    "heading": f"Topic {k + 1}",
                    "markdown": f"{lines[i][1]}\n\n$$x^2 + y^2 = r^2$$",
                    "refs": [self._ref(lines, i)],
                    "emphasized": k == 0,
                }
                for k, i in enumerate(idx)
            ],
            "open_questions": ["What happens in higher dimensions?"],
        }

    def _GlossaryOut(self, lines, req) -> dict[str, Any]:  # type: ignore[no-untyped-def]
        return {
            "terms": [
                {
                    "term": "Principal component",
                    "definition": "A direction of maximum variance.",
                    "german": "Hauptkomponente",
                    "first_mention": lines[0][0],
                }
            ]
        }

    def _AnnouncementsOut(self, lines, req) -> dict[str, Any]:  # type: ignore[no-untyped-def]
        return {
            "items": [
                {
                    "kind": "deadline",
                    "text": "Assignment due",
                    "date": "2026-10-20",
                    "at": lines[-1][0],
                }
            ]
        }

    def _FlashcardsOut(self, lines, req) -> dict[str, Any]:  # type: ignore[no-untyped-def]
        qa = [
            ("In PCA, what do the eigenvectors of the covariance matrix represent?",
             "The directions of maximum variance."),
            ("Why must the data be centered before PCA?",
             "Otherwise the first component points towards the mean."),
            ("In k-means, how is each point assigned?", "To the nearest centroid."),
            ("What do the eigenvalues tell us in PCA?",
             "How much variance each component explains."),
        ]  # fmt: skip
        idx = self._pick(lines, len(qa))
        cards = [
            {
                "type": "basic",
                "front": front,
                "back": back,
                "tags": ["mock"],
                "difficulty": "medium",
                "refs": [self._ref(lines, i)],
            }
            for (front, back), i in zip(qa, idx, strict=False)
        ]
        cards.append(
            {
                "type": "cloze",
                "front": "PCA projects onto the eigenvectors with the {{c1::largest eigenvalues}}.",
                "back": "",
                "tags": ["mock"],
                "difficulty": "easy",
                "refs": [self._ref(lines, 0)],
            }
        )
        return {"cards": cards}

    def _ExercisesOut(self, lines, req) -> dict[str, Any]:  # type: ignore[no-untyped-def]
        return {
            "exercises": [
                {
                    "title": "Variance by hand",
                    "kind": "calculation",
                    "prompt": "Compute the variance of $\\{1, 2, 3\\}$.",
                    "hints": ["Center the data first."],
                    "solution_steps": [
                        "Mean is 2.",
                        "Squared deviations: 1, 0, 1.",
                        "Variance = 2/3.",
                    ],
                    "answer": "2/3",
                    "expected_output": "",
                    "difficulty": "easy",
                    "refs": [self._ref(lines, 0)],
                }
            ]
        }

    def _QuestionsOut(self, lines, req) -> dict[str, Any]:  # type: ignore[no-untyped-def]
        return {
            "questions": [
                {
                    "type": "mcq",
                    "bloom": "understand",
                    "difficulty": "medium",
                    "points": 2,
                    "prompt": "What do the eigenvectors of the covariance matrix represent?",
                    "options": [
                        {
                            "text": "Directions of maximum variance",
                            "correct": True,
                            "explanation": "By definition.",
                        },
                        {
                            "text": "Cluster centroids",
                            "correct": False,
                            "explanation": "That's k-means.",
                        },
                    ],
                    "answer": "Directions of maximum variance",
                    "explanation": "PCA finds them.",
                    "rubric": [{"criterion": "Correct option", "points": 2}],
                    "emphasized": True,
                    "refs": [self._ref(lines, 0)],
                },
                {
                    "type": "short",
                    "bloom": "remember",
                    "difficulty": "easy",
                    "points": 1,
                    "prompt": "Name the first step of PCA.",
                    "options": [],
                    "answer": "Center the data.",
                    "explanation": "Subtract the mean.",
                    "rubric": [{"criterion": "Mentions centering", "points": 1}],
                    "emphasized": False,
                    "refs": [self._ref(lines, 1)],
                },
            ]
        }

    def _StandaloneQuery(self, lines, req) -> dict[str, Any]:  # type: ignore[no-untyped-def]
        q = req.instruction.split("Latest question:", 1)[-1].split("Return a short", 1)[0]
        return {"query": q.strip()}

    def _Ping(self, lines, req) -> dict[str, Any]:  # type: ignore[no-untyped-def]
        return {"ok": True}

    def _HintsOut(self, lines, req) -> dict[str, Any]:  # type: ignore[no-untyped-def]
        """Confirms the keyword candidates, else flags the middle line as repeated."""
        found = re.findall(r'^- \[(\d+:\d{2}(?::\d{2})?)\] "(.*)"$', req.instruction, re.M)
        if found:
            return {
                "hints": [
                    {"start": t, "end": t, "kind": "exam", "topic": f"Know: {q[:40]}", "quote": q}
                    for t, q in found
                ]
            }
        i = len(lines) // 2
        ref = self._ref(lines, i)
        return {
            "hints": [
                {**ref, "kind": "repeated", "topic": lines[i][1][:60], "quote": lines[i][1][:40]}
            ]
        }

    def _SubjectHubOut(self, lines, req) -> dict[str, Any]:  # type: ignore[no-untyped-def]
        """One topic per note section heading, one concept per lecture, chained in order."""
        lectures = re.findall(r'^## L(\d+) "(.*)"', req.context, re.M)
        sections = re.findall(r"^Note sections: (.*)$", req.context, re.M)
        topics, concepts = [], []
        for (n, title), secs in zip(lectures, sections, strict=False):
            for head in [h.strip().lstrip("★ ") for h in secs.split("|") if h.strip()]:
                topics.append(
                    {
                        "name": head,
                        "summary": f"{head} from L{n}",
                        "places": [{"lecture": int(n), "sections": [head]}],
                        "exam_relevant": head.startswith("Topic 1"),
                    }
                )
            concepts.append({"id": f"c{n}", "label": title, "lecture": int(n)})
        relations = [
            {"source": a["id"], "target": b["id"], "label": "leads to"}
            for a, b in pairwise(concepts)
        ]
        return {
            "overview": f"Mock overview of {len(lectures)} lecture(s).",
            "recaps": [{"lecture": int(n), "recap": f"Recap of {t}."} for n, t in lectures],
            "topics": topics,
            "concepts": concepts,
            "relations": relations,
        }

    def _PastExamOut(self, lines, req) -> dict[str, Any]:  # type: ignore[no-untyped-def]
        """One question per non-empty line that starts with a number, e.g. "1) Derive ..."."""
        found = re.findall(
            r"^(\d+[a-z]?)[).:] (.+?)(?: \((\d+) ?(?:P|pts|points)\))?$", req.context, re.M
        )
        return {
            "title": "Mock past exam",
            "duration_min": 90,
            "total_points": float(sum(int(p or 0) for *_, p in found)),
            "questions": [
                {
                    "number": n,
                    "type": "calculation" if "comput" in t.lower() else "short",
                    "points": float(p or 0),
                    "topic": t.split()[1] if len(t.split()) > 1 else t,
                    "text": t,
                }
                for n, t, p in found
            ],
            "style_notes": "Derivations by hand; short justifications.",
        }

    def _SimQuestionsOut(self, lines, req) -> dict[str, Any]:  # type: ignore[no-untyped-def]
        n = int(re.search(r"Write (\d+) new (?:exam|practice) questions", req.instruction).group(1))  # type: ignore[union-attr]
        lecture = int((re.findall(r'^## L(\d+) "', req.context, re.M) or ["1"])[0])
        stamp = (re.findall(r"\[(\d+:\d{2})\]", req.context) or ["00:05"])[0]
        return {
            "questions": [
                {
                    "type": "short",
                    "bloom": "understand",
                    "difficulty": "medium",
                    "points": 4.0,
                    "prompt": f"New mock exam question {i + 1}: explain the main idea.",
                    "options": [],
                    "answer": "The main idea is the mock answer.",
                    "explanation": "Because the lecture said so.",
                    "rubric": [
                        {"criterion": "States the main idea", "points": 2.0},
                        {"criterion": "Explains why", "points": 2.0},
                    ],
                    "emphasized": False,
                    "refs": [{"start": stamp, "end": stamp}],
                    "lecture": lecture,
                    "topic": "Mock topic",
                }
                for i in range(n)
            ]
        }

    def _ExamGradeOut(self, lines, req) -> dict[str, Any]:  # type: ignore[no-untyped-def]
        """Full marks for answers containing "correct", half otherwise; over-awards get clamped."""
        blocks = re.split(r"(?m)^- answer_id=", req.instruction)[1:]
        answers = []
        for b in blocks:
            aid = int(b.split(" ", 1)[0])
            student = b.split("Student answer:", 1)[1]
            items = re.findall(r"^    (\d+)\) .* \(([\d.]+) pts\)$", b, re.M)
            good = "correct" in student.lower()
            rubric = [
                {"item": int(i), "points": float(p) * (5 if good else 0.5), "comment": "mock"}
                for i, p in items
            ]
            answers.append(
                {"answer_id": aid, "points": 99.0, "rubric": rubric, "feedback": "Mock feedback."}
            )
        return {"answers": answers, "summary": "Mock summary.", "revise": ["Mock topic"]}

    def _CheatSheetOut(self, lines, req) -> dict[str, Any]:  # type: ignore[no-untyped-def]
        lectures = re.findall(r'^## L(\d+) "(.*)"', req.context, re.M)
        body = "\n\n".join(f"## {t}\n- ★ Key idea of L{n}\n- $x^2$" for n, t in lectures)
        return {"title": "Mock cheat sheet", "markdown": body}

    def _ExplanationsOut(self, lines, req) -> dict[str, Any]:  # type: ignore[no-untyped-def]
        ids = re.findall(r"^- id=(\d+) \[(\d+:\d{2}(?::\d{2})?)\]", req.instruction, re.M)
        return {
            "explanations": [
                {
                    "bookmark_id": int(i),
                    "title": f"The passage at {t}",
                    "explanation": f"In simpler words: what was said around [{t}].",
                    "example": "For example, $x = 2$.",
                    "check_question": "What was the main point?",
                    "check_answer": "The mock answer.",
                    "refs": [{"start": t, "end": t}],
                }
                for i, t in ids
            ]
        }

    def _CleanupOut(self, lines, req) -> dict[str, Any]:  # type: ignore[no-untyped-def]
        """Respell vocabulary terms that were split or mis-cased ("place holder" -> "PlaceHolder").

        Also returns two fixes the job must reject: an unknown line and a full rewrite.
        """
        vocab = re.search(r"^Vocabulary: (.+)$", req.context, re.M)
        terms = [t.strip() for t in vocab.group(1).split(",")] if vocab else []
        fixes = []
        numbered = re.findall(r"^#(\d+) \[[^\]]+\] (.*)$", req.instruction, re.M)
        for n, text in numbered:
            fixed = text
            for term in terms:
                pattern = (
                    r"\b" + r"[\s-]?".join(re.escape(c) for c in term.replace(" ", "")) + r"\b"
                )
                fixed = re.sub(pattern, term, fixed, flags=re.I)
            if fixed != text:
                fixes.append({"line": int(n), "text": fixed, "reason": "vocabulary spelling"})
        if numbered:
            fixes.append({"line": 999_999, "text": "No such line.", "reason": "bogus"})
            first = numbered[0]
            fixes.append(
                {"line": int(first[0]), "text": "An entirely different sentence.", "reason": "x"}
            )
        return {"fixes": fixes}

    def _VerifyOut(self, lines, req) -> dict[str, Any]:  # type: ignore[no-untyped-def]
        ids = [int(x) for x in ITEM_ID.findall(req.instruction)]
        return {
            "verdicts": [
                {
                    "id": i,
                    "supported": "UNSUPPORTED" not in req.instruction.split(f"id={i}", 1)[1][:400],
                    "issue": "",
                }
                for i in ids
            ]
        }
