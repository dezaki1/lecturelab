"""Tiny persistent UI state (last used subject etc.) in <data_dir>/state.json."""

from __future__ import annotations

import json
import os
from typing import Any

from backend.core.config import get_settings


def _path():  # type: ignore[no-untyped-def]
    return get_settings().data_dir / "state.json"


def load_state() -> dict[str, Any]:
    try:
        return json.loads(_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def update_state(**changes: Any) -> None:
    state = load_state() | changes
    tmp = _path().with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    os.replace(tmp, _path())
