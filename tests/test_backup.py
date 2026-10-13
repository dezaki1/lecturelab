from __future__ import annotations

import shutil
import sqlite3
import zipfile
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.core import backup
from backend.core.config import Settings
from backend.core.jobs import Worker
from backend.core.state import update_state
from tests.fakes import fake_factory

FIXTURES = Path(__file__).parent / "fixtures"
pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not on PATH")


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(settings, start_worker=False, source_factory=fake_factory())) as c:
        yield c


def _lecture(c: TestClient) -> int:
    sid = c.post("/api/subjects", json={"name": "ML"}).json()["id"]
    lid = c.post(
        "/api/imports",
        params={"subject_id": sid, "filename": "lecture.m4a"},
        content=(FIXTURES / "lecture.m4a").read_bytes(),
    ).json()["id"]
    Worker().run_pending()
    return lid


def test_backup_api_snapshot_mirror_and_restore(
    client: TestClient, settings: Settings, tmp_path: Path
) -> None:
    _lecture(client)
    dest = tmp_path / "OneDrive" / "LectureLab backup"
    r = client.put("/api/settings/backup", json={"folder": str(dest), "enabled": True})
    assert r.status_code == 200 and r.json()["folder"] == str(dest) and r.json()["enabled"]
    bad = client.put("/api/settings/backup", json={"folder": str(settings.data_dir / "x")})
    assert bad.status_code == 422 and "inside the LectureLab data folder" in bad.json()["detail"]

    first = client.post("/api/settings/backup/run").json()
    assert first["ok"] and first["files_copied"] >= 2  # audio.m4a + transcript.json at least
    snap = dest / "snapshots" / first["snapshot"]
    with zipfile.ZipFile(snap) as z:
        assert {"lecturelab.db", "state.json"} <= set(z.namelist())
        z.extract("lecturelab.db", tmp_path / "check")
    db = sqlite3.connect(tmp_path / "check" / "lecturelab.db")
    assert db.execute("select count(*) from lecture").fetchone()[0] == 1
    db.close()
    mirrored = list((dest / "files" / "subjects").rglob("audio.m4a"))
    assert len(mirrored) == 1
    assert (dest / "README.txt").exists()

    again = client.post("/api/settings/backup/run").json()
    assert again["files_copied"] == 0  # nothing changed: incremental
    status = client.get("/api/settings/backup").json()
    assert status["last"]["ok"] and len(status["snapshots"]) >= 1
    assert status["suggested_folder"].endswith("LectureLab backup")

    target = tmp_path / "restored"
    used = backup.restore(dest, target)
    assert (target / "lecturelab.db").exists() and list((target / "subjects").rglob("audio.m4a"))
    assert used == sorted(p.name for p in (dest / "snapshots").glob("*.zip"))[-1]
    with pytest.raises(backup.BackupError, match="already has a database"):
        backup.restore(dest, target)


def test_rotation_keeps_newest_per_day_and_week(tmp_path: Path) -> None:
    start = datetime(2026, 10, 13, 9, 0)
    names = []
    for d in range(40):  # 40 days, two backups per day
        for h in (9, 21):
            t = start - timedelta(days=d) + timedelta(hours=h - 9)
            p = tmp_path / f"lecturelab-{t:{backup.STAMP}}.zip"
            p.write_bytes(b"x")
            names.append(p.name)
    deleted = backup.rotate(tmp_path, keep_daily=7, keep_weekly=4)
    kept = sorted(p.name for p in tmp_path.glob("*.zip"))
    assert len(kept) + len(deleted) == 80
    assert "lecturelab-2026-10-13_210000.zip" in kept  # newest overall
    assert "lecturelab-2026-10-13_090000.zip" not in kept  # older one of the same day
    assert 7 <= len(kept) <= 7 + 4


def test_due_and_folder_checks(client: TestClient, settings: Settings, tmp_path: Path) -> None:
    assert not backup.due()  # disabled by default
    settings.backup.enabled, settings.backup.folder = True, tmp_path / "b"
    assert backup.due()  # never backed up
    now = datetime.now(UTC)
    update_state(last_backup_ok=(now - timedelta(hours=2)).isoformat())
    assert not backup.due(now)
    update_state(last_backup_ok=(now - timedelta(hours=25)).isoformat())
    assert backup.due(now)
    with pytest.raises(backup.BackupError, match="full path"):
        backup.check_folder(Path("relative/folder"))
