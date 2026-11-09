"""Microphone gain before speech recognition: a quiet microphone is lifted, silence stays silence."""

import numpy as np

from wvt.audio import analyze, dbfs, input_gain, soft_clip, trim_and_normalize

SR = 16000


def tone(level, seconds=2.0, lead=0.5, total=3.0, noise=1e-5):
    t = np.arange(int(SR * seconds)) / SR
    x = np.zeros(int(SR * total), dtype=np.float32)
    start = int(SR * lead)
    x[start:start + len(t)] = level * np.sin(2 * np.pi * 220 * t) * (1 + 0.5 * np.sin(2 * np.pi * 3 * t))
    return x + np.random.default_rng(0).normal(0, noise, len(x)).astype(np.float32)


def test_quiet_speech_is_lifted_until_it_counts_as_speech():
    quiet = tone(0.004)  # about -52 dBFS: below the 0.006 silence threshold
    assert analyze(quiet, 0.006).speech_seconds == 0.0
    y, gain = input_gain(quiet, 0.006)
    assert 15 < gain <= 30 and analyze(y, 0.006).speech_seconds > 1.9
    clip = trim_and_normalize(y, 0.006)
    assert 1.9 < len(clip) / SR < 3.0 and np.abs(clip).max() <= 1.0


def test_noise_alone_is_never_lifted_over_the_threshold():
    noise = np.random.default_rng(1).normal(0, 0.001, SR * 2).astype(np.float32)
    y, _ = input_gain(noise, 0.006)
    assert analyze(y, 0.006).speech_seconds == 0.0


def test_normal_speech_is_left_alone_and_manual_gain_never_clips_hard():
    normal = tone(0.2)
    y, gain = input_gain(normal, 0.006)
    assert gain == 0.0 and np.allclose(y, normal)
    y, gain = input_gain(normal, 0.006, gain_db=12, auto=False)
    assert round(gain) == 12 and np.abs(y).max() < 1.0
    assert soft_clip(np.array([0.5, 0.95, 3.0, -3.0])).tolist()[0] == 0.5
    assert dbfs(0.1) == -20.0
