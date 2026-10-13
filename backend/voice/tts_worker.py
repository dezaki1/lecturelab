"""Text-to-speech child process: ``python -m backend.voice.tts_worker`` (JSON args on stdin).

Renders a list of sections into ONE mono 16-bit WAV with a pause between sections and reports
where each section starts (for chapters). Runs as a child so minutes of CPU-heavy synthesis
never stall the API. Reports progress as JSON lines on stdout. Deliberately imports nothing
from the app. ``engine: "fake"`` writes short tones (tests).

Args: {engine, model, config, rate, out, pause_s, sections: [text, ...]}
"""

from __future__ import annotations

import json
import math
import os
import struct
import sys
import wave
from pathlib import Path
from typing import Any


def emit(**msg: Any) -> None:
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()


def fake_pcm(text: str, rate: int) -> bytes:
    secs = min(2.0, 0.3 + len(text) / 400)
    return b"".join(
        struct.pack("<h", int(2000 * math.sin(2 * math.pi * 330 * i / rate)))
        for i in range(int(rate * secs))
    )


def main() -> int:
    args = json.loads(sys.stdin.read())
    sections: list[str] = args["sections"]
    out = Path(args["out"])
    tmp = out.with_suffix(".wav.tmp")
    try:
        if args.get("engine") == "fake":
            rate = 16_000
            voice = None
        else:
            from piper import PiperVoice, SynthesisConfig

            voice = PiperVoice.load(args["model"], config_path=args["config"])
            rate = voice.config.sample_rate
            syn = SynthesisConfig(length_scale=1 / float(args.get("rate") or 1.0))
        pause = b"\x00\x00" * int(rate * float(args.get("pause_s", 0.9)))
        starts: list[float] = []
        frames = 0
        total_chars = sum(len(t) for t in sections) or 1
        done_chars = 0
        with wave.open(str(tmp), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(rate)
            for i, text in enumerate(sections):
                if i:
                    w.writeframes(pause)
                    frames += len(pause) // 2
                starts.append(frames / rate)
                if voice is None:
                    pcm = fake_pcm(text, rate)
                    w.writeframes(pcm)
                    frames += len(pcm) // 2
                else:
                    for chunk in voice.synthesize(text, syn_config=syn):
                        pcm = chunk.audio_int16_bytes
                        w.writeframes(pcm)
                        frames += len(pcm) // 2
                done_chars += len(text)
                emit(type="progress", fraction=done_chars / total_chars, section=i)
        os.replace(tmp, out)
    except Exception as e:
        tmp.unlink(missing_ok=True)
        emit(type="error", message=f"{type(e).__name__}: {e}")
        return 1
    emit(type="done", starts=starts, duration=frames / rate)
    return 0


if __name__ == "__main__":
    sys.exit(main())
