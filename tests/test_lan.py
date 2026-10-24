from __future__ import annotations

import socket
from collections.abc import Iterator

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.core import lan
from backend.core.config import Settings


@pytest.fixture
def app(settings: Settings):  # type: ignore[no-untyped-def]
    return create_app(settings, start_worker=False)


@pytest.fixture
def local(app) -> Iterator[TestClient]:  # type: ignore[no-untyped-def]
    with TestClient(app) as c:
        yield c


def test_guard_requires_pairing(app, local: TestClient) -> None:  # type: ignore[no-untyped-def]
    token = lan.ensure_token()
    remote = TestClient(lan.LanGuard(app), follow_redirects=False)
    assert remote.get("/api/subjects").status_code == 401
    r = remote.get("/")
    assert r.status_code == 401 and "isn't paired" in r.text
    assert remote.get("/manifest.webmanifest").status_code != 401  # public PWA files
    assert remote.get("/?token=wrong").status_code == 401

    r = remote.get(f"/review?token={token}&x=1")
    assert r.status_code == 303 and r.headers["location"] == "/review?x=1"
    cookie = r.headers["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=Lax" in cookie and token in cookie
    remote.cookies.set(lan.COOKIE, token)
    assert remote.get("/api/subjects").status_code == 200
    assert remote.get("/api/system").json()["remote"] is True
    assert local.get("/api/system").json()["remote"] is False

    # PC-only things.
    assert remote.get("/api/lan").status_code == 403
    assert remote.put("/api/settings/provider", json={"provider": "mock"}).status_code == 403
    assert remote.post("/api/recorder/start", json={}).status_code == 403
    assert (
        remote.post("/api/timetable/sources", json={"url": "https://x.test/a.ics"}).status_code
        == 403
    )
    assert remote.get("/api/settings/llm").status_code != 403  # reading is fine
    assert remote.post("/api/subjects", json={"name": "From the tablet"}).status_code == 201

    # Bearer works too; a new pairing code unpairs everyone.
    bearer = TestClient(lan.LanGuard(app))
    assert (
        bearer.get("/api/subjects", headers={"Authorization": f"Bearer {token}"}).status_code == 200
    )
    local.post("/api/lan/token")
    assert remote.get("/api/subjects").status_code == 401


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_lan_server_start_stop(settings: Settings, local: TestClient) -> None:
    settings.lan.host = "127.0.0.1"  # tests never listen on the network
    port = _free_port()
    r = local.put("/api/lan", json={"enabled": True, "port": port})
    st = r.json()
    try:
        assert r.status_code == 200 and st["running"] and st["port"] == port, st
        base = f"http://127.0.0.1:{port}"
        assert httpx.get(f"{base}/api/health").status_code == 401
        token = lan.get_token()
        ok = httpx.get(f"{base}/api/health", headers={"Authorization": f"Bearer {token}"})
        assert ok.status_code == 200
        if st["urls"]:
            assert st["pair_url"].endswith(f"?token={token}") and st["qr_svg"].startswith("<svg")
            assert "viewBox=" in st["qr_svg"] and "width=" not in st["qr_svg"]  # scales, no crop
        assert local.get("/api/lan").json()["running"]
    finally:
        st = local.put("/api/lan", json={"enabled": False}).json()
    assert not st["running"]
    with pytest.raises(httpx.TransportError):  # refused (Windows may time out first)
        httpx.get(f"http://127.0.0.1:{port}/api/health", timeout=2)


def test_port_in_use(settings: Settings, local: TestClient) -> None:
    settings.lan.host = "127.0.0.1"
    with socket.socket() as busy:
        busy.bind(("127.0.0.1", 0))
        busy.listen()
        port = busy.getsockname()[1]
        st = local.put("/api/lan", json={"enabled": True, "port": port}).json()
        assert not st["running"] and st["error"]
    local.put("/api/lan", json={"enabled": False})


def test_address_order() -> None:
    # University Wi-Fi with a public address is the real network; virtual adapters come after.
    assert lan.order_addresses("140.78.231.249", ["192.168.64.1", "192.168.56.1"]) == [
        "140.78.231.249",
        "192.168.64.1",
        "192.168.56.1",
    ]
    assert lan.is_public("140.78.231.249") and not lan.is_public("192.168.0.100")
    # Home Wi-Fi: no duplicates; loopback, link-local and other public addresses are dropped.
    assert lan.order_addresses(
        "192.168.0.100", ["192.168.0.100", "127.0.0.1", "169.254.3.4", "8.8.4.4", "10.0.0.5"]
    ) == ["192.168.0.100", "10.0.0.5"]
    assert lan.order_addresses(None, ["192.168.56.1"]) == ["192.168.56.1"]  # offline


def test_tailscale_address(monkeypatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
    """Tailscale's 100.x addresses aren't "private" to Python, so they come from Tailscale."""
    exe = tmp_path / "tailscale.exe"
    exe.write_text("")
    monkeypatch.setattr(lan.shutil, "which", lambda name: str(exe))
    monkeypatch.setattr(
        lan.subprocess,
        "run",
        lambda *a, **k: type("R", (), {"stdout": "100.90.155.90\n"})(),
    )
    assert lan.tailscale_address() == "100.90.155.90"
    assert not lan.is_public("100.90.155.90")  # no "public network" warning for it
    monkeypatch.setattr(lan.shutil, "which", lambda name: None)
    monkeypatch.setattr(lan, "TAILSCALE_EXE", tmp_path / "missing.exe")
    assert lan.tailscale_address() is None
