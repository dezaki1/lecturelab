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
    # "apps": process loopback of every app except LectureLab (Windows 10 2004+), recorded
    # independent of the volume slider and mute; "device": loopback of the default output
    # (silent at volume 0 with many drivers). "apps" falls back to "device" when unavailable.
    system_capture: str = "apps"
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


class SlidesConfig(BaseModel):
    """Slide capture while recording (Zoom window, else a chosen monitor) and from videos."""

    enabled: bool = True  # default for new recordings; the recorder panel can toggle it
    interval_s: float = 2.0
    # The largest visible window matching a class name or title is captured.
    window_classes: list[str] = Field(
        default_factory=lambda: ["ConfMultiTabContentWndClass", "ZPContentViewWndClass"]
    )
    window_titles: list[str] = Field(default_factory=lambda: ["Zoom Meeting", "Zoom Webinar"])
    fallback_monitor: int | None = None  # 1-based; None = only capture the Zoom window
    # Fraction of the slide content (non-background, non-video cells) that must change.
    change_threshold: float = 0.15
    max_width: int = 1920
    jpeg_quality: int = 85
    from_video: bool = True  # find slides in imported videos
    ocr: bool = True


class TranscriptionConfig(BaseModel):
    # "faster-whisper" for real use; "fake" generates placeholder segments (tests, UI work).
    engine: str = "faster-whisper"
    model: str = "large-v3-turbo"
    device: str = "auto"  # auto | cuda | cpu
    cuda_compute_type: str = "int8_float16"  # fits the 4 GB RTX A1000 comfortably
    cpu_compute_type: str = "int8"
    cpu_model: str = "large-v3-turbo"  # fallback model; "small" is ~4x faster on CPU
    cpu_threads: int = 0  # 0 = ctranslate2 default
    beam_size: int = 5
    vad: bool = True
    # GPU: transcribe this many speech pieces at once (2-4x faster; ~3 GB VRAM with the default
    # model). Halves itself when the GPU runs out of memory; 0 or 1 = one at a time.
    batch_size: int = 8
    # Auto-start transcription when a recording/import has been encoded.
    auto: bool = True
    # Where Whisper models are stored; defaults to <data_dir>/models.
    models_dir: Path | None = None


class LLMConfig(BaseModel):
    # anthropic | ollama | openai (any OpenAI-compatible endpoint) | mock (tests, offline UI work)
    provider: str = "anthropic"
    # Two tiers (SPEC §13.1): "main" writes study material and chats, "cheap" verifies/cleans up.
    anthropic_main: str = "claude-sonnet-5-5"
    anthropic_cheap: str = "claude-haiku-5-5"
    main_effort: str = "medium"  # low | medium | high | xhigh | max
    cheap_effort: str = "low"
    chat_effort: str = "low"  # chat replies: short thinking keeps them fast
    # Server-side refusal fallback for the main model (Claude API only; Haiku has none).
    anthropic_fallbacks: bool = True
    # Background work (notes, cards, cleanup, overviews) through the Message Batches API: half
    # price, usually done within minutes. Settings can switch it (state.json "llm_batch").
    anthropic_batch: bool = True
    batch_timeout_s: float = 3 * 3600  # then cancel and send the call the normal way
    ollama_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "qwen3:8b"
    ollama_num_ctx: int = 32768
    ollama_think: bool = False  # qwen3 etc. can "think"; off = much faster on a 4 GB GPU
    openai_base_url: str = ""
    openai_model: str = ""
    # Optional per-task model override, e.g. {"questions": "claude-opus-5-5"}.
    task_models: dict[str, str] = Field(default_factory=dict)
    max_retries: int = 2  # extra attempts when the output fails validation
    timeout_s: float = 900.0


class GenerationConfig(BaseModel):
    # Generate study material automatically after transcription (if an LLM is configured).
    auto: bool = True
    verify: bool = True
    # Local models passed wrong answers in testing; "ok" from them is false confidence, so by
    # default items generated offline stay "unchecked".
    verify_local: bool = False
    # Output amounts scale with lecture length; the model picks within these bounds
    # depending on how dense the content is (SPEC §9: never fixed counts).
    cards_per_10min: tuple[float, float] = (2.0, 6.0)
    questions_per_10min: tuple[float, float] = (0.8, 2.5)
    exercises_per_10min: tuple[float, float] = (0.3, 1.2)
    min_items: int = 3
    # Transcript cleanup before generation: fix misheard terms using slide text and vocabulary
    # (cheap model). Off for local models by default: slow, and they rewrite too freely.
    cleanup: bool = True
    cleanup_local: bool = False
    cleanup_lines_per_call: int = 400
    # Course overview (subject hub) after each generated lecture. Off for local models by
    # default: it reads all lectures' notes, which takes a long time on a small GPU.
    subject_hub: bool = True
    subject_hub_local: bool = False


class SearchConfig(BaseModel):
    # ollama | mock (tests) | none (keyword search only)
    embed_provider: str = "ollama"
    embed_model: str = "bge-m3"
    embed_dim: int = 1024
    embed_keep_alive: str = "2m"  # how long Ollama keeps the model in (V)RAM after use
    chunk_min_s: float = 60.0
    chunk_max_s: float = 90.0
    chat_top_k: int = 12  # retrieved chunks per subject/all-scope chat turn


class StudyConfig(BaseModel):
    desired_retention: float = 0.9  # FSRS target recall probability
    new_cards_per_day: int = 30
    max_reviews_per_day: int = 400


class LiveConfig(BaseModel):
    enabled: bool = False  # live transcript for new recordings (also switchable mid-recording)
    window_s: float = 15.0  # audio per live transcription step (text lags about this much)
    ask_minutes: float = 10.0  # how much of the live transcript quick questions see


class VoiceConfig(BaseModel):
    voice: str = "en_US-lessac-medium"  # Piper voice for the examiner and audio recaps
    rate: float = 1.0  # speaking speed (1.0 = the voice's natural pace)
    tts_engine: str = "piper"  # piper | fake (tests)
    stt_idle_s: int = 300  # unload the voice-answer Whisper model after this much idle time


class RecapConfig(BaseModel):
    auto_lecture: bool = False  # make an audio recap for every newly generated lecture
    minutes: int = 7  # default length


class LanConfig(BaseModel):
    enabled: bool = False  # tablet/phone access on the home network (token-protected)
    host: str = "0.0.0.0"  # all interfaces; the main server stays on 127.0.0.1
    port: int = 8770


class PlannerConfig(BaseModel):
    minutes_per_day: int = 90  # study time per study day, reviews included
    weekdays: list[int] = Field(default_factory=lambda: [0, 1, 2, 3, 4, 5, 6])  # Monday = 0
    start_time: str = "18:00"  # when the study block sits in the exported calendar
    block_min: int = 30  # length of one topic block
    review_sec_per_card: int = 12  # estimate for the daily review block


class TimetableConfig(BaseModel):
    remind_min: int = 2  # reminder this many minutes before a lecture; 0 = off
    auto_record: bool = False  # start recording when Zoom is open during a slot
    early_min: int = 10  # a slot counts as "now" from this many minutes before its start
    sync_hours: float = 6.0  # how often subscribed calendar links are re-read


class BackupConfig(BaseModel):
    enabled: bool = False
    folder: Path | None = None  # e.g. ~/OneDrive/LectureLab backup
    interval_hours: float = 24.0
    keep_daily: int = 7
    keep_weekly: int = 4


class DownloadConfig(BaseModel):
    """Import from a link (yt-dlp)."""

    max_height: int = 1080  # video resolution cap; slides stay readable at 720-1080p
    concurrent_fragments: int = 4  # parallel HLS/DASH segment downloads
    probe_timeout_s: float = 60.0
    # "Just download" target; None = the Windows Downloads folder.
    save_dir: Path | None = None


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="LECTURELAB_", env_nested_delimiter="__")

    _toml_path: ClassVar[Path | None] = None

    data_dir: Path = Field(default_factory=default_data_dir)
    host: str = "127.0.0.1"
    port: int = 8765
    open_browser: bool = True
    app_window: bool = True  # open as an app window (Edge/Chrome --app) instead of a browser tab
    tray: bool = True
    ffmpeg: str = "ffmpeg"
    ffprobe: str = "ffprobe"
    hotkeys: HotkeyConfig = Field(default_factory=HotkeyConfig)
    recorder: RecorderConfig = Field(default_factory=RecorderConfig)
    slides: SlidesConfig = Field(default_factory=SlidesConfig)
    transcription: TranscriptionConfig = Field(default_factory=TranscriptionConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    generation: GenerationConfig = Field(default_factory=GenerationConfig)
    search: SearchConfig = Field(default_factory=SearchConfig)
    study: StudyConfig = Field(default_factory=StudyConfig)
    backup: BackupConfig = Field(default_factory=BackupConfig)
    timetable: TimetableConfig = Field(default_factory=TimetableConfig)
    planner: PlannerConfig = Field(default_factory=PlannerConfig)
    lan: LanConfig = Field(default_factory=LanConfig)
    voice: VoiceConfig = Field(default_factory=VoiceConfig)
    live: LiveConfig = Field(default_factory=LiveConfig)
    recap: RecapConfig = Field(default_factory=RecapConfig)
    downloads: DownloadConfig = Field(default_factory=DownloadConfig)

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
    def models_dir(self) -> Path:
        return self.transcription.models_dir or self.data_dir / "models"

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

[transcription]
# model = "large-v3-turbo"
# device = "auto"            # auto | cuda | cpu
# cuda_compute_type = "int8_float16"
# cpu_model = "large-v3-turbo"   # e.g. "small" for faster CPU fallback
# auto = true                # transcribe automatically after recording/import

[llm]
# provider = "anthropic"     # anthropic | ollama | openai | mock
# anthropic_main = "claude-sonnet-5-5"
# anthropic_cheap = "claude-haiku-5-5"
# main_effort = "medium"
# ollama_model = "qwen3:8b"
# The Anthropic API key is stored in Windows Credential Manager (Settings page in the app).

[generation]
# auto = true                # generate study material after transcription
# verify = true              # cheap second pass that checks cards/questions against the source
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
