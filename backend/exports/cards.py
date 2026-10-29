"""Export flashcards to Anki (.apkg via genanki) and CSV.

Anki decks are "LectureLab::<subject>::L<n> <lecture title>". Note GUIDs are derived from the
card id, so importing again updates the same notes instead of duplicating them. Markdown is
converted to simple HTML and $...$ math to MathJax \\(...\\), which Anki renders natively. Each
note links back to the lecture moment in LectureLab. Scheduling isn't exported: Anki starts the
cards as new.
"""

from __future__ import annotations

import csv
import hashlib
import html
import io
import re
import tempfile
from pathlib import Path
from typing import Any

from sqlmodel import Session, col, select

from backend.core.config import get_settings
from backend.core.models import Flashcard, Lecture, Subject
from backend.core.util import format_hms
from backend.search.retrieve import lecture_numbers, short_name
from backend.study.cards import card_state

CSS = """
.card { font-family: system-ui, sans-serif; font-size: 20px; text-align: left; color: #111;
  background: #fff; line-height: 1.45; max-width: 46em; margin: 0 auto; }
.nightMode .card, .card.nightMode { color: #eee; background: #1e1e1e; }
code, pre { font-family: Consolas, monospace; font-size: 0.85em; }
pre { background: rgba(127,127,127,.12); padding: .6em .8em; border-radius: 6px; overflow-x: auto; }
.cloze { font-weight: bold; color: #2563eb; }
.source { margin-top: 1.2em; font-size: 0.7em; opacity: .7; }
.source a { color: inherit; }
"""


def _id(name: str) -> int:
    """Stable 31-bit id for a deck/model name (Anki ids must fit in a signed 64-bit int)."""
    return int(hashlib.sha1(name.encode()).hexdigest()[:8], 16)


def _models() -> dict[str, Any]:
    import genanki

    fields = [{"name": "Front"}, {"name": "Back"}, {"name": "Source"}]
    answer = '{{FrontSide}}<hr id="answer">{{Back}}<div class="source">{{Source}}</div>'
    basic = genanki.Model(
        _id("LectureLab Basic v1"),
        "LectureLab Basic",
        fields=fields,
        templates=[{"name": "Card 1", "qfmt": "{{Front}}", "afmt": answer}],
        css=CSS,
    )
    reversed_ = genanki.Model(
        _id("LectureLab Basic+Reversed v1"),
        "LectureLab Basic (and reversed)",
        fields=fields,
        templates=[
            {"name": "Card 1", "qfmt": "{{Front}}", "afmt": answer},
            {
                "name": "Card 2",
                "qfmt": "{{Back}}",
                "afmt": '{{FrontSide}}<hr id="answer">{{Front}}'
                '<div class="source">{{Source}}</div>',
            },
        ],
        css=CSS,
    )
    cloze = genanki.Model(
        _id("LectureLab Cloze v1"),
        "LectureLab Cloze",
        fields=[{"name": "Text"}, {"name": "Back Extra"}, {"name": "Source"}],
        templates=[
            {
                "name": "Cloze",
                "qfmt": "{{cloze:Text}}",
                "afmt": '{{cloze:Text}}<br>{{Back Extra}}<div class="source">{{Source}}</div>',
            }
        ],
        css=CSS,
        model_type=genanki.Model.CLOZE,
    )
    return {"basic": basic, "reversed": reversed_, "cloze": cloze}


SPECIAL = re.compile(r"(```[\s\S]*?```|`[^`\n]+`|\$\$[\s\S]+?\$\$|\$[^$\n]+?\$)")


def md_to_html(md: str) -> str:
    """Just enough Markdown for flashcards: bold, italics, code, math, line breaks."""
    out = []
    for i, part in enumerate(SPECIAL.split(md)):
        if i % 2:
            if part.startswith("```"):
                body = part[3:-3]
                body = body.split("\n", 1)[1] if "\n" in body else body  # drop the language tag
                out.append(
                    f"<pre><code>{html.escape(body.strip(chr(10)), quote=False)}</code></pre>"
                )
            elif part.startswith("`"):
                out.append(f"<code>{html.escape(part[1:-1], quote=False)}</code>")
            elif part.startswith("$$"):
                out.append(r"\[" + html.escape(part[2:-2], quote=False) + r"\]")
            else:
                out.append(r"\(" + html.escape(part[1:-1], quote=False) + r"\)")
            continue
        t = html.escape(part, quote=False)
        t = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", t)
        t = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"<i>\1</i>", t)
        out.append(t.replace("\n", "<br>"))
    return "".join(out)


def _context(s: Session, cards: list[Flashcard]) -> tuple[dict, dict, dict]:
    subjects = {x.id: x for x in s.exec(select(Subject)).all()}
    lectures = {
        x.id: x
        for x in s.exec(
            select(Lecture).where(col(Lecture.id).in_({c.lecture_id for c in cards}))
        ).all()
    }
    numbers = lecture_numbers(s, {c.subject_id for c in cards})
    return subjects, lectures, numbers


def _source(card: Flashcard, subject: Subject | None, number: int | None, link: bool) -> str:
    if not card.source_refs or number is None:
        return ""
    ref = card.source_refs[0]
    label = f"{short_name(subject.name) if subject else ''} L{number} · " + (
        f"p. {ref['page']}" if ref.get("page") else format_hms(ref["start_s"])
    )
    if not link:
        return label.strip()
    cfg = get_settings()
    url = f"http://{cfg.host}:{cfg.port}/lectures/{card.lecture_id}?t={int(ref['start_s'])}"
    return f'<a href="{url}">{html.escape(label.strip())}</a>'


def _tag(text: str) -> str:
    return re.sub(r"\s+", "_", text.strip())


def to_apkg(s: Session, cards: list[Flashcard]) -> bytes:
    import genanki

    models = _models()
    subjects, lectures, numbers = _context(s, cards)
    decks: dict[str, Any] = {}
    for c in cards:
        subject = subjects.get(c.subject_id)
        lec = lectures.get(c.lecture_id)
        number = numbers.get(c.lecture_id) if c.lecture_id else None
        name = f"LectureLab::{subject.name if subject else 'Cards'}"
        if lec is not None and number is not None:
            name += f"::L{number:02d} {lec.title}"
        if name not in decks:
            decks[name] = genanki.Deck(_id(name), name)
        source = _source(c, subject, number, link=True)
        tags = [_tag(t) for t in c.tags if t.strip()]
        if subject:
            tags.append(_tag(short_name(subject.name)))
        if c.type == "cloze":
            model, fields = models["cloze"], [md_to_html(c.front), md_to_html(c.back), source]
        else:
            model = models["reversed" if c.type == "reversed" else "basic"]
            fields = [md_to_html(c.front), md_to_html(c.back), source]
        decks[name].add_note(
            genanki.Note(
                model=model,
                fields=fields,
                tags=tags,
                guid=genanki.guid_for("lecturelab", c.id),
            )
        )
    with tempfile.TemporaryDirectory(dir=get_settings().tmp_dir) as tmp:
        path = Path(tmp) / "cards.apkg"
        genanki.Package(list(decks.values())).write_to_file(str(path))
        return path.read_bytes()


def to_csv(s: Session, cards: list[Flashcard]) -> bytes:
    subjects, lectures, numbers = _context(s, cards)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["subject", "lecture", "type", "front", "back", "tags", "state", "due", "source"])
    for c in cards:
        subject = subjects.get(c.subject_id)
        lec = lectures.get(c.lecture_id)
        number = numbers.get(c.lecture_id) if c.lecture_id else None
        w.writerow(
            [
                subject.name if subject else "",
                f"L{number} {lec.title}" if lec and number else "",
                c.type,
                c.front,
                c.back,
                " ".join(_tag(t) for t in c.tags),
                card_state(c),
                c.due.isoformat() if c.due else "",
                _source(c, subject, number, link=False),
            ]
        )
    return buf.getvalue().encode("utf-8-sig")  # BOM: Excel opens UTF-8 correctly
