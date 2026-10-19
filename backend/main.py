"""Command-line entry point: ``uv run lecturelab [serve|seed-subjects]``."""

from __future__ import annotations

import argparse
import json
import logging
import sys
import threading
import time
import urllib.request
from logging.handlers import RotatingFileHandler
from pathlib import Path

import uvicorn

from backend.core.config import Settings, load_settings, set_settings
from backend.core.window import open_ui

log = logging.getLogger("lecturelab")


def setup_logging(settings: Settings) -> None:
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    file_handler = RotatingFileHandler(
        settings.logs_dir / "lecturelab.log", maxBytes=5_000_000, backupCount=3, encoding="utf-8"
    )
    file_handler.setFormatter(fmt)
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    root.handlers[:] = [file_handler, console]


def _already_running(settings: Settings) -> bool:
    try:
        url = f"http://{settings.host}:{settings.port}/api/health"
        with urllib.request.urlopen(url, timeout=1) as r:
            return json.load(r).get("app") == "lecturelab"
    except OSError:
        return False


def serve(settings: Settings) -> None:
    url = f"http://{settings.host}:{settings.port}"
    if _already_running(settings):
        print(f"LectureLab is already running at {url}; opening it.")
        open_ui(url)
        return

    from backend.app import create_app

    app = create_app(settings)
    config = uvicorn.Config(
        app,
        host=settings.host,
        port=settings.port,
        log_level="warning",
        access_log=False,
        # Open browser tabs keep a live-update stream open; don't let them block quitting.
        timeout_graceful_shutdown=3,
    )
    server = uvicorn.Server(config)

    def after_start() -> None:
        while not server.started:
            if server.should_exit:
                return
            time.sleep(0.1)
        log.info("LectureLab running at %s (data: %s)", url, settings.data_dir)
        if settings.open_browser:
            open_ui(url)

    threading.Thread(target=after_start, daemon=True).start()

    from backend.capture.recorder import get_recorder

    hotkeys = tray = None
    if settings.hotkeys.enabled:
        from backend.capture.hotkeys import Hotkeys

        hotkeys = Hotkeys(settings.hotkeys, get_recorder())
        hotkeys.start()
    if settings.tray:
        from backend.capture.tray import Tray

        def quit_app() -> None:
            server.should_exit = True

        tray = Tray(get_recorder(), url, quit_app)
        tray.start()
    try:
        server.run()
    finally:
        if hotkeys:
            hotkeys.stop()
        if tray:
            tray.stop()


def run_eval_cli(settings: Settings, action: str, n: int) -> None:
    """``lecturelab eval make|retrieval|grounding``: runs now and saves the run like the UI."""
    from backend.core.db import init_db, session_scope
    from backend.core.models import EvalRun
    from backend.evals import runner

    set_settings(settings)
    init_db(settings.db_path)
    with session_scope() as s:
        if action == "make":
            made = runner.make_candidates(s, n)
            print(f"{made} candidate questions made; approve them on the Evals page.")
            return
        try:
            if action == "retrieval":
                metrics, details = runner.run_retrieval(s)
                cost = 0.0
            else:
                metrics, details, cost = runner.run_grounding(s)
        except runner.EvalError as e:
            print(e)
            return
        s.add(
            EvalRun(
                kind=action,
                status="done",
                metrics=metrics,
                details=details,
                config=runner._config(),
                cost_usd=cost,
            )
        )
        s.commit()
    print(f"{action}: " + ", ".join(f"{k}={v}" for k, v in metrics.items()))


def make_shortcuts(remove: bool = False) -> list[str]:
    """Desktop + Start-menu shortcuts that start LectureLab without a terminal window."""
    import os
    import subprocess

    repo = Path(__file__).resolve().parents[1]
    env = {
        **os.environ,
        # wscript + a .vbs that runs the app hidden: uv's venv pythonw.exe is a console
        # trampoline and would show an empty terminal (closing it stopped the app).
        "LL_TARGET": str(
            Path(os.environ.get("WINDIR", r"C:\Windows")) / "System32" / "wscript.exe"
        ),
        "LL_ARGS": f'"{repo / "scripts" / "LectureLab.vbs"}"',
        "LL_DIR": str(repo),
        "LL_ICON": str(repo / "scripts" / "lecturelab.ico"),
        "LL_REMOVE": "1" if remove else "",
    }
    # Paths go in through the environment, so spaces and quotes in them need no escaping.
    script = r"""
$places = @([Environment]::GetFolderPath('Desktop'),
            [Environment]::GetFolderPath('Programs'))
$shell = New-Object -ComObject WScript.Shell
foreach ($dir in $places) {
  $path = Join-Path $dir 'LectureLab.lnk'
  if ($env:LL_REMOVE) { Remove-Item -LiteralPath $path -ErrorAction SilentlyContinue; continue }
  $lnk = $shell.CreateShortcut($path)
  $lnk.TargetPath = $env:LL_TARGET
  $lnk.Arguments = $env:LL_ARGS
  $lnk.WorkingDirectory = $env:LL_DIR
  $lnk.IconLocation = $env:LL_ICON
  $lnk.Description = 'LectureLab: record, transcribe and study your lectures'
  $lnk.Save()
  $path
}
"""
    out = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    return [line for line in out.stdout.splitlines() if line.strip()]


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="lecturelab")
    parser.add_argument("--data-dir", type=Path, help="override the data folder")
    sub = parser.add_subparsers(dest="cmd")
    p_serve = sub.add_parser("serve", help="run the app (default)")
    p_serve.add_argument("--no-browser", action="store_true")
    p_serve.add_argument("--no-tray", action="store_true")
    p_serve.add_argument("--no-hotkeys", action="store_true")
    p_serve.add_argument("--port", type=int)
    p_seed = sub.add_parser("seed-subjects", help="import subjects from a JSON file")
    p_seed.add_argument("file", type=Path)
    sub.add_parser("backup", help="back up now to the configured backup folder")
    p_restore = sub.add_parser("restore", help="rebuild a data folder from a backup folder")
    p_restore.add_argument("source", type=Path, help="the backup folder")
    p_restore.add_argument(
        "--to", type=Path, help="data folder to restore into (default: --data-dir)"
    )
    p_restore.add_argument("--snapshot", help="snapshot file name (default: the newest)")
    p_restore.add_argument("--force", action="store_true", help="overwrite an existing database")
    p_short = sub.add_parser(
        "shortcut", help="create Desktop + Start-menu shortcuts (no terminal window needed)"
    )
    p_short.add_argument("--remove", action="store_true", help="remove them again")
    p_eval = sub.add_parser("eval", help="evals: make search questions, run retrieval/grounding")
    p_eval.add_argument("action", choices=["make", "retrieval", "grounding"])
    p_eval.add_argument("-n", type=int, default=20, help="make: how many candidate questions")
    args = parser.parse_args(argv)

    overrides: dict[str, object] = {}
    if getattr(args, "no_browser", False):
        overrides["open_browser"] = False
    if getattr(args, "no_tray", False):
        overrides["tray"] = False
    if getattr(args, "no_hotkeys", False):
        overrides["hotkeys"] = {"enabled": False}
    if getattr(args, "port", None):
        overrides["port"] = args.port
    settings = load_settings(args.data_dir, **overrides)
    setup_logging(settings)

    if args.cmd == "seed-subjects":
        from backend.core.config import set_settings
        from backend.core.db import init_db
        from backend.seed import seed_subjects

        set_settings(settings)
        init_db(settings.db_path)
        created, skipped = seed_subjects(args.file)
        print(f"Subjects: {created} created, {skipped} already existed.")
        return

    if args.cmd == "backup":
        from backend.api.settings import apply_saved_backup
        from backend.core import backup
        from backend.core.config import set_settings

        set_settings(settings)
        apply_saved_backup(settings)
        result = backup.run_backup()
        copied = result["files_copied"]
        print(
            f"Backed up to {settings.backup.folder}: {result['snapshot']}, {copied} files copied."
        )
        return

    if args.cmd == "shortcut":
        made = make_shortcuts(args.remove)
        if args.remove:
            print("Removed the shortcuts.")
        else:
            print("Created:")
            for path in made:
                print(f"  {path}")
        return

    if args.cmd == "eval":
        run_eval_cli(settings, args.action, args.n)
        return

    if args.cmd == "restore":
        from backend.core import backup
        from backend.core.config import set_settings

        if _already_running(settings) and not args.to:
            print("Quit LectureLab first, or restore into another folder with --to.")
            return
        target = args.to or settings.data_dir
        set_settings(load_settings(target))
        used = backup.restore(args.source, target, args.snapshot, args.force)
        print(
            f'Restored {used} into {target}. Start it with: lecturelab --data-dir "{target}" serve'
        )
        return

    serve(settings)


if __name__ == "__main__":
    main()
