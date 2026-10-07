"""Application settings.

Precedence (highest first): explicit kwargs > environment (``LECTURELAB_*``, nested with ``__``)
> ``<data_dir>/config.toml`` > defaults. The data dir itself comes from ``LECTURELAB_DATA_DIR``
or defaults to ``%USERPROFILE%\\LectureLab``.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import ClassVar

from pydantic import BaseModel, Field
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    TomlConfigSettingsSource,
)


def default_data_dir() -> Path:
    env = os.environ.get("LECTURELAB_DATA_DIR")
    return Path(env).expanduser() if env else Path.home() / "LectureLab"


class HotkeyConfig(BaseModel):
    # pynput GlobalHotKeys syntax. Zoom's own shortcuts are mostly Alt+<letter>, so we avoid Alt.
    start_stop: str = "<ctrl>+<shift>+<f9>"
    important: str = "<ctrl>+<shift>+<f10>"
    confused: str = "<ctrl>+<shift>+<f11>"
    enabled: bool = True


class RecorderConfig(BaseModel):
    sample_rate: int = 48_000
    fsync_interval_s: float = 3.0
    device_poll_s: float = 2.0
    # Writer stays this far behind wall clock so normal capture jitter is absorbed, not padded.
    latency_margin_s: float = 0.25
    # A source buffering more than this is considered drifted and trimmed to the newest audio.
    max_buffer_s: float = 1.0
    min_free_gb: float = 2.0
    warn_free_gb: float = 5.0
    stop_free_gb: float = 1.0
    aac_bitrate: str = "64k"
    keep_pcm: bool = False
    mic_default: bool = False
    mic_gain: float = 1.0
    system_gain: float = 1.0


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="LECTURELAB_", env_nested_delimiter="__")

    _toml_path: ClassVar[Path | None] = None

    data_dir: Path = Field(default_factory=default_data_dir)
    host: str = "127.0.0.1"
    port: int = 8765
    open_browser: bool = True
    tray: bool = True
    ffmpeg: str = "ffmpeg"
    ffprobe: str = "ffprobe"
    hotkeys: HotkeyConfig = Field(default_factory=HotkeyConfig)
    recorder: RecorderConfig = Field(default_factory=RecorderConfig)

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        sources: list[PydanticBaseSettingsSource] = [init_settings, env_settings]
        if cls._toml_path is not None and cls._toml_path.exists():
            sources.append(TomlConfigSettingsSource(settings_cls, toml_file=cls._toml_path))
        return tuple(sources)

    # Derived paths -------------------------------------------------------------------------
    @property
    def db_path(self) -> Path:
        return self.data_dir / "lecturelab.db"

    @property
    def subjects_dir(self) -> Path:
        return self.data_dir / "subjects"

    @property
    def trash_dir(self) -> Path:
        return self.data_dir / "trash"

    @property
    def tmp_dir(self) -> Path:
        return self.data_dir / "tmp"

    @property
    def logs_dir(self) -> Path:
        return self.data_dir / "logs"

    def ensure_dirs(self) -> None:
        for d in (self.data_dir, self.subjects_dir, self.trash_dir, self.logs_dir, self.tmp_dir):
            d.mkdir(parents=True, exist_ok=True)


CONFIG_TEMPLATE = """\
# LectureLab configuration. Uncomment and edit; restart the app to apply.
# Environment variables override this file, e.g. LECTURELAB_PORT=9000.

# port = 8765
# open_browser = true
# tray = true
# ffmpeg = "ffmpeg"

[hotkeys]
# start_stop = "<ctrl>+<shift>+<f9>"
# important = "<ctrl>+<shift>+<f10>"
# confused = "<ctrl>+<shift>+<f11>"
# enabled = true

[recorder]
# mic_default = false
# aac_bitrate = "64k"
# keep_pcm = false
# min_free_gb = 2.0
"""


def load_settings(data_dir: Path | None = None, **overrides: object) -> Settings:
    data_dir = data_dir or default_data_dir()
    toml_path = data_dir / "config.toml"
    Settings._toml_path = toml_path
    try:
        settings = Settings(data_dir=data_dir, **overrides)  # type: ignore[arg-type]
    finally:
        Settings._toml_path = None
    settings.ensure_dirs()
    if not toml_path.exists():
        toml_path.write_text(CONFIG_TEMPLATE, encoding="utf-8")
    return settings


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = load_settings()
    return _settings


def set_settings(settings: Settings) -> None:
    global _settings
    _settings = settings
