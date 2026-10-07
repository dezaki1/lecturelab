from __future__ import annotations

import numpy as np

from backend.capture.mixer import Mixer, SourceBuffer

SR = 1000


def test_pads_silence_to_match_wall_clock() -> None:
    buf = SourceBuffer()
    buf.push(np.full(500, 0.5, dtype=np.float32))  # only 0.5 s delivered
    mixer = Mixer({"system": buf}, SR, latency_margin_s=0.25)
    step = mixer.step(1.25)  # 1.25 s elapsed - 0.25 s margin = 1.0 s due
    assert step is not None
    assert step.pcm.size == 1000
    assert step.padded["system"] == 500
    assert np.all(step.pcm[:500] > 0) and np.all(step.pcm[500:] == 0)
    assert mixer.step(1.25) is None  # nothing new due
    final = mixer.step(1.25, final=True)  # on stop the margin is flushed too
    assert final is not None and mixer.written == 1250


def test_trims_source_that_runs_ahead() -> None:
    buf = SourceBuffer()
    buf.push(np.arange(3000, dtype=np.float32) / 1e4)  # 3 s queued, only 1 s due
    mixer = Mixer({"system": buf}, SR, latency_margin_s=0.0, max_buffer_s=1.0)
    step = mixer.step(1.0)
    assert step is not None and step.trimmed["system"] == 1000
    assert buf.available() == 1000  # newest audio kept for the next step
    assert step.pcm[0] == int(1000 / 1e4 * 32767)  # oldest second was dropped


def test_mixes_sources_with_gain_and_clips() -> None:
    a, b = SourceBuffer(), SourceBuffer()
    a.push(np.full(100, 0.8, dtype=np.float32))
    b.push(np.full(100, 0.8, dtype=np.float32))
    mixer = Mixer({"system": a, "mic": b}, SR, gains={"mic": 0.5}, latency_margin_s=0)
    step = mixer.step(0.1)
    assert step is not None
    assert step.pcm.max() == 32767  # 0.8 + 0.4 clipped to 1.0
    assert step.levels["system"].active and step.levels["mic"].rms_db > -3


def test_source_buffer_partial_take() -> None:
    buf = SourceBuffer()
    buf.push(np.arange(5, dtype=np.float32))
    buf.push(np.arange(5, 10, dtype=np.float32))
    assert buf.take(7).tolist() == list(range(7))
    assert buf.available() == 3
    assert buf.take(10).tolist() == [7, 8, 9]
