"""Backups to a folder the user chooses (e.g. inside OneDrive, so it syncs to the cloud).

Two parts, so a semester's audio isn't zipped and uploaded again every day:

- ``snapshots/lecturelab-<timestamp>.zip``: a consistent copy of the database (SQLite backup
  API, safe while the app runs), config.toml and state.json. Rotated: the newest per day for
  ``keep_daily`` days plus the newest per week for ``keep_weekly`` weeks.
- ``files/subjects/...``: an incremental mirror of the lecture folders (audio, slides, PDFs,
  transcripts). Only new or changed files are copied; files deleted in the app stay in the
  backup.

Not backed up: Whisper models (downloaded again), temp files, raw recording PCM, logs, the
trash folder, and the API key (it lives in Windows Credential Manager).
"""

from __future__ import annotations

import logging
import shutil
import sqlite3
import threading
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from backend.core.config import get_settings
from backend.core.events import bus
from backend.core.state import load_state, update_state

log = logging.getLogger(__name__)

SNAPSHOTS = "snapshots"
FILES = "files"
MIRRORED = ("subjects",)
SKIP_SUFFIXES = {".pcm", ".tmp", ".part"}
STAMP = "%Y-%m-%d_%H%M%S"
README = """LectureLab backup
=================
snapshots/  database + settings, one zip per backup (newest per day/week kept)
files/      copies of the lecture folders (audio, slides, PDFs, transcripts)

Restore into a new data folder:
    uv run lecturelab restore "<this folder>" --to "<new data folder>"
Your Anthropic API key is not in the backup; add it again in Settings.
"""

_lock = threading.Lock()


class BackupError(Exception):
    pass


def _skip(path: Path) -> bool:
    return path.suffix.lower() in SKIP_SUFFIXES or path.name.endswith(".part.m4a")


def check_folder(folder: Path) -> Path:
    folder = folder.expanduser()
    data = get_settings().data_dir.resolve()
    if not folder.is_absolute():
        raise BackupError("Use a full path, e.g. C:\\Users\\you\\OneDrive\\LectureLab backup")
    if folder.resolve() == data or data in folder.resolve().parents:
        raise BackupError("The backup folder can't be inside the LectureLab data folder")
    try:
        folder.mkdir(parents=True, exist_ok=True)
        probe = folder / ".lecturelab-write-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as e:
        raise BackupError(f"Can't write to {folder}: {e}") from e
    return folder


def snapshot(dest: Path, now: datetime) -> Path:
    """Zip a consistent copy of the database plus the settings files."""
    settings = get_settings()
    dest.mkdir(parents=True, exist_ok=True)
    tmp_db = settings.tmp_dir / f"backup-{now:{STAMP}}.db"
    src = sqlite3.connect(settings.db_path)
    try:
        out = sqlite3.connect(tmp_db)
        with out:
            src.backup(out)
        out.close()
    finally:
        src.close()
    target = dest / f"lecturelab-{now:{STAMP}}.zip"
    part = target.with_suffix(".zip.part")
    try:
        with zipfile.ZipFile(part, "w", zipfile.ZIP_DEFLATED) as z:
            z.write(tmp_db, "lecturelab.db")
            for name in ("config.toml", "state.json"):
                if (settings.data_dir / name).exists():
                    z.write(settings.data_dir / name, name)
        part.replace(target)
    finally:
        tmp_db.unlink(missing_ok=True)
        part.unlink(missing_ok=True)
    return target


def mirror(dest: Path) -> tuple[int, int]:
    """Copy new or changed lecture files. Returns (files copied, bytes copied)."""
    data = get_settings().data_dir
    copied = size = 0
    for top in MIRRORED:
        root = data / top
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if not path.is_file() or _skip(path):
                continue
            rel = path.relative_to(data)
            target = dest / rel
            st = path.stat()
            if target.exists():
                tst = target.stat()
                if tst.st_size == st.st_size and tst.st_mtime >= st.st_mtime - 1:
                    continue
            target.parent.mkdir(parents=True, exist_ok=True)
            part = target.with_name(target.name + ".part")
            shutil.copy2(path, part)
            part.replace(target)
            copied += 1
            size += st.st_size
    return copied, size


def rotate(dest: Path, keep_daily: int, keep_weekly: int) -> list[str]:
    """Keep the newest snapshot of each of the last ``keep_daily`` days and of each of the
    last ``keep_weekly`` ISO weeks; delete the rest. Returns the deleted names."""
    snaps = []
    for p in dest.glob("lecturelab-*.zip"):
        try:
            snaps.append((datetime.strptime(p.stem.removeprefix("lecturelab-"), STAMP), p))
        except ValueError:
            continue
    snaps.sort(reverse=True)
    keep: set[Path] = set()
    days: list[object] = []
    weeks: list[object] = []
    for t, p in snaps:
        day, week = t.date(), t.isocalendar()[:2]
        if day not in days and len(days) < keep_daily:
            days.append(day)
            keep.add(p)
        if week not in weeks and len(weeks) < keep_weekly:
            weeks.append(week)
            keep.add(p)
    deleted = []
    for _, p in snaps:
        if p not in keep:
            p.unlink(missing_ok=True)
            deleted.append(p.name)
    return deleted


def run_backup() -> dict[str, Any]:
    """Back up now. Raises BackupError (and records it) if the folder isn't usable."""
    cfg = get_settings().backup
    if not cfg.folder:
        raise BackupError("Choose a backup folder first")
    if not _lock.acquire(blocking=False):
        raise BackupError("A backup is already running")
    started = datetime.now(UTC)
    bus.publish("backup", {"status": "running"})
    try:
        folder = check_folder(Path(cfg.folder))
        (folder / "README.txt").write_text(README, encoding="utf-8")
        snap = snapshot(folder / SNAPSHOTS, started.astimezone())
        copied, size = mirror(folder / FILES)
        rotate(folder / SNAPSHOTS, cfg.keep_daily, cfg.keep_weekly)
        result = {
            "ok": True,
            "at": started.isoformat(),
            "seconds": round((datetime.now(UTC) - started).total_seconds(), 1),
            "snapshot": snap.name,
            "files_copied": copied,
            "bytes_copied": size,
            "error": "",
        }
        update_state(last_backup=result, last_backup_ok=result["at"])
        log.info("Backup done: %s, %d files (%d MB)", snap.name, copied, size // 1_000_000)
        return result
    except Exception as e:
        result = {"ok": False, "at": started.isoformat(), "error": str(e)}
        update_state(last_backup=result)
        log.warning("Backup failed: %s", e)
        bus.publish("notify", {"title": "Backup failed", "message": str(e), "level": "error"})
        if isinstance(e, BackupError):
            raise
        raise BackupError(str(e)) from e
    finally:
        _lock.release()
        bus.publish("backup", {"status": "idle"})


def status() -> dict[str, Any]:
    cfg = get_settings().backup
    state = load_state()
    snaps: list[dict[str, Any]] = []
    if cfg.folder and (Path(cfg.folder) / SNAPSHOTS).exists():
        for p in sorted((Path(cfg.folder) / SNAPSHOTS).glob("lecturelab-*.zip"), reverse=True):
            snaps.append({"name": p.name, "bytes": p.stat().st_size})
    return {
        "folder": str(cfg.folder) if cfg.folder else "",
        "enabled": cfg.enabled,
        "interval_hours": cfg.interval_hours,
        "last": state.get("last_backup"),
        "running": _lock.locked(),
        "snapshots": snaps,
    }


def due(now: datetime | None = None) -> bool:
    cfg = get_settings().backup
    if not (cfg.enabled and cfg.folder):
        return False
    last = load_state().get("last_backup_ok")
    if not last:
        return True
    now = now or datetime.now(UTC)
    return now - datetime.fromisoformat(last) >= timedelta(hours=cfg.interval_hours)


class BackupScheduler:
    """Checks every few minutes and backs up when the interval has passed."""

    def __init__(self, check_s: float = 600.0) -> None:
        self.check_s = check_s
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="backup", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        # Give the app a minute to start (and the user to begin recording) first.
        if self._stop.wait(60):
            return
        while not self._stop.is_set():
            try:
                if due():
                    run_backup()
            except BackupError:
                pass  # recorded in state and notified
            except Exception:
                log.exception("Backup scheduler error")
            self._stop.wait(self.check_s)


def restore(
    source: Path, target: Path, snapshot_name: str | None = None, force: bool = False
) -> str:
    """Rebuild a data folder from a backup folder. Returns the snapshot used."""
    snaps = sorted((source / SNAPSHOTS).glob("lecturelab-*.zip"))
    if not snaps:
        raise BackupError(f"No snapshots in {source / SNAPSHOTS}")
    snap = next((p for p in snaps if p.name == snapshot_name), None) if snapshot_name else snaps[-1]
    if snap is None:
        raise BackupError(f"Snapshot {snapshot_name} not found")
    if (target / "lecturelab.db").exists() and not force:
        raise BackupError(f"{target} already has a database; use --force to overwrite it")
    target.mkdir(parents=True, exist_ok=True)
    for stale in ("lecturelab.db-wal", "lecturelab.db-shm"):
        (target / stale).unlink(missing_ok=True)
    with zipfile.ZipFile(snap) as z:
        z.extractall(target)
    files = source / FILES
    if files.exists():
        shutil.copytree(files, target, dirs_exist_ok=True)
    return snap.name
