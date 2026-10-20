"""Start LectureLab from the desktop/Start-menu shortcut: no terminal window.

Run as ``pythonw -m backend.launcher`` (what ``lecturelab shortcut`` puts in the shortcut).
If LectureLab is already running, it only opens the window. Otherwise it brings the
environment and the built frontend up to date (like ``start.ps1``, without a console), then
runs the server; the tray icon has Open and Quit. Problems show as a message box; details
go to ``logs/launcher.log``.
"""

from __future__ import annotations

import os
import subprocess
import sys
import traceback
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
FRONTEND = REPO / "frontend"
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def message(text: str, error: bool = True) -> None:
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, text, "LectureLab", 0x10 if error else 0x40)
    except Exception:
        pass


def _newest(*paths: Path) -> float:
    best = 0.0
    for p in paths:
        if p.is_file():
            best = max(best, p.stat().st_mtime)
        elif p.is_dir():
            for f in p.rglob("*"):
                if f.is_file():
                    best = max(best, f.stat().st_mtime)
    return best


def _run(cmd: list[str], log, cwd: Path) -> int:  # type: ignore[no-untyped-def]
    log.write(f"$ {' '.join(cmd)}\n")
    log.flush()
    return subprocess.call(
        cmd, cwd=cwd, stdout=log, stderr=subprocess.STDOUT, creationflags=CREATE_NO_WINDOW
    )


def update(log) -> None:  # type: ignore[no-untyped-def]
    """Dependencies after a pull, and the frontend when its sources are newer than the build."""
    stamp = REPO / ".venv" / ".lecturelab-synced"
    if _newest(REPO / "uv.lock", REPO / "pyproject.toml") > (
        stamp.stat().st_mtime if stamp.exists() else 0
    ):
        uv = "uv.exe" if os.name == "nt" else "uv"
        if _run([uv, "sync", "--inexact", "--no-install-project"], log, REPO) == 0:
            stamp.touch()
    dist = FRONTEND / "dist" / "index.html"
    built = dist.stat().st_mtime if dist.exists() else 0.0
    sources = _newest(FRONTEND / "src", FRONTEND / "index.html", FRONTEND / "package-lock.json")
    if sources > built:
        npm = "npm.cmd" if os.name == "nt" else "npm"
        try:
            if not (FRONTEND / "node_modules").exists():
                _run([npm, "install", "--no-fund", "--no-audit"], log, FRONTEND)
            code = _run([npm, "run", "build"], log, FRONTEND)
        except OSError:
            code = 1
        if code != 0 and not dist.exists():
            raise RuntimeError("Building the interface failed (is Node.js installed?)")


def main() -> None:
    from backend.core.config import load_settings
    from backend.core.util import write_problem

    settings = load_settings(None)
    logs = settings.logs_dir
    # pythonw has nowhere to print: without this, a blocked data folder (Controlled Folder
    # Access) meant the app silently never started.
    if problem := write_problem(logs):
        message(f"LectureLab can't start.\n\n{problem}")
        return
    log = open(logs / "launcher.log", "a", encoding="utf-8", buffering=1)  # noqa: SIM115
    # pythonw has no console: send print()/stray output to the log instead of nowhere.
    sys.stdout = sys.stderr = log
    try:
        from backend.core.config import set_settings
        from backend.core.util import instance_lock
        from backend.main import _already_running
        from backend.main import main as cli

        set_settings(settings)
        # Only the copy that gets the lock updates and builds; for any other click serve()
        # just opens the window (waiting for the copy that is still starting).
        lock = settings.data_dir / ".lecturelab.lock"
        if not _already_running(settings) and instance_lock(lock) is not None:
            update(log)
        cli(["serve"])
    except SystemExit:
        pass
    except Exception as e:
        traceback.print_exc()
        message(f"LectureLab could not start:\n\n{e}\n\nDetails: {logs / 'launcher.log'}")


if __name__ == "__main__":
    main()
