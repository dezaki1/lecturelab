"""Settings for LAN mode (tablet & phone access). PC only: the LAN guard blocks /api/lan."""

from __future__ import annotations

from typing import Any

import segno
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from backend.core import lan
from backend.core.config import get_settings
from backend.core.state import load_state, update_state

router = APIRouter(prefix="/api/lan", tags=["lan"])


class LanSettings(BaseModel):
    enabled: bool | None = None
    port: int | None = Field(default=None, ge=1024, le=65535)


def apply_saved_settings(settings) -> None:  # type: ignore[no-untyped-def]
    saved = load_state().get("lan") or {}
    for k in ("enabled", "port"):
        if k in saved:
            setattr(settings.lan, k, saved[k])


def _server(request: Request) -> lan.LanServer:
    if request.scope.get("lan"):
        raise HTTPException(403, "Only available on the PC running LectureLab")
    return request.app.state.lan


def _status(srv: lan.LanServer) -> dict[str, Any]:
    cfg = get_settings().lan
    port = srv.port or cfg.port
    addresses = lan.lan_addresses()
    urls = [f"http://{ip}:{port}/" for ip in addresses]
    out: dict[str, Any] = {
        "enabled": cfg.enabled,
        "running": srv.running,
        "port": port,
        "error": srv.error,
        "urls": urls,
        # Not a home network (e.g. university Wi-Fi): others on it may reach the port, traffic
        # is unencrypted, and such networks usually block device-to-device connections anyway.
        "public_network": bool(addresses) and lan.is_public(addresses[0]),
        "pair_url": None,
        "qr_svg": None,
        # Away from home: the same server through Tailscale (the user's private network).
        "tailscale_url": None,
        "tailscale_pair_url": None,
        "tailscale_qr_svg": None,
    }
    if srv.running and urls:
        out["pair_url"] = pair = f"{urls[0]}?token={lan.ensure_token()}"
        out["qr_svg"] = _qr(pair)
    if ts := lan.tailscale_address():
        out["tailscale_url"] = url = f"http://{ts}:{port}/"
        if srv.running:
            out["tailscale_pair_url"] = pair = f"{url}?token={lan.ensure_token()}"
            out["tailscale_qr_svg"] = _qr(pair)
    return out


def _qr(text: str) -> str:
    # viewBox (omitsize) so the page can scale it without cropping; 4-module quiet zone, which
    # phone cameras need to find the code.
    return str(segno.make(text, error="m").svg_inline(scale=5, border=4, omitsize=True))


@router.get("")
def status(request: Request) -> dict[str, Any]:
    return _status(_server(request))


@router.put("")
def put_settings(req: LanSettings, request: Request) -> dict[str, Any]:
    srv = _server(request)
    cfg = get_settings().lan
    restart = req.port is not None and req.port != cfg.port and srv.running
    if req.port is not None:
        cfg.port = req.port
    if req.enabled is not None:
        cfg.enabled = req.enabled
    update_state(lan={"enabled": cfg.enabled, "port": cfg.port})
    if restart or not cfg.enabled:
        srv.stop()
    if cfg.enabled and not srv.running:
        srv.start()
    return _status(srv)


@router.post("/token")
def rotate_token(request: Request) -> dict[str, Any]:
    """New pairing code: every paired device has to scan again."""
    srv = _server(request)
    lan.new_token()
    return _status(srv)
