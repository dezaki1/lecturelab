"""Import from a link: real yt-dlp against a local HTTP server (no internet)."""

from __future__ import annotations

import shutil
import subprocess
import threading
from collections.abc import Iterator
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

import pytest
from fastapi.testclient import TestClient

from backend.core.config import Settings
from backend.core.jobs import Worker
from backend.core.lan import _blocked
from backend.pipeline.download import clean_tmp, parse_progress, write_cookies

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not on PATH")

PAGE = """<!doctype html><html><head><title>VL Algorithms - Week 3</title></head>
<body><video controls src="lecture.mp4"></video></body></html>"""


class Handler(SimpleHTTPRequestHandler):
    """Static files; anything under /private/ needs the cookie session=abc."""

    def do_GET(self) -> None:
        if self.path.startswith("/private/"):
            if "session=abc" not in (self.headers.get("Cookie") or ""):
                self.send_error(403)
                return
            self.path = self.path.removeprefix("/private")
        super().do_GET()

    def log_message(self, *args: object) -> None:
        pass


@pytest.fixture(scope="module")
def site(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    root = tmp_path_factory.mktemp("site")
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y",
         "-f", "lavfi", "-i", "testsrc=size=160x120:rate=10:duration=3",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
         "-shortest", "-c:v", "libx264", "-c:a", "aac", str(root / "lecture.mp4")],
        check=True,
    )  # fmt: skip
    (root / "page.html").write_text(PAGE, encoding="utf-8")
    (root / "empty.html").write_text("<html><body>No video</body></html>", encoding="utf-8")
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(Handler, directory=str(root)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


@pytest.fixture
def sid(client: TestClient) -> int:
    return client.post("/api/subjects", json={"name": "Algorithms"}).json()["id"]


def _lecture(client: TestClient, lid: int) -> dict:
    return client.get(f"/api/lectures/{lid}").json()


def test_direct_media_link_becomes_a_lecture(
    client: TestClient, settings: Settings, site: str, sid: int
) -> None:
    r = client.post(
        "/api/downloads",
        json={"url": f"{site}/lecture.mp4", "subject_id": sid, "kind": "VL"},
        headers={"Origin": "chrome-extension://abcdef"},
    )
    assert r.status_code == 201, r.text
    lec = r.json()
    assert lec["status"] == "processing"
    assert lec["title"] == "Video from 127.0.0.1"
    Worker().run_pending()

    lec = _lecture(client, lec["id"])
    assert lec["stages"]["capture"] == "done", lec
    (audio,) = settings.subjects_dir.glob("*/*/audio.m4a")
    folder = audio.parent
    assert not list(folder.glob("source.*")), "the downloaded copy is removed after import"
    assert not list(folder.glob("download.*"))


def test_page_link_uses_the_page_title(
    client: TestClient, settings: Settings, site: str, sid: int
) -> None:
    info = client.post("/api/downloads/probe", json={"url": f"{site}/page.html"})
    assert info.status_code == 200, info.text
    assert info.json()["has_video"] is True

    lid = client.post(
        "/api/downloads", json={"url": f"{site}/page.html", "subject_id": sid, "video": False}
    ).json()["id"]
    Worker().run_pending()
    lec = _lecture(client, lid)
    assert lec["stages"]["capture"] == "done", lec
    assert lec["title"] == "VL Algorithms - Week 3"


def test_cookies_unlock_private_links(
    client: TestClient, settings: Settings, site: str, sid: int
) -> None:
    url = f"{site}/private/lecture.mp4"
    denied = client.post("/api/downloads", json={"url": url, "subject_id": sid}).json()
    Worker().run_pending()
    lec = _lecture(client, denied["id"])
    assert lec["status"] == "failed"
    assert "403" in lec["error"]

    cookies = "127.0.0.1\tFALSE\t/\tFALSE\t0\tsession\tabc\n"
    ok = client.post(
        "/api/downloads", json={"url": url, "subject_id": sid, "cookies": cookies}
    ).json()
    assert len(list(settings.tmp_dir.glob("cookies-*.txt"))) == 1
    Worker().run_pending()
    assert _lecture(client, ok["id"])["stages"]["capture"] == "done"
    assert not list(settings.tmp_dir.glob("cookies-*.txt")), "cookies are deleted after use"


def test_bad_links_and_foreign_websites_are_refused(client: TestClient, sid: int) -> None:
    for url in ("file:///C:/secret.txt", "ftp://example.com/x.mp4", "not a link"):
        r = client.post("/api/downloads", json={"url": url, "subject_id": sid})
        assert r.status_code == 422, url
    r = client.post(
        "/api/downloads",
        json={"url": "https://example.com/v.mp4", "subject_id": sid},
        headers={"Origin": "https://evil.example"},
    )
    assert r.status_code == 403
    r = client.post(
        "/api/downloads",
        json={"url": "https://example.com/v.mp4", "subject_id": 999},
        headers={"Origin": "http://testserver"},
    )
    assert r.status_code == 404
    assert _blocked("POST", "/api/downloads")
    assert _blocked("POST", "/api/downloads/probe")


def test_probe_reports_yt_dlp_errors(client: TestClient, site: str) -> None:
    r = client.post("/api/downloads/probe", json={"url": f"{site}/missing.mp4"})
    assert r.status_code == 422
    assert "404" in r.json()["detail"]
    r = client.post("/api/downloads/probe", json={"url": f"{site}/empty.html"})
    assert r.status_code == 422
    assert r.json()["detail"].startswith("No video found on this page")


def test_parse_progress() -> None:
    assert parse_progress("LLPROGRESS 50 100 NA NA NA") == 0.5
    assert parse_progress("LLPROGRESS 50 NA 200 NA NA") == 0.25
    assert parse_progress("LLPROGRESS 999 NA NA 3 12") == 0.25
    assert parse_progress("LLPROGRESS NA NA NA NA NA") is None
    assert parse_progress("LLPROGRESS garbage") is None


def test_cookie_files(settings: Settings) -> None:
    settings.tmp_dir.mkdir(parents=True, exist_ok=True)
    assert write_cookies("# only a comment\n") is None
    path = write_cookies(".jku.at\tTRUE\t/\tTRUE\t0\tMoodleSession\tx")
    assert path is not None
    assert path.read_text().startswith("# Netscape HTTP Cookie File\n")
    clean_tmp(now=path.stat().st_mtime + 3600)
    assert path.exists()
    clean_tmp(now=path.stat().st_mtime + 3 * 24 * 3600)
    assert not path.exists()
