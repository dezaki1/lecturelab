"""LAN mode: a second, token-protected listener so a tablet or phone can use LectureLab.

The normal server stays on 127.0.0.1. When LAN mode is on, a second uvicorn server serves the
same app on all interfaces (``lan.port``, default 8770), wrapped in ``LanGuard``:

- every ``/api`` request needs the pairing token, as an HttpOnly cookie (set by opening the
  pairing link ``/?token=...`` once, e.g. by scanning the QR code in Settings) or as
  ``Authorization: Bearer``. Without it: 401, and pages show a short "not paired" notice,
- some things stay PC-only (403): settings changes and API keys, LAN mode itself, starting or
  stopping recordings, adding calendar links and importing from links (the server would
  fetch any URL),
- requests through the guard carry ``scope["lan"] = True`` (``/api/system`` reports it, so the
  UI hides PC-only controls).

The token lives in Windows Credential Manager. "New pairing code" replaces it, which unpairs
every device. Traffic is plain HTTP, fine on a home network but not on public Wi-Fi.
"""

from __future__ import annotations

import contextlib
import ipaddress
import json
import logging
import secrets
import socket
import threading
import time
from http.cookies import SimpleCookie
from typing import Any
from urllib.parse import parse_qsl, urlencode

import uvicorn

from backend.core.config import get_settings

log = logging.getLogger(__name__)

COOKIE = "ll_lan"
SERVICE = "LectureLab"
TOKEN_NAME = "lan_token"
COOKIE_MAX_AGE = 400 * 24 * 3600  # browsers cap cookies at 400 days

# PC-only: (method or "*", path prefix)
BLOCKED = (
    ("*", "/api/lan"),
    ("WRITE", "/api/settings"),
    ("WRITE", "/api/recorder"),
    ("WRITE", "/api/timetable"),
    ("*", "/api/downloads"),
)
PUBLIC_PREFIXES = ("/assets/", "/icons/")
PUBLIC_FILES = ("/manifest.webmanifest", "/sw.js", "/favicon.svg")

PAIR_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>LectureLab</title>
<style>body{font-family:system-ui,sans-serif;max-width:32rem;margin:15vh auto;padding:0 1.5rem;
line-height:1.5;color:#222;background:#fafafa}@media(prefers-color-scheme:dark){body{color:#ddd;
background:#111}}h1{font-size:1.4rem}</style></head><body><h1>This device isn't paired</h1>
<p>On your PC, open LectureLab &rarr; <b>Settings &rarr; Tablet &amp; phone access</b> and scan the
QR code (or open the link shown there) on this device.</p></body></html>"""


# Token storage -------------------------------------------------------------------------------


def _keyring_get() -> str | None:
    try:
        import keyring

        return keyring.get_password(SERVICE, TOKEN_NAME)
    except Exception as e:
        log.warning("Could not read the LAN token: %s", type(e).__name__)
        return None


def _keyring_set(value: str) -> None:
    import keyring

    keyring.set_password(SERVICE, TOKEN_NAME, value)


# Replaced in tests so they never touch the real Credential Manager.
store: dict[str, Any] = {"get": _keyring_get, "set": _keyring_set}
_cache: dict[str, str | None] = {}


def get_token() -> str | None:
    if "token" not in _cache:
        _cache["token"] = store["get"]()
    return _cache["token"]


def new_token() -> str:
    token = secrets.token_urlsafe(24)
    store["set"](token)
    _cache["token"] = token
    return token


def ensure_token() -> str:
    return get_token() or new_token()


def valid(candidate: str | None) -> bool:
    token = get_token()
    return bool(token and candidate and secrets.compare_digest(candidate, token))


# The guard ------------------------------------------------------------------------------------


def _blocked(method: str, path: str) -> bool:
    write = method not in ("GET", "HEAD", "OPTIONS")
    return any(
        (path == prefix or path.startswith(prefix + "/")) and (m == "*" or write)
        for m, prefix in BLOCKED
    )


class LanGuard:
    """ASGI wrapper enforcing the pairing token on the LAN listener."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] not in ("http", "websocket"):
            return await self.app(scope, receive, send)
        scope = {**scope, "lan": True}
        path: str = scope["path"]
        method: str = scope.get("method", "GET")
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}
        cookie = SimpleCookie()
        with contextlib.suppress(Exception):
            cookie.load(headers.get("cookie", ""))
        sent = cookie[COOKIE].value if COOKIE in cookie else None
        auth = headers.get("authorization", "")
        bearer = auth[7:].strip() if auth.lower().startswith("bearer ") else None
        paired = valid(sent) or valid(bearer)

        query = parse_qsl(scope.get("query_string", b"").decode("latin-1"), keep_blank_values=True)
        offered = dict(query).get("token")
        if offered is not None and not path.startswith("/api/"):
            if not valid(offered):
                return await _respond(send, 401, PAIR_PAGE, "text/html")
            rest = urlencode([(k, v) for k, v in query if k != "token"])
            location = path + (f"?{rest}" if rest else "")
            set_cookie = (
                f"{COOKIE}={offered}; Max-Age={COOKIE_MAX_AGE}; Path=/; HttpOnly; SameSite=Lax"
            )
            return await _respond(
                send, 303, "", "text/plain", [("location", location), ("set-cookie", set_cookie)]
            )

        if path.startswith("/api/"):
            if not paired:
                body = json.dumps({"detail": "This device isn't paired with LectureLab"})
                return await _respond(send, 401, body, "application/json")
            if _blocked(method, path):
                body = json.dumps({"detail": "Only available on the PC running LectureLab"})
                return await _respond(send, 403, body, "application/json")
        elif not paired and not (path.startswith(PUBLIC_PREFIXES) or path in PUBLIC_FILES):
            return await _respond(send, 401, PAIR_PAGE, "text/html")
        await self.app(scope, receive, send)


async def _respond(
    send: Any, status: int, body: str, ctype: str, extra: list[tuple[str, str]] | None = None
) -> None:
    data = body.encode()
    headers = [
        (b"content-type", f"{ctype}; charset=utf-8".encode()),
        (b"content-length", str(len(data)).encode()),
        (b"cache-control", b"no-store"),
        *[(k.encode(), v.encode()) for k, v in (extra or [])],
    ]
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": data})


# Addresses ------------------------------------------------------------------------------------


def default_route_address() -> str | None:
    """This PC's address on the network its internet traffic goes through (Wi-Fi/LAN)."""
    with contextlib.suppress(OSError), socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.connect(("8.8.8.8", 53))  # UDP: nothing is sent, this only picks the route
        return str(s.getsockname()[0])
    return None


def order_addresses(default: str | None, others: list[str]) -> list[str]:
    """The default route's address first, whatever its kind: a university Wi-Fi hands out
    public addresses (e.g. JKU's 140.78.x.x). Then other private ones, which are often virtual
    adapters (Hyper-V, VirtualBox) a tablet can't reach."""
    out: list[str] = []
    for a in [default, *others]:
        if not a or a in out:
            continue
        ip = ipaddress.ip_address(a)
        if ip.is_loopback or ip.is_link_local or ip.is_unspecified:
            continue
        if a != default and not ip.is_private:
            continue
        out.append(a)
    return out


def lan_addresses() -> list[str]:
    others: list[str] = []
    with contextlib.suppress(OSError):
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            others.append(str(info[4][0]))
    return order_addresses(default_route_address(), others)


def is_public(address: str) -> bool:
    return not ipaddress.ip_address(address).is_private


# The second server ----------------------------------------------------------------------------


class LanServer:
    """Runs ``LanGuard(app)`` on ``lan.host:lan.port`` in a thread; start/stop at runtime."""

    def __init__(self, app: Any) -> None:
        self.app = app
        self.error = ""
        self._server: uvicorn.Server | None = None
        self._thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        return bool(self._server and self._server.started and not self._server.should_exit)

    @property
    def port(self) -> int | None:
        if not self.running:
            return None
        with contextlib.suppress(Exception):
            return int(self._server.servers[0].sockets[0].getsockname()[1])  # type: ignore[union-attr]
        return None

    def start(self, timeout: float = 5.0) -> bool:
        if self.running:
            return True
        cfg = get_settings().lan
        ensure_token()
        self.error = ""
        config = uvicorn.Config(
            LanGuard(self.app),
            host=cfg.host,
            port=cfg.port,
            lifespan="off",  # the main server owns startup/shutdown
            log_level="warning",
            access_log=False,
            timeout_graceful_shutdown=2,
        )
        server = uvicorn.Server(config)
        self._server = server

        def run() -> None:
            try:
                server.run()
            except BaseException as e:  # uvicorn exits via SystemExit when the port is taken
                self.error = f"Could not listen on port {cfg.port}: {type(e).__name__}"
            if not server.started and not self.error:
                self.error = f"Could not listen on port {cfg.port} (is it in use?)"

        self._thread = threading.Thread(target=run, name="lan-server", daemon=True)
        self._thread.start()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and self._thread.is_alive() and not server.started:
            time.sleep(0.05)
        if not server.started:
            server.should_exit = True
            self.error = self.error or "LAN server did not start"
            log.warning(self.error)
            return False
        log.info("LAN mode on: port %s", self.port)
        return True

    def stop(self) -> None:
        if self._server is not None:
            self._server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._server = None
        self._thread = None
