from __future__ import annotations

import re
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import delete

from backend.core.db import session_scope
from backend.core.jobs import Worker
from backend.core.models import TranscriptSegment
from backend.pipeline.align import monotonic_match, place_pages, similarity
from tests.pdfgen import make_pdf

FIXTURES = Path(__file__).parent / "fixtures"
pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not on PATH")


def script_segments(duration: float = 114.0) -> list[tuple[float, float, str]]:
    """The fixture lecture's script as timed sentences (time proportional to word count)."""
    text = (FIXTURES / "lecture.txt").read_text(encoding="utf-8")
    sentences = [s.strip() for s in re.split(r"(?<=[.?!])\s+", text) if s.strip()]
    words = [len(s.split()) for s in sentences]
    total = sum(words)
    out, t = [], 0.0
    for s, w in zip(sentences, words, strict=True):
        d = duration * w / total
        out.append((round(t, 2), round(t + d, 2), s))
        t += d
    return out


def time_of(segments: list[tuple[float, float, str]], phrase: str) -> float:
    return next(a for a, _, s in segments if phrase in s)


UNRELATED_FIRST = ["Support vector machines", "Maximum margin, hinge loss, kernels"]
PAGES = [
    ["Principal component analysis", "Unsupervised learning, k-means recap"],
    ["Computing PCA", "Center the data", "Covariance matrix", "Eigenvectors and eigenvalues"],
    ["Exam relevant", "Keep the largest eigenvalues", "Best linear projection, least squares"],
    ["Questions?"],  # nothing to match: placed by interpolation
    ["Example", "Height and weight are correlated", "First component along the diagonal"],
    ["Exercise", "Implement PCA in Python with NumPy", "Compare with scikit-learn"],
]
UNRELATED_LAST = ["Reinforcement learning", "Markov decision processes, Bellman equation"]


def test_monotonic_match_keeps_order_and_skips_weak_pages() -> None:
    import numpy as np

    sim = np.array([[0.9, 0.1, 0.0], [0.0, 0.05, 0.0], [0.8, 0.1, 0.7], [0.0, 0.0, 0.6]])
    # Pages may share a target (a short slide); order never goes backwards; weak pages skip.
    assert monotonic_match(sim, 0.12) == [0, None, 0, 2]
    sim[3] = [0.7, 0.0, 0.0]  # a later page can't jump back before page 2's target
    assert monotonic_match(sim, 0.12)[3] in (None, 0)


def test_pages_are_placed_on_the_lecture_timeline() -> None:
    segs = script_segments()
    texts = ["\n".join(p) for p in [UNRELATED_FIRST, *PAGES, UNRELATED_LAST]]
    placed = {p.page: p for p in place_pages(texts, segs)}
    # Pages from other lectures (first/last) are left out.
    assert sorted(placed) == [2, 3, 4, 5, 6, 7]
    times = [placed[p].t_s for p in sorted(placed)]
    assert times == sorted(times)
    assert abs(placed[3].t_s - time_of(segs, "First, we center")) <= 30
    assert abs(placed[6].t_s - time_of(segs, "Suppose we have two features")) <= 30
    assert abs(placed[7].t_s - time_of(segs, "implement PCA in Python")) <= 30
    assert placed[5].score == 0.0  # "Questions?" was interpolated between its neighbours
    assert placed[4].t_s <= placed[5].t_s <= placed[6].t_s


def test_captured_slides_are_preferred_for_matching() -> None:
    segs = script_segments()
    texts = ["\n".join(p) for p in PAGES]
    screen = [(5.0, "\n".join(PAGES[0])), (40.0, "\n".join(PAGES[1]))]
    placed = {p.page: p for p in place_pages(texts, segs, screen)}
    # Two captured slides anchor pages 1-2 exactly; the transcript places the rest.
    assert placed[1].t_s == 5.0 and placed[2].t_s == 40.0 and len(placed) >= 5
    assert all(placed[p].t_s >= 40.0 for p in placed if p > 2)
    screen += [
        (60.0, "\n".join(PAGES[2])),
        (80.0, "\n".join(PAGES[4])),
        (95.0, "\n".join(PAGES[5])),
    ]
    placed = {p.page: p for p in place_pages(texts, segs, screen)}
    # More captured slides: their exact times win.
    assert placed[1].t_s == 5.0 and placed[2].t_s == 40.0 and placed[6].t_s == 95.0
    assert placed[2].score > 0.9


def test_similarity_shape() -> None:
    assert similarity(["a b c"], ["d e", "f"]).shape == (1, 2)


# API ---------------------------------------------------------------------------------------------
def _lecture_with_script(client: TestClient) -> int:
    sid = client.post("/api/subjects", json={"name": "ML"}).json()["id"]
    r = client.post(
        "/api/imports",
        params={"subject_id": sid, "filename": "lecture.m4a"},
        content=(FIXTURES / "lecture.m4a").read_bytes(),
    )
    lid = r.json()["id"]
    Worker().run_pending()
    with session_scope() as s:  # the fake engine's placeholder text -> the real script
        s.exec(delete(TranscriptSegment).where(TranscriptSegment.lecture_id == lid))  # type: ignore[call-overload]
        for i, (a, b, text) in enumerate(script_segments()):
            s.add(TranscriptSegment(lecture_id=lid, idx=i, start=a, end=b, text=text))
        s.commit()
    return lid


def test_upload_place_list_and_delete_a_slide_pdf(client: TestClient, tmp_path: Path) -> None:
    lid = _lecture_with_script(client)
    pdf = make_pdf(tmp_path / "PCA.pdf", [UNRELATED_FIRST, *PAGES, UNRELATED_LAST])

    bad = client.post(f"/api/lectures/{lid}/documents", params={"filename": "x.pdf"}, content=b"hi")
    assert bad.status_code == 415
    r = client.post(
        f"/api/lectures/{lid}/documents",
        params={"filename": "Week 3 - PCA.pdf"},
        content=pdf.read_bytes(),
    )
    assert r.status_code == 201, r.text
    doc = r.json()
    assert doc["title"] == "Week 3 - PCA" and doc["processing"] is True

    assert Worker().run_pending() == 2  # document, then search reindex
    [doc] = client.get(f"/api/lectures/{lid}/documents").json()
    assert doc["pages"] == 8 and doc["placed"] == 6 and doc["processing"] is False

    slides = client.get(f"/api/lectures/{lid}/slides").json()
    assert [s["page"] for s in slides] == [2, 3, 4, 5, 6, 7]
    assert all(s["source"] == "pdf" for s in slides)
    assert "Covariance matrix" in slides[1]["ocr_text"]
    img = client.get(f"/api/slides/{slides[0]['id']}/image")
    assert img.status_code == 200 and img.content[:2] == b"\xff\xd8"  # JPEG
    f = client.get(f"/api/documents/{doc['id']}/file")
    assert f.status_code == 200 and f.content.startswith(b"%PDF")

    assert client.delete(f"/api/documents/{doc['id']}").status_code == 204
    assert client.get(f"/api/lectures/{lid}/slides").json() == []
    assert client.get(f"/api/lectures/{lid}/documents").json() == []


def test_pdf_slide_text_reaches_generation(client: TestClient, tmp_path: Path) -> None:
    from backend.core.models import Lecture
    from backend.pipeline.generate import build_context

    lid = _lecture_with_script(client)
    pdf = make_pdf(tmp_path / "PCA.pdf", PAGES)
    client.post(
        f"/api/lectures/{lid}/documents", params={"filename": "PCA.pdf"}, content=pdf.read_bytes()
    )
    Worker().run_pending()
    with session_scope() as s:
        ctx = build_context(s, s.get(Lecture, lid))  # type: ignore[arg-type]
    section = ctx.context.split("## Slides shown", 1)[1].split("## Transcript", 1)[0]
    assert "(PDF p. 2) Computing PCA / Center the data / Covariance matrix" in section
    assert section.index("p. 1)") < section.index("p. 6)")
