"""Prompt templates in ``prompts/*.md``: YAML-ish front matter (``version: N``) + body.

Placeholders are ``{{name}}``. Cloze syntax like ``{{c1::...}}`` contains "::" and is left alone.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import cache
from pathlib import Path

PROMPTS_DIR = Path(__file__).resolve().parents[2] / "prompts"
PLACEHOLDER = re.compile(r"\{\{([a-z_]+)\}\}")


@dataclass(frozen=True)
class Prompt:
    name: str
    version: int
    body: str

    def render(self, **values: object) -> str:
        missing = set(PLACEHOLDER.findall(self.body)) - values.keys()
        if missing:
            raise KeyError(f"Prompt {self.name!r} needs {sorted(missing)}")
        return PLACEHOLDER.sub(lambda m: str(values[m.group(1)]), self.body).strip()

    @property
    def tag(self) -> str:
        return f"{self.name}@v{self.version}"


@cache
def load_prompt(name: str) -> Prompt:
    text = (PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8")
    version = 1
    if text.startswith("---"):
        _, front, text = text.split("---", 2)
        m = re.search(r"version:\s*(\d+)", front)
        if m:
            version = int(m.group(1))
    return Prompt(name, version, text.strip() + "\n")
