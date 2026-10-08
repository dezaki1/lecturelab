"""Deterministic offline provider for tests and UI work. Never calls a network.

It reads the "[mm:ss] text" transcript lines from the context and builds schema-valid output
that cites real timestamps, so the whole pipeline (refs, rendering, verification) is exercised.
"""

from __future__ import annotations

import re
from typing import Any

from backend.ai.types import LLMRequest, LLMResult, Usage

LINE = re.compile(r"^\[(\d+:\d{2}(?::\d{2})?)\] (.+)$", re.M)
ITEM_ID = re.compile(r"^- id=(\d+)", re.M)


class MockProvider:
    name = "mock"

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

    def _Ping(self, lines, req) -> dict[str, Any]:  # type: ignore[no-untyped-def]
        return {"ok": True}

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
