"""Text-to-speech with Piper (local, CPU): the examiner's voice, later the audio recaps.

Voices are downloaded on request (Settings → Voice) from the rhasspy/piper-voices repository
on Hugging Face into ``<models_dir>/piper/``. Synthesised clips are cached as WAV files under
``<data>/cache/tts/`` (keyed by voice, speed and text), so replaying a question costs nothing.
The ``fake`` engine (tests) writes a short tone without any model.
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import math
import os
import struct
import threading
import wave
from pathlib import Path
from typing import Any

import httpx

from backend.core.config import get_settings
from backend.core.events import bus

log = logging.getLogger(__name__)

HF = "https://huggingface.co/rhasspy/piper-voices/resolve/main"
# id -> (path in the repository, label, language, approx. MB)
VOICES: dict[str, tuple[str, str, str, int]] = {
    "en_US-lessac-medium": ("en/en_US/lessac/medium", "English (US), Lessac", "en", 63),
    "en_GB-alan-medium": ("en/en_GB/alan/medium", "English (UK), Alan", "en", 63),
    "de_DE-thorsten-medium": ("de/de_DE/thorsten/medium", "German, Thorsten", "de", 63),
}

_lock = threading.Lock()  # one synthesis at a time (ONNX session per voice)
_loaded: dict[str, Any] = {}
_downloads: dict[str, dict[str, Any]] = {}


class TtsError(Exception):
    pass


def voices_dir() -> Path:
    return get_settings().models_dir / "piper"


def voice_files(voice: str) -> tuple[Path, Path]:
    d = voices_dir()
    return d / f"{voice}.onnx", d / f"{voice}.onnx.json"


def installed(voice: str) -> bool:
    model, cfg = voice_files(voice)
    return model.is_file() and cfg.is_file()


def catalog() -> list[dict[str, Any]]:
    return [
        {
            "id": v,
            "label": label,
            "language": lang,
            "size_mb": mb,
            "installed": installed(v),
            "download": _downloads.get(v),
        }
        for v, (_, label, lang, mb) in VOICES.items()
    ]


def ready() -> bool:
    cfg = get_settings().voice
    return cfg.tts_engine == "fake" or installed(cfg.voice)


# Download -------------------------------------------------------------------------------------


def start_download(voice: str) -> None:
    if voice not in VOICES:
        raise TtsError(f"Unknown voice {voice!r}")
    if installed(voice) or (_downloads.get(voice) or {}).get("state") == "running":
        return
    _downloads[voice] = {"state": "running", "fraction": 0.0, "error": ""}
    threading.Thread(target=_download, args=(voice,), name=f"voice-dl-{voice}", daemon=True).start()


def _download(voice: str) -> None:
    path = VOICES[voice][0]
    model, cfg = voice_files(voice)
    model.parent.mkdir(parents=True, exist_ok=True)
    state = _downloads[voice]
    try:
        _fetch(f"{HF}/{path}/{voice}.onnx.json?download=true", cfg, None)
        json.loads(cfg.read_text(encoding="utf-8"))  # a broken config would fail at load time
        _fetch(f"{HF}/{path}/{voice}.onnx?download=true", model, state)
        state.update(state="done", fraction=1.0)
        log.info("Downloaded Piper voice %s", voice)
    except Exception as e:
        state.update(state="failed", error=f"{type(e).__name__}: {e}")
        log.warning("Voice download failed: %s", e)
        for f in (model, cfg):
            f.unlink(missing_ok=True)
    bus.publish("voice", {"voice": voice, **state})


def _fetch(url: str, dest: Path, state: dict[str, Any] | None) -> None:
    part = dest.with_suffix(dest.suffix + ".part")
    with httpx.stream("GET", url, follow_redirects=True, timeout=60) as r:
        r.raise_for_status()
        total = int(r.headers.get("content-length") or 0)
        done = 0
        last = 0.0
        with open(part, "wb") as f:
            for chunk in r.iter_bytes(1 << 16):
                f.write(chunk)
                done += len(chunk)
                if state is not None and total:
                    state["fraction"] = done / total
                    if state["fraction"] - last >= 0.02:
                        last = state["fraction"]
                        bus.publish("voice", {"voice": dest.stem.removesuffix(".onnx"), **state})
    os.replace(part, dest)


# Synthesis ------------------------------------------------------------------------------------


def _voice(name: str):  # type: ignore[no-untyped-def]
    if name not in _loaded:
        from piper import PiperVoice

        model, cfg = voice_files(name)
        if not model.is_file():
            raise TtsError("The voice isn't downloaded yet (Settings → Voice)")
        _loaded[name] = PiperVoice.load(model, config_path=cfg)
    return _loaded[name]


def _fake_wav(text: str) -> bytes:
    rate, secs = 16_000, min(3.0, 0.2 + len(text) / 200)
    frames = b"".join(
        struct.pack("<h", int(3000 * math.sin(2 * math.pi * 440 * i / rate)))
        for i in range(int(rate * secs))
    )
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(frames)
    return buf.getvalue()


def synthesize(text: str, voice: str | None = None) -> Path:
    """WAV file for ``text`` (cached)."""
    settings = get_settings()
    cfg = settings.voice
    voice = voice or cfg.voice
    text = " ".join(text.split())
    if not text:
        raise TtsError("Nothing to say")
    key = hashlib.sha1(f"{cfg.tts_engine}|{voice}|{cfg.rate}|{text}".encode()).hexdigest()
    out = settings.data_dir / "cache" / "tts" / f"{key}.wav"
    if out.is_file():
        return out
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp")
    if cfg.tts_engine == "fake":
        tmp.write_bytes(_fake_wav(text))
    else:
        from piper import SynthesisConfig

        with _lock:
            v = _voice(voice)
            with wave.open(str(tmp), "wb") as w:
                v.synthesize_wav(text, w, syn_config=SynthesisConfig(length_scale=1 / cfg.rate))
    os.replace(tmp, out)
    return out
