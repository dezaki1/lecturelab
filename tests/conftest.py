from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.core.config import Settings, load_settings


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return make_settings(tmp_path / "data")


def make_settings(data_dir: Path) -> Settings:
    # Fake transcription engine: no Whisper model needed (see test_transcribe for the real one).
    return load_settings(data_dir, open_browser=False, tray=False, transcription={"engine": "fake"})


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    app = create_app(settings, start_worker=False)
    with TestClient(app) as c:
        yield c
