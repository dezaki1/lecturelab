"""Run as a subprocess: start a recording with fake sources, then wait to be killed."""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.app import create_app
from backend.capture.recorder import get_recorder
from backend.core.config import load_settings
from tests.fakes import fake_factory

settings = load_settings(Path(sys.argv[1]), open_browser=False, tray=False)
create_app(settings, start_worker=False, source_factory=fake_factory())
get_recorder().start(int(sys.argv[2]))
get_recorder().bookmark("important")
print("RECORDING", flush=True)
while True:
    time.sleep(1)
