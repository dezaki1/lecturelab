"""Export subjects as an Obsidian vault (a zip of Markdown files).

Layout: ``LectureLab/<Subject>/`` with one note per lecture (``L03 <title>.md``), a course
overview (topics as [[wikilinks]] to lecture sections, the concept map as a Mermaid diagram,
all exam hints), the cumulative glossary and the cheat sheet. Lecture notes contain the summary,
study notes, exam hints, explanations, glossary, announcements, flashcards in the Obsidian
Spaced Repetition format and exam questions as collapsible callouts. Timestamps become links
that open the lecture at that moment in LectureLab.
"""

from __future__ import annotations

import difflib
import io
import re
import zipfile
from datetime import date
from typing import Any

from sqlmodel import Session, col, select

from backend.core.config import get_settings
from backend.core.models import (
    Bookmark,
    ExamHint,
    Explanation,
    Flashcard,
    Lecture,
    Note,
    Question,
    Subject,
)
from backend.core.util import format_hms
from backend.pipeline.generate import parse_ts
from backend.pipeline.subject_hub import current_subject_note, lectures_with_notes

ROOT = "LectureLab"
UNSAFE = re.compile(r'[\\/:*?"<>|#^\[\]]+')
TS = re.compile(r"\[((?:\d{1,2}:)?\d{1,3}:\d{2})(?:\s*[-–]\s*(?:\d{1,2}:)?\d{1,3}:\d{2})?\](?!\()")
CODE = re.compile(r"(```[\s\S]*?```|`[^`\n]*`)")
HEADING = re.compile(r"^(#{1,6}) (.*)$")
CLOZE = re.compile(r"\{\{c\d+::(.+?)(?:::.+?)?\}\}")


def safe(name: str) -> str:
    cleaned = " ".join(UNSAFE.sub(" ", name).split()).strip(" .")
    return cleaned[:80] or "Untitled"


def base_url() -> str:
    cfg = get_settings()
    return f"http://{cfg.host}:{cfg.port}"


def _outside_code(md: str, fn) -> str:  # type: ignore[no-untyped-def]
    return "".join(part if i % 2 else fn(part) for i, part in enumerate(CODE.split(md)))


def link_times(md: str, lecture_id: int) -> str:
    """[12:34] -> [12:34](http://.../lectures/5?t=754)."""
    url = f"{base_url()}/lectures/{lecture_id}"

    def repl(m: re.Match[str]) -> str:
        t = parse_ts(m.group(1))
        return m.group(0) if t is None else f"[{m.group(0)[1:-1]}]({url}?t={int(t)})"

    return _outside_code(md, lambda part: TS.sub(repl, part))


def lecture_body(md: str, lecture_id: int, shift: int = 1) -> tuple[str, list[str]]:
    """Demote headings by ``shift``, move timestamps out of headings (clean anchors for
    wikilinks) and link the rest. Returns the Markdown and the heading texts."""
    out, headings, in_code = [], [], False
    for line in md.splitlines():
        if line.lstrip().startswith("```"):
            in_code = not in_code
        m = None if in_code else HEADING.match(line)
        if not m:
            out.append(line)
            continue
        level = min(len(m.group(1)) + shift, 6)
        stamps = TS.findall(m.group(2))
        text = " ".join(TS.sub("", m.group(2)).split())
        headings.append(text)
        out.append(f"{'#' * level} {text}")
        if stamps:
            t = int(parse_ts(stamps[0]) or 0)
            out.append(f"[▶ {stamps[0]}]({base_url()}/lectures/{lecture_id}?t={t})")
    return link_times("\n".join(out), lecture_id), headings


def _current(s: Session, lecture_id: int, kind: str) -> Note | None:
    return s.exec(
        select(Note).where(Note.lecture_id == lecture_id, Note.kind == kind, col(Note.is_current))
    ).first()


def _yaml(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _tag(text: str) -> str:
    return re.sub(r"[^\w-]+", "-", text.lower()).strip("-") or "subject"


def _ts_link(lecture_id: int, t: float) -> str:
    return f"[{format_hms(t)}]({base_url()}/lectures/{lecture_id}?t={int(t)})"


def lecture_note(
    s: Session,
    subject: Subject,
    n: int,
    lec: Lecture,
    names: dict[int, str],
    prev: str | None,
    nxt: str | None,
) -> tuple[str, list[str]]:
    tag = _tag(subject.name)
    front = [
        "---",
        f"subject: {_yaml(subject.name)}",
        f"lecture: {n}",
        f"title: {_yaml(lec.title)}",
        f"date: {lec.started_at.astimezone():%Y-%m-%d}",
    ]
    if lec.duration_s:
        front.append(f"duration: {_yaml(format_hms(lec.duration_s))}")
    if lec.kind:
        front.append(f"type: {lec.kind}")
    front += [
        f"tags: [lecturelab, {tag}]",
        f"lecturelab: {base_url()}/lectures/{lec.id}",
        "---",
        "",
    ]
    nav = " · ".join(
        x
        for x in (
            f"← [[{prev}]]" if prev else "",
            "[[_Course overview|Course overview]]",
            f"[[{nxt}]] →" if nxt else "",
        )
        if x
    )
    parts = [*front, f"# L{n} {lec.title}", "", nav, ""]
    headings: list[str] = []
    for kind, title in (("summary", "Summary"), ("notes", "Notes")):
        note = _current(s, lec.id, kind)  # type: ignore[arg-type]
        if note is not None:
            body, heads = lecture_body(note.content_md, lec.id)  # type: ignore[arg-type]
            if kind == "notes":
                headings = heads
            parts += [f"## {title}", "", body, ""]

    hints = s.exec(
        select(ExamHint).where(ExamHint.lecture_id == lec.id).order_by(col(ExamHint.start_s))
    ).all()
    if hints:
        parts += ["## Exam hints", ""]
        parts += [
            f"- ★ {_ts_link(lec.id, h.start_s)} {h.topic or h.quote}"  # type: ignore[arg-type]
            + (f' ("{h.quote}")' if h.topic and h.quote else "")
            for h in hints
        ]
        parts.append("")

    explanations = s.exec(
        select(Explanation, Bookmark)
        .join(Bookmark, col(Bookmark.id) == col(Explanation.bookmark_id))
        .where(Explanation.lecture_id == lec.id)
        .order_by(col(Bookmark.t_s))
    ).all()
    if explanations:
        parts += ["## Explain this part", ""]
        for e, b in explanations:
            parts += [
                f"### {e.title or 'Confusing passage'}",
                f"Bookmarked at {_ts_link(lec.id, b.t_s)}" + (f": *{b.note}*" if b.note else ""),  # type: ignore[arg-type]
                "",
                link_times(e.explanation_md, lec.id),  # type: ignore[arg-type]
                "",
                "**Example.** " + link_times(e.example_md, lec.id),  # type: ignore[arg-type]
                "",
                f"> [!question] {e.check_question}",
                f"> [!success]- Answer\n> {e.check_answer}",
                "",
            ]

    for kind, title in (("glossary", "Glossary"), ("announcements", "Announcements")):
        note = _current(s, lec.id, kind)  # type: ignore[arg-type]
        if note is not None and note.content_md.strip():
            body, _ = lecture_body(note.content_md, lec.id, shift=2)  # type: ignore[arg-type]
            parts += [f"## {title}", "", body, ""]

    cards = s.exec(
        select(Flashcard)
        .where(
            Flashcard.lecture_id == lec.id,
            col(Flashcard.is_current),
            col(Flashcard.suspended).is_(False),
        )
        .order_by(col(Flashcard.id))
    ).all()
    if cards:
        parts += ["## Flashcards", "", f"#flashcards/{tag}", ""]
        for c in cards:
            if c.type == "cloze":
                parts += [CLOZE.sub(r"==\1==", c.front), ""]
            else:
                parts += [
                    c.front.strip(),
                    "??" if c.type == "reversed" else "?",
                    c.back.strip(),
                    "",
                ]

    questions = s.exec(
        select(Question)
        .where(Question.lecture_id == lec.id, col(Question.is_current))
        .order_by(col(Question.kind), col(Question.id))
    ).all()
    for qkind, title in (("exam", "Exam questions"), ("exercise", "Exercises")):
        items = [q for q in questions if q.kind == qkind]
        if not items:
            continue
        parts += [f"## {title}", ""]
        for i, q in enumerate(items, start=1):
            head = q.title or f"Question {i}"
            parts.append(
                f"> [!question] {head} ({q.points:g} points)"
                if qkind == "exam"
                else f"> [!example] {head}"
            )
            parts += [f"> {line}" for line in link_times(q.prompt, lec.id).splitlines()]  # type: ignore[arg-type]
            for o in q.options:
                parts.append(f"> - {o.get('text', '')}")
            answer = q.answer if qkind == "exam" else "\n".join([*q.solution_steps, q.answer])
            parts.append(">")
            parts.append("> > [!success]- Answer")
            parts += [
                f"> > {line}" for line in link_times(answer, lec.id).splitlines() if line.strip()
            ]  # type: ignore[arg-type]
            parts.append("")
    return "\n".join(parts).rstrip() + "\n", headings


def mermaid(hub: Note) -> str:
    nodes = hub.data.get("concepts") or []
    known = {}
    lines = ["```mermaid", "flowchart TD"]
    for i, node in enumerate(nodes):
        nid = f"c{i}"
        known[node.get("id")] = nid
        label = str(node.get("label", "")).replace('"', "'")
        lines.append(f'  {nid}["{label}"]')
    for e in hub.data.get("relations") or []:
        a, b = known.get(e.get("source")), known.get(e.get("target"))
        if a and b and a != b:
            rel = str(e.get("label", "")).replace("|", "/").replace('"', "'")
            lines.append(f'  {a} -->|"{rel}"| {b}' if rel else f"  {a} --> {b}")
    lines.append("```")
    return "\n".join(lines)


def subject_files(s: Session, subject: Subject) -> dict[str, str]:
    lectures = [(n, lec) for n, lec in lectures_with_notes(s, subject.id)]  # type: ignore[arg-type]
    names = {lec.id: f"L{n:02d} {safe(lec.title)}" for n, lec in lectures}
    files: dict[str, str] = {}
    headings: dict[int, list[str]] = {}
    for i, (n, lec) in enumerate(lectures):
        prev = names[lectures[i - 1][1].id] if i > 0 else None
        nxt = names[lectures[i + 1][1].id] if i + 1 < len(lectures) else None
        body, heads = lecture_note(s, subject, n, lec, names, prev, nxt)  # type: ignore[arg-type]
        files[f"{names[lec.id]}.md"] = body  # type: ignore[index]
        headings[lec.id] = heads  # type: ignore[index]

    overview = [f"# {subject.name}", ""]
    if subject.exam_date:
        overview += [f"Exam: {subject.exam_date:%Y-%m-%d}", ""]
    hub = current_subject_note(s, subject.id, "course")  # type: ignore[arg-type]
    if hub is not None:
        overview += [hub.content_md.strip(), ""]
        ids = {int(k): v for k, v in (hub.data.get("lecture_ids") or {}).items()}
        topics = hub.data.get("topics") or []
        if topics:
            overview += ["## Topics", ""]
            for t in topics:
                links = []
                for p in t.get("places") or []:
                    lid = ids.get(int(p.get("lecture") or 0))
                    if lid not in names:
                        continue
                    target = names[lid]
                    for sec in (p.get("sections") or [])[:2]:
                        match = difflib.get_close_matches(
                            sec, headings.get(lid, []), n=1, cutoff=0.5
                        )
                        links.append(
                            f"[[{target}#{match[0]}|L{p.get('lecture')}: {sec}]]"
                            if match
                            else f"[[{target}]]"
                        )
                    if not p.get("sections"):
                        links.append(f"[[{target}]]")
                star = "★ " if t.get("exam_relevant") else ""
                summary = f": {t['summary']}" if t.get("summary") else ""
                overview.append(f"- {star}**{t.get('name', '')}**{summary} · " + ", ".join(links))
            overview.append("")
        if hub.data.get("concepts"):
            overview += ["## Concept map", "", mermaid(hub), ""]
    overview += ["## Lectures", ""] + [f"- [[{names[lec.id]}]]" for _, lec in lectures] + [""]  # type: ignore[index]
    hints = s.exec(
        select(ExamHint).where(ExamHint.subject_id == subject.id).order_by(col(ExamHint.start_s))
    ).all()
    by_lecture: dict[int, list[ExamHint]] = {}
    for h in hints:
        by_lecture.setdefault(h.lecture_id, []).append(h)
    if hints:
        overview += ["## Exam hints", ""]
        for _, lec in lectures:
            for h in by_lecture.get(lec.id, []):  # type: ignore[arg-type]
                overview.append(
                    f"- ★ [[{names[lec.id]}]] {_ts_link(lec.id, h.start_s)} {h.topic or h.quote}"
                )  # type: ignore[arg-type, index]
        overview.append("")
    files["_Course overview.md"] = "\n".join(overview).rstrip() + "\n"

    from backend.study.topics import cumulative_glossary

    glossary = cumulative_glossary(s, subject.id)  # type: ignore[arg-type]
    if glossary:
        g = [f"# {subject.name}: glossary", ""]
        for e in glossary:
            g.append(f"## {e['term']}")
            if e["german"]:
                g.append(f"*{e['german']}*")
            g += ["", e["definition"], ""]
            g.append(
                "Lectures: "
                + ", ".join(
                    f"[[{names[x['lecture_id']]}]]"
                    for x in e["lectures"]
                    if x["lecture_id"] in names
                )
            )
            g.append("")
        files["Glossary.md"] = "\n".join(g).rstrip() + "\n"

    sheet = current_subject_note(s, subject.id, "cheatsheet")  # type: ignore[arg-type]
    if sheet is not None:
        title = sheet.data.get("title") or f"{subject.name}: cheat sheet"
        files["Cheat sheet.md"] = f"# {title}\n\n{sheet.content_md.strip()}\n"
    return files


def export_vault(s: Session, subject_ids: list[int] | None = None) -> bytes:
    q = select(Subject).order_by(col(Subject.name))
    if subject_ids:
        q = q.where(col(Subject.id).in_(subject_ids))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(
            f"{ROOT}/README.md",
            f"# LectureLab export\n\nExported on {date.today():%Y-%m-%d}. Timestamps link to "
            f"LectureLab ({base_url()}); they work while LectureLab is running.\n",
        )
        for subject in s.exec(q).all():
            folder = f"{ROOT}/{safe(subject.name)}"
            for name, text in subject_files(s, subject).items():
                z.writestr(f"{folder}/{name}", text)
    return buf.getvalue()


def vault_stats(data: bytes) -> dict[str, Any]:
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        return {"files": len(z.namelist())}
