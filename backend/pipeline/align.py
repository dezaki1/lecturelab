"""Place the pages of a slide PDF on the lecture timeline.

Each page is compared with what was on screen (captured slides' OCR text, when the lecture has
some) or else with what was said (overlapping transcript windows), using TF-IDF cosine
similarity. A monotonic alignment then assigns pages to times in order: slides are presented
front to back, and a page may be skipped (not shown in this lecture). Pages between two matched
pages get interpolated times; pages before the first or after the last match belong to other
lectures and are left out.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from itertools import pairwise

import numpy as np

TOKEN = re.compile(r"[a-zà-öø-ÿß0-9]+(?:[-'][a-zà-öø-ÿß0-9]+)*")
_STOP = """
the and for are but not you all any can her was one our out has have had this that with from
they will what when which there their then them these those into than also just only very about
more some such here where would could should been being does did doing its let lets okay yeah
right now well so we it is in on of to a an as at by be or if do me my
der die das und ist ein eine einen einem einer nicht mit von den dem des sich auf für auch wir
ich sie es zu im am an als wie wenn oder aber noch nur dann also hier
"""
STOPWORDS = frozenset(_STOP.split())
WINDOW_S = 45.0
STEP_S = 15.0
MATCH_MIN = 0.12  # cosine below this is not a match (unrelated text scores ~0.0-0.08)
SCREEN_MATCH_MIN = 0.3  # page vs captured slide: same slide scores well above 0.5


def tokens(text: str) -> list[str]:
    return [
        t for t in TOKEN.findall(text.lower()) if t not in STOPWORDS and (len(t) > 2 or t.isdigit())
    ]


def tfidf(docs: list[str]) -> np.ndarray:
    """L2-normalised TF-IDF rows (sublinear tf, smoothed idf)."""
    toks = [tokens(d) for d in docs]
    vocab: dict[str, int] = {}
    for ts in toks:
        for t in ts:
            vocab.setdefault(t, len(vocab))
    m = np.zeros((len(docs), max(len(vocab), 1)), dtype=np.float32)
    for i, ts in enumerate(toks):
        for t, c in Counter(ts).items():
            m[i, vocab[t]] = 1 + math.log(c)
    df = (m > 0).sum(axis=0)
    m *= np.log((1 + len(docs)) / (1 + df)) + 1
    norms = np.linalg.norm(m, axis=1, keepdims=True)
    return m / np.where(norms == 0, 1, norms)


def similarity(pages: list[str], targets: list[str]) -> np.ndarray:
    """pages x targets cosine similarity, with idf computed over both sets."""
    m = tfidf(pages + targets)
    return m[: len(pages)] @ m[len(pages) :].T


def monotonic_match(sim: np.ndarray, threshold: float) -> list[int | None]:
    """Assign each page a target index (non-decreasing in page order) or None, maximising the
    total similarity above ``threshold``."""
    n, m = sim.shape
    if n == 0 or m == 0:
        return [None] * n
    gain = np.maximum(sim - threshold, 0)
    g = np.zeros((n + 1, m), dtype=np.float64)  # g[i, j]: best for pages < i, targets <= j
    for i in range(1, n + 1):
        row = g[i - 1] + gain[i - 1]
        g[i] = np.maximum.accumulate(np.maximum(row, g[i - 1]))
    out: list[int | None] = [None] * n
    i, j = n, m - 1
    while i > 0 and j >= 0:
        if j > 0 and g[i, j] == g[i, j - 1]:
            j -= 1
        elif gain[i - 1, j] > 0 and math.isclose(g[i, j], g[i - 1, j] + gain[i - 1, j]):
            out[i - 1] = j
            i -= 1
        else:
            i -= 1
    return out


@dataclass
class Placed:
    page: int  # 1-based
    t_s: float
    score: float  # similarity of the match; 0 for interpolated pages


def windows(segments: list[tuple[float, float, str]]) -> list[tuple[float, str]]:
    """Overlapping transcript windows: (start time, text)."""
    if not segments:
        return []
    out = []
    end = segments[-1][1]
    t = 0.0
    while t < end:
        text = " ".join(s[2] for s in segments if s[1] > t and s[0] < t + WINDOW_S)
        if text:
            out.append((t, text))
        t += STEP_S
    return out


def _transcript_matches(
    pages: list[str], segments: list[tuple[float, float, str]]
) -> dict[int, tuple[float, float]]:
    """Page -> (time, score) from the transcript. A page is matched to a window, then placed
    at the first sentence in it that talks about the page (slides appear as a topic starts)."""
    wins = windows(segments)
    if not wins:
        return {}
    sim = similarity(pages, [t for _, t in wins])
    out: dict[int, tuple[float, float]] = {}
    for i, j in enumerate(monotonic_match(sim, MATCH_MIN)):
        if j is None:
            continue
        start = wins[j][0]
        inside = [s for s in segments if s[1] > start and s[0] < start + WINDOW_S]
        per_sentence = similarity([pages[i]], [s[2] for s in inside])[0]
        best = float(per_sentence.max())
        k = next(k for k, v in enumerate(per_sentence) if v >= 0.7 * best) if best > 0 else 0
        out[i] = (max(inside[k][0], 0.0), float(sim[i, j]))
    return out


def place_pages(
    pages: list[str],
    segments: list[tuple[float, float, str]],
    screen: list[tuple[float, str]] | None = None,
) -> list[Placed]:
    """Times for the pages of a PDF in this lecture (only pages inside the matched range)."""
    if not pages:
        return []
    # Captured slides give exact times; they are anchors. The transcript fills in the other
    # pages wherever that keeps the order consistent with the anchors.
    anchors: dict[int, tuple[float, float]] = {}  # page index -> (time, score)
    if screen:
        sim = similarity(pages, [t for _, t in screen])
        for i, j in enumerate(monotonic_match(sim, SCREEN_MATCH_MIN)):
            if j is not None:
                anchors[i] = (screen[j][0], float(sim[i, j]))
    matched = dict(anchors)
    for i, (t, score) in _transcript_matches(pages, segments).items():
        if i in matched:
            continue
        before = [anchors[a][0] for a in anchors if a < i]
        after = [anchors[a][0] for a in anchors if a > i]
        if (before and t < max(before)) or (after and t > min(after)):
            continue
        matched[i] = (t, score)
    if not matched:
        return []
    order = sorted(matched)
    latest = 0.0
    for i in order:  # refined times can cross slightly; slides still go front to back
        latest = max(latest, matched[i][0])
        matched[i] = (latest, matched[i][1])
    placed = [Placed(i + 1, *matched[i]) for i in order]
    # Pages between two matched pages were shown in between: interpolate their times.
    for a, b in pairwise(order):
        ta, tb = matched[a][0], matched[b][0]
        for k in range(a + 1, b):
            placed.append(Placed(k + 1, ta + (tb - ta) * (k - a) / (b - a), 0.0))
    return sorted(placed, key=lambda p: p.page)
