from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.core.config import Settings, load_settings


@pytest.fixture(autouse=True)
def no_real_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests must never reach a real LLM or the internet, even when code builds its own settings
    (the CLI) and the developer's API key sits in the Credential Manager. Real HTTP connections
    are refused except to this machine (LAN-mode tests); local Ollama's port is refused too.
    Fake clients and the in-process TestClient transport are unaffected."""
    from urllib.parse import urlsplit

    import httpx
    import httpx2

    def guard(original):  # type: ignore[no-untyped-def]
        def handle_request(self, request):  # type: ignore[no-untyped-def]
            url = urlsplit(str(request.url))
            if url.hostname not in ("127.0.0.1", "localhost", "::1") or url.port == 11434:
                raise AssertionError(f"Test tried to reach the network: {request.url}")
            return original(self, request)

        return handle_request

    for mod in (httpx, httpx2):
        monkeypatch.setattr(
            mod.HTTPTransport, "handle_request", guard(mod.HTTPTransport.handle_request)
        )


@pytest.fixture(autouse=True)
def lan_token_store(monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, str]]:
    """Keep the LAN pairing token in memory, never in the real Credential Manager."""
    from backend.core import lan

    box: dict[str, str] = {}
    monkeypatch.setitem(lan.store, "get", lambda: box.get("token"))
    monkeypatch.setitem(lan.store, "set", lambda v: box.__setitem__("token", v))
    lan._cache.clear()
    yield box
    lan._cache.clear()


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return make_settings(tmp_path / "data")


def make_settings(data_dir: Path) -> Settings:
    # Fake transcription engine (no Whisper model) and the mock LLM provider: tests never call
    # real APIs. Auto-generation is off by default; test_generate turns it on explicitly.
    return load_settings(
        data_dir,
        open_browser=False,
        tray=False,
        transcription={"engine": "fake"},
        llm={"provider": "mock"},
        generation={"auto": False, "cleanup": False, "subject_hub": False},
        search={"embed_provider": "mock"},
        slides={"enabled": False},  # never grab the real screen; test_slides uses a fake
        voice={"tts_engine": "fake"},  # no Piper model needed
    )


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    app = create_app(settings, start_worker=False)
    with TestClient(app) as c:
        yield c
