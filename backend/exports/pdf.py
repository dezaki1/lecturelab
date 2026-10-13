"""PDF export: print LectureLab's own print pages with headless Microsoft Edge (or Chrome).

Edge ships with Windows 11, so no PDF library is needed, and the page renders exactly like the
app (KaTeX math, code highlighting). A throwaway browser profile keeps the user's own Edge
untouched. Without a browser the UI falls back to the browser's "Save as PDF".
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from backend.core.config import get_settings
from backend.core.media import CREATE_NO_WINDOW

TIMEOUT_S = 90
# Time the page gets to fetch its data and render math before printing (virtual time).
RENDER_BUDGET_MS = 15_000


class PdfError(Exception):
    pass


def find_browser() -> str | None:
    """Edge first (always installed on Windows 11), then Chrome."""
    roots = [os.environ.get(k, "") for k in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA")]
    for rel, exe in (
        ("Microsoft/Edge/Application/msedge.exe", "msedge"),
        ("Google/Chrome/Application/chrome.exe", "chrome"),
    ):
        for root in filter(None, roots):
            path = Path(root) / rel
            if path.exists():
                return str(path)
        if found := shutil.which(exe):
            return found
    return None


def print_pdf(url: str) -> bytes:
    browser = find_browser()
    if browser is None:
        raise PdfError("No Edge or Chrome found for PDF export")
    with tempfile.TemporaryDirectory(dir=get_settings().tmp_dir) as tmp:
        out = Path(tmp) / "out.pdf"
        cmd = [
            browser,
            "--headless=new",
            "--disable-gpu",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-extensions",
            f"--user-data-dir={Path(tmp) / 'profile'}",
            "--no-pdf-header-footer",
            "--run-all-compositor-stages-before-draw",
            f"--virtual-time-budget={RENDER_BUDGET_MS}",
            f"--print-to-pdf={out}",
            url,
        ]
        try:
            subprocess.run(
                cmd,
                capture_output=True,
                timeout=TIMEOUT_S,
                creationflags=CREATE_NO_WINDOW,
                check=False,
            )
        except subprocess.TimeoutExpired as e:
            raise PdfError("The browser took too long to print the page") from e
        if not out.exists() or out.stat().st_size == 0:
            raise PdfError("The browser didn't produce a PDF")
        return out.read_bytes()
