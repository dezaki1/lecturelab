"""Generated output with an odd label is mapped, not rejected (one bad item failed a whole call)."""

from __future__ import annotations

import pytest

from backend.ai.schemas import CardOut, QuestionOut


def _question(type_: str, bloom: str = "apply") -> QuestionOut:
    return QuestionOut.model_validate(
        {
            "type": type_,
            "bloom": bloom,
            "difficulty": "medium",
            "points": 4,
            "prompt": "Show that f is continuous at 0.",
            "options": [],
            "answer": "...",
            "explanation": "...",
            "rubric": [],
            "emphasized": False,
            "refs": [],
        }
    )


@pytest.mark.parametrize(
    ("raw", "kind"),
    [
        ("proof", "long"),  # 2026-10-21: Math III's questions failed on this three times
        ("Derivation", "long"),
        ("multiple_choice", "mcq"),
        ("Multiple-Choice", "mcq"),
        ("multi-select", "multi"),
        ("true_false", "tf"),
        ("numerical", "calculation"),
        ("short answer", "short"),
        ("programming", "code"),
        ("calculation", "calculation"),
    ],
)
def test_question_types(raw: str, kind: str) -> None:
    assert _question(raw).type == kind


def test_question_bloom_level() -> None:
    assert _question("short", bloom="Analysis").bloom == "analyze"
    assert _question("short", bloom="comprehension").bloom == "understand"


def test_card_types_still_lenient() -> None:
    assert CardOut._lenient_type("Q&A") == "basic"
    assert CardOut._lenient_type("Cloze deletion") == "cloze"
