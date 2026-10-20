from __future__ import annotations

import os
import re
import unicodedata
from pathlib import Path
from typing import IO

_GERMAN = str.maketrans(
    {"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss", "Ä": "Ae", "Ö": "Oe", "Ü": "Ue"}
)


def slugify(text: str, max_len: int = 60) -> str:
    text = text.translate(_GERMAN)
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return text[:max_len].rstrip("-") or "untitled"


def format_hms(seconds: float) -> str:
    s = int(seconds)
    h, rem = divmod(s, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def write_blocked(folder: Path) -> str:
    return (
        f"Windows is not letting LectureLab save files in {folder}. This is usually Controlled "
        "Folder Access (Windows Security > Virus & threat protection > Ransomware protection): "
        "allow Python through it, or take this folder off the protected folders."
    )


def write_problem(folder: Path) -> str | None:
    """None if a new file can be created in ``folder``, else an explanation for the user."""
    probe = folder / f".write-test-{os.getpid()}"
    try:
        folder.mkdir(parents=True, exist_ok=True)
        probe.write_bytes(b"")
        probe.unlink()
    except OSError:
        return write_blocked(folder)
    return None


_held: dict[Path, IO[bytes]] = {}


def instance_lock(path: Path) -> IO[bytes] | None:
    """Hold an exclusive lock on ``path`` for the life of the process: the open file, or None if
    another process has it. The OS drops the lock when the process ends, even after a crash.
    Asking again in the same process (launcher, then serve) returns the lock already held."""
    path = path.resolve()
    if path in _held:
        return _held[path]
    f = open(path, "a+b")  # noqa: SIM115  (kept open: closing it releases the lock)
    try:
        if os.name == "nt":
            import msvcrt

            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        f.close()
        return None
    _held[path] = f
    return f
