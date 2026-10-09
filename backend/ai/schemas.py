"""Structured output schemas (sent to the model as JSON schema, validated with Pydantic).

Keep these simple: plain types, Literal enums and descriptions only. Structured outputs don't
support numeric/length constraints, so bounds are enforced in ``pipeline.generate``.
Timestamps are strings like "12:34" or "1:02:03", exactly as they appear in the transcript.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Difficulty = Literal["easy", "medium", "hard"]


class Ref(BaseModel):
    start: str = Field(description="Timestamp where the supporting passage starts, e.g. 12:34")
    end: str = Field(description="Timestamp where it ends")


class Bullet(BaseModel):
    text: str = Field(description="Markdown; LaTeX math allowed")
    refs: list[Ref]


class SummaryOut(BaseModel):
    topic_title: str = Field(description="Short title for this lecture's topic, max 8 words")
    tldr: list[Bullet] = Field(description="5-8 TL;DR bullets")
    remember: list[Bullet] = Field(description="Exactly 3: 'If you remember only 3 things'")


class NoteSection(BaseModel):
    heading: str
    markdown: str = Field(description="Section body in Markdown (no top-level heading)")
    refs: list[Ref] = Field(description="Where in the lecture this section's content comes from")
    emphasized: bool = Field(description="True if the lecturer stressed this section as important")


class NotesOut(BaseModel):
    sections: list[NoteSection]
    open_questions: list[str] = Field(description="Things to look up or ask about")


class GlossaryTerm(BaseModel):
    term: str
    definition: str
    german: str = Field(description="German term if used or standard, else empty string")
    first_mention: str = Field(description="Timestamp of the first mention")


class GlossaryOut(BaseModel):
    terms: list[GlossaryTerm]


class Announcement(BaseModel):
    kind: Literal["deadline", "exam", "assignment", "admin", "other"]
    text: str
    date: str = Field(description="ISO date YYYY-MM-DD if one is stated or inferable, else empty")
    at: str = Field(description="Timestamp where it was announced")


class AnnouncementsOut(BaseModel):
    items: list[Announcement]


class CardOut(BaseModel):
    type: Literal["basic", "cloze", "reversed"]
    front: str = Field(description="Question side; for cloze, the full text with {{c1::...}}")
    back: str = Field(description="Answer side; for cloze, optional extra context")
    tags: list[str]
    difficulty: Difficulty
    refs: list[Ref]


class FlashcardsOut(BaseModel):
    cards: list[CardOut]


class ExerciseOut(BaseModel):
    title: str
    kind: Literal["calculation", "derivation", "conceptual", "coding"]
    prompt: str = Field(description="Exercise statement in Markdown")
    hints: list[str] = Field(description="Progressive hints, weakest first")
    solution_steps: list[str] = Field(description="Step-by-step worked solution, Markdown")
    answer: str = Field(description="Final answer")
    expected_output: str = Field(
        description="For coding tasks: expected program output, else empty"
    )
    difficulty: Difficulty
    refs: list[Ref]


class ExercisesOut(BaseModel):
    exercises: list[ExerciseOut]


class Option(BaseModel):
    text: str
    correct: bool
    explanation: str = Field(description="Why this option is right or wrong")


class RubricItem(BaseModel):
    criterion: str
    points: float


class QuestionOut(BaseModel):
    type: Literal["mcq", "multi", "tf", "short", "long", "calculation", "code"]
    bloom: Literal["remember", "understand", "apply", "analyze", "evaluate", "create"]
    difficulty: Difficulty
    points: float
    prompt: str = Field(description="Question text in Markdown")
    options: list[Option] = Field(description="For mcq/multi/tf; empty otherwise")
    answer: str = Field(description="Model answer")
    explanation: str
    rubric: list[RubricItem] = Field(
        description="Grading rubric; points sum to the question's points"
    )
    emphasized: bool = Field(description="True if it targets something the lecturer stressed")
    refs: list[Ref]


class QuestionsOut(BaseModel):
    questions: list[QuestionOut]


class Verdict(BaseModel):
    id: int
    supported: bool = Field(description="True if the cited excerpt supports the item as written")
    issue: str = Field(description="If not supported: what is wrong or missing; else empty")


class VerifyOut(BaseModel):
    verdicts: list[Verdict]


class LineFix(BaseModel):
    line: int = Field(description="The line number shown before the timestamp")
    text: str = Field(description="The complete corrected line")
    reason: str = Field(description='What was misheard, e.g. "eigen vectors → eigenvectors"')


class CleanupOut(BaseModel):
    fixes: list[LineFix] = Field(description="Only lines that needed a fix")


OUTPUTS: dict[str, type[BaseModel]] = {
    "summary": SummaryOut,
    "notes": NotesOut,
    "glossary": GlossaryOut,
    "announcements": AnnouncementsOut,
    "flashcards": FlashcardsOut,
    "exercises": ExercisesOut,
    "questions": QuestionsOut,
    "verify": VerifyOut,
    "cleanup": CleanupOut,
}
