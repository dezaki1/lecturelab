"""Transcription child process: ``python -m backend.pipeline.whisper_worker`` (JSON args on stdin).

``--serve`` keeps one model loaded for short voice answers (oral exam, teach-back): the first
stdin line is the model config, then one JSON request per line (``audio``, ``language``,
``hotwords``), each answered with one ``result`` or ``error`` line.

Runs in its own process so (a) the API never stalls, (b) VRAM is guaranteed to be released when
it exits, and (c) a CUDA crash can't take the app down. Reports progress as JSON lines on
stdout and writes ``transcript.json`` atomically. Deliberately imports nothing from the app.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any


def emit(**msg: Any) -> None:
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()


def add_cuda_dll_dirs() -> None:
    """Make the pip-installed NVIDIA libraries (cuBLAS, cuDNN) loadable on Windows."""
    import site

    roots = [Path(p) / "nvidia" for p in site.getsitepackages()]
    dirs = [str(b) for r in roots if r.exists() for b in r.glob("*/bin") if b.is_dir()]
    if dirs:
        os.environ["PATH"] = os.pathsep.join([*dirs, os.environ.get("PATH", "")])
        for d in dirs:
            if hasattr(os, "add_dll_directory"):
                os.add_dll_directory(d)


def load_audio(path: str, ffmpeg: str = "ffmpeg", sample_rate: int = 16_000):  # type: ignore[no-untyped-def]
    """Decode any audio/video file to mono float32 at 16 kHz with ffmpeg.

    We decode ourselves instead of letting faster-whisper use PyAV: one decoder for the whole
    app, and immune to PyAV API changes (PyAV 19 broke faster-whisper 1.2.1's av.open call).
    """
    import subprocess

    import numpy as np

    cmd = [ffmpeg, "-nostdin", "-v", "error", "-i", path, "-ac", "1", "-ar", str(sample_rate),
           "-f", "f32le", "-"]  # fmt: skip
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    out = subprocess.run(cmd, capture_output=True, check=False, creationflags=flags)
    if out.returncode != 0:
        raise RuntimeError(f"ffmpeg could not decode the audio: {out.stderr.decode()[-300:]}")
    return np.frombuffer(out.stdout, dtype=np.float32)


def model_is_cached(model: str, models_dir: Path) -> bool:
    name = model if "/" in model else f"faster-whisper-{model}"
    return any(models_dir.glob(f"models--*{name.split('/')[-1]}*/snapshots/*/model.bin"))


def load_model(args: dict[str, Any]):  # type: ignore[no-untyped-def]
    if args["device"] == "cuda":
        add_cuda_dll_dirs()
    os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
    from faster_whisper import WhisperModel

    models_dir = Path(args["models_dir"])
    cached = model_is_cached(args["model"], models_dir)
    emit(type="status", message="Loading model" if cached else "Downloading model (first run)")
    return WhisperModel(
        args["model"],
        device=args["device"],
        compute_type=args["compute_type"],
        cpu_threads=args.get("cpu_threads", 0),
        download_root=str(models_dir),
    )


def run_faster_whisper(args: dict[str, Any]) -> dict[str, Any]:
    t_load = time.monotonic()
    model = load_model(args)
    load_s = time.monotonic() - t_load

    emit(type="status", message="Decoding audio")
    audio = load_audio(args["audio"], args.get("ffmpeg", "ffmpeg"))
    emit(
        type="status", message="Detecting language" if not args.get("language") else "Transcribing"
    )
    t0 = time.monotonic()
    segments_iter, info = model.transcribe(
        audio,
        language=args.get("language"),
        beam_size=args.get("beam_size", 5),
        vad_filter=args.get("vad", True),
        word_timestamps=True,
        # Long lectures: don't feed previous text back in (avoids repetition loops); the
        # subject vocabulary is passed as hotwords, which are prepended to every window.
        condition_on_previous_text=False,
        hotwords=args.get("hotwords") or None,
    )
    emit(type="status", message="Transcribing", language=info.language)
    segments = []
    last_emit = 0.0
    for seg in segments_iter:
        segments.append(
            {
                "start": round(seg.start, 3),
                "end": round(seg.end, 3),
                "text": seg.text.strip(),
                "avg_logprob": round(seg.avg_logprob, 4),
                "no_speech_prob": round(seg.no_speech_prob, 4),
                "words": [
                    {
                        "start": round(w.start, 3),
                        "end": round(w.end, 3),
                        "word": w.word,
                        "p": round(w.probability, 3),
                    }
                    for w in (seg.words or [])
                ],
            }
        )
        now = time.monotonic()
        if info.duration and now - last_emit > 1.0:
            last_emit = now
            emit(type="progress", fraction=min(seg.end / info.duration, 0.999))
    elapsed = time.monotonic() - t0
    return {
        "language": info.language,
        "language_probability": round(info.language_probability, 3),
        "duration": info.duration,
        "segments": segments,
        "elapsed_s": round(elapsed, 1),
        "load_s": round(load_s, 1),
    }


def run_fake(args: dict[str, Any]) -> dict[str, Any]:
    """Deterministic placeholder transcript: one 5-second segment at a time."""
    if os.environ.get("LECTURELAB_FAKE_FAIL_DEVICE") == args["device"]:
        raise RuntimeError(f"simulated {args['device']} failure")
    duration = float(args.get("duration") or 30.0)
    segments, t, i = [], 0.0, 0
    while t < duration:
        end = min(t + 5.0, duration)
        words = f"This is placeholder segment {i + 1}.".split()
        step = (end - t) / len(words)
        segments.append(
            {
                "start": round(t, 3),
                "end": round(end, 3),
                "text": " ".join(words),
                "avg_logprob": -0.1,
                "no_speech_prob": 0.01,
                "words": [
                    {
                        "start": round(t + k * step, 3),
                        "end": round(t + (k + 1) * step, 3),
                        "word": " " + w,
                        "p": 0.99,
                    }
                    for k, w in enumerate(words)
                ],
            }
        )
        emit(type="progress", fraction=end / duration)
        t, i = end, i + 1
    return {
        "language": args.get("language") or "en",
        "language_probability": 1.0,
        "duration": duration,
        "segments": segments,
        "elapsed_s": 0.0,
        "load_s": 0.0,
    }


FAKE_CLIP_TEXT = "This is a placeholder spoken answer."


def serve() -> int:
    """Short clips, one model load: config line first, then one request per line."""
    cfg = json.loads(sys.stdin.readline())
    fake = cfg.get("engine") == "fake"
    try:
        model = None if fake else load_model(cfg)
    except Exception as e:
        emit(type="error", message=f"{type(e).__name__}: {e}")
        return 1
    emit(type="ready", device=cfg["device"])
    for line in sys.stdin:
        if not line.strip():
            continue
        req = json.loads(line)
        try:
            if model is None:
                emit(type="result", text=FAKE_CLIP_TEXT, language=req.get("language") or "en")
                continue
            audio = load_audio(req["audio"], cfg.get("ffmpeg", "ffmpeg"))
            segments, info = model.transcribe(
                audio,
                language=req.get("language") or None,
                beam_size=cfg.get("beam_size", 5),
                vad_filter=True,
                condition_on_previous_text=False,
                hotwords=req.get("hotwords") or None,
            )
            text = " ".join(seg.text.strip() for seg in segments).strip()
            emit(type="result", text=text, language=info.language, duration=info.duration)
        except Exception as e:  # one bad clip doesn't end the server
            emit(type="error", message=f"{type(e).__name__}: {e}")
    return 0


def main() -> int:
    if "--serve" in sys.argv:
        return serve()
    args = json.loads(sys.stdin.read())
    try:
        result = run_fake(args) if args.get("engine") == "fake" else run_faster_whisper(args)
    except Exception as e:  # reported to the parent, which may retry on CPU
        emit(type="error", message=f"{type(e).__name__}: {e}")
        return 1
    result.update(
        {
            "version": 1,
            "model": args["model"],
            "device": args["device"],
            "compute_type": args["compute_type"],
            "hotwords": args.get("hotwords") or "",
        }
    )
    out = Path(args["out"])
    tmp = out.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, out)
    emit(type="done", elapsed_s=result["elapsed_s"], language=result["language"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
