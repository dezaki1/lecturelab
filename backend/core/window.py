"""Open the UI: an app window (Edge/Chrome ``--app``, no tabs or address bar) or a browser tab."""

from __future__ import annotations

import logging
import subprocess
import webbrowser

from backend.core.config import get_settings

log = logging.getLogger(__name__)


def open_ui(url: str) -> None:
    if get_settings().app_window:
        from backend.exports.pdf import find_browser

        browser = find_browser()
        if browser:
            try:
                subprocess.Popen([browser, f"--app={url}", "--window-size=1400,900"])
                return
            except OSError as e:
                log.warning("Could not open an app window (%s); using a browser tab", e)
    webbrowser.open(url)
