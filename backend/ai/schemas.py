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


class HintOut(BaseModel):
    start: str = Field(description="mm:ss copied from the transcript where the hint begins")
    end: str = Field(description="mm:ss where the flagged explanation ends")
    kind: str = Field(
        description='"exam" if the lecturer refers to the exam or grading, "important" for other '
        'explicit emphasis ("remember this", "das ist wichtig"), "repeated" for a point the '
        "lecturer comes back to several times"
    )
    topic: str = Field(description="What the student must know, as one short line")
    quote: str = Field(description="The lecturer's words that signal it (short, verbatim)")


class HintsOut(BaseModel):
    hints: list[HintOut]


class ExplanationOut(BaseModel):
    bookmark_id: int = Field(description="The id of the bookmark being explained")
    title: str = Field(description="What the confusing passage is about, a few words")
    explanation: str = Field(description="Simpler explanation in Markdown (LaTeX allowed)")
    example: str = Field(description="A small concrete example in Markdown")
    check_question: str = Field(description="One question that checks understanding")
    check_answer: str = Field(description="Its short model answer")
    refs: list[Ref]


class ExplanationsOut(BaseModel):
    explanations: list[ExplanationOut]


class TopicPlace(BaseModel):
    lecture: int = Field(description="Lecture number, e.g. 3 for L3")
    sections: list[str] = Field(
        description="Headings of that lecture's note sections covering the topic, copied exactly"
    )


class HubTopic(BaseModel):
    name: str = Field(description="Short topic name; keep names of existing topics unchanged")
    summary: str = Field(description="One line: what this topic is about")
    places: list[TopicPlace]
    exam_relevant: bool = Field(description="True if the lecturer flagged it for the exam")


class LectureRecap(BaseModel):
    lecture: int
    recap: str = Field(description="2-3 sentences: what this lecture added to the course")


class ConceptNode(BaseModel):
    id: str = Field(description="Short identifier, letters/digits/underscores")
    label: str = Field(description="Concept name as shown in the map")
    lecture: int = Field(description="Lecture number where the concept is introduced")


class ConceptEdge(BaseModel):
    source: str = Field(description="id of the source concept")
    target: str = Field(description="id of the target concept")
    label: str = Field(description='Relation, a few words, e.g. "is solved by", "generalises"')


class SubjectHubOut(BaseModel):
    overview: str = Field(
        description="Markdown, 1-3 paragraphs: the course so far, its thread and how the "
        "lectures build on each other"
    )
    recaps: list[LectureRecap]
    topics: list[HubTopic]
    concepts: list[ConceptNode]
    relations: list[ConceptEdge]


class PastExamQuestion(BaseModel):
    number: str = Field(description='As printed, e.g. "3b"')
    type: Literal["mcq", "multi", "tf", "short", "long", "calculation", "code", "other"]
    points: float = Field(description="Points if stated, else 0")
    topic: str = Field(description="Course topic it tests, a few words")
    text: str = Field(description="The question reduced to its core, keeping the exam's wording")


class PastExamOut(BaseModel):
    title: str = Field(description='e.g. "Final exam, winter 2024"')
    duration_min: int = Field(description="Exam duration in minutes if stated, else 0")
    total_points: float = Field(description="Total points if stated, else 0")
    questions: list[PastExamQuestion]
    style_notes: str = Field(
        description="How this exam asks: question styles, what answers must show (derivations, "
        "proofs, code, numbers by hand), allowed aids, typical traps"
    )


class SimQuestion(QuestionOut):
    lecture: int = Field(description="Number of the lecture the question is based on, e.g. 3")
    topic: str = Field(description="Course topic it tests")


class SimQuestionsOut(BaseModel):
    questions: list[SimQuestion]


class RubricScore(BaseModel):
    item: int = Field(description="Index of the rubric item, starting at 0")
    points: float = Field(description="Points awarded for this item")
    comment: str = Field(description="Why, briefly")


class GradedAnswer(BaseModel):
    answer_id: int
    points: float = Field(description="Total points awarded (sum of the rubric items)")
    rubric: list[RubricScore]
    feedback: str = Field(
        description="Markdown: what is right, what is wrong or missing, and how the full-mark "
        "answer differs"
    )


class ExamGradeOut(BaseModel):
    answers: list[GradedAnswer]
    summary: str = Field(description="Overall feedback in Markdown: strengths and weaknesses")
    revise: list[str] = Field(description="Topics to revise, most important first")


class CheatSheetOut(BaseModel):
    title: str = Field(description='e.g. "Machine Learning: cheat sheet"')
    markdown: str = Field(description="The cheat sheet in Markdown (no top-level # heading)")


class LineFix(BaseModel):
    line: int = Field(description="The line number shown before the timestamp")
    text: str = Field(description="The complete corrected line")
    reason: str = Field(description='What was misheard, e.g. "eigen vectors → eigenvectors"')


class CleanupOut(BaseModel):
    fixes: list[LineFix] = Field(description="Only lines that needed a fix")


OUTPUTS: dict[str, type[BaseModel]] = {
    "hints": HintsOut,
    "summary": SummaryOut,
    "notes": NotesOut,
    "glossary": GlossaryOut,
    "announcements": AnnouncementsOut,
    "flashcards": FlashcardsOut,
    "exercises": ExercisesOut,
    "questions": QuestionsOut,
    "explanations": ExplanationsOut,
    "verify": VerifyOut,
    "cleanup": CleanupOut,
    "past_exam": PastExamOut,
    "exam_questions": SimQuestionsOut,
    "exam_grade": ExamGradeOut,
    "cheatsheet": CheatSheetOut,
}
