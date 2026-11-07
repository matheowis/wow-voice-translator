"""How alike two voices sound: speaker embeddings from WeSpeaker ResNet34 (ONNX, Apache-2.0).

Used by the voice designer to keep the five voices of a pool clearly different: every
Voice Design preview is embedded and the one least similar to the voices already chosen
wins.  Audio is decoded with ffmpeg; features are Kaldi-style 80-bin log-mel fbank
(25 ms / 10 ms, povey window, pre-emphasis 0.97) with mean normalisation, the same input
the model was trained on.  Cosine similarity: ~0.7+ the same speaker, ~0.2 unrelated voices.
"""

from __future__ import annotations

import subprocess
from functools import lru_cache
from pathlib import Path
from typing import Optional

import numpy as np

from ..config import resolve_path

MODEL_FILE = "models/speaker/voxceleb_resnet34.onnx"
MODEL_REPO = "Wespeaker/wespeaker-voxceleb-resnet34"
SR = 16000
FRAME, SHIFT, NFFT, NMEL = 400, 160, 512, 80


def decode_audio(data: bytes) -> np.ndarray:
    """Any audio ffmpeg understands (mp3 previews, wav...) -> mono float32 at 16 kHz."""
    proc = subprocess.run(["ffmpeg", "-v", "error", "-i", "pipe:0", "-f", "s16le", "-ac", "1", "-ar", str(SR), "pipe:1"],
                          input=data, capture_output=True, check=True)
    return np.frombuffer(proc.stdout, dtype="<i2").astype(np.float32) / 32768.0


def _mel(f):
    return 1127.0 * np.log(1.0 + np.asarray(f) / 700.0)


@lru_cache(maxsize=1)
def _mel_banks() -> np.ndarray:
    """(NMEL, NFFT//2 + 1) triangular filters exactly as Kaldi/torchaudio build them."""
    lo, hi = _mel(20.0), _mel(SR / 2)
    delta = (hi - lo) / (NMEL + 1)
    bins = _mel(np.arange(NFFT // 2) * SR / NFFT)
    banks = np.zeros((NMEL, NFFT // 2 + 1), dtype=np.float32)
    for m in range(NMEL):
        left, center, right = lo + m * delta, lo + (m + 1) * delta, lo + (m + 2) * delta
        up = (bins - left) / (center - left)
        down = (right - bins) / (right - center)
        banks[m, : NFFT // 2] = np.maximum(0.0, np.minimum(up, down))
    return banks


def fbank(x: np.ndarray) -> np.ndarray:
    """Kaldi fbank (no dither) + cepstral-mean-style normalisation -> (frames, 80)."""
    x = x.astype(np.float64) * 32768.0
    if len(x) < FRAME:
        x = np.pad(x, (0, FRAME - len(x)))
    n = 1 + (len(x) - FRAME) // SHIFT
    idx = np.arange(FRAME)[None, :] + SHIFT * np.arange(n)[:, None]
    frames = x[idx]
    frames = frames - frames.mean(axis=1, keepdims=True)
    frames = np.concatenate([frames[:, :1] * (1 - 0.97), frames[:, 1:] - 0.97 * frames[:, :-1]], axis=1)
    window = (0.5 - 0.5 * np.cos(2 * np.pi * np.arange(FRAME) / (FRAME - 1))) ** 0.85
    spec = np.abs(np.fft.rfft(frames * window, n=NFFT)) ** 2
    feats = np.log(np.maximum(spec @ _mel_banks().T.astype(np.float64), np.finfo(np.float32).eps))
    return (feats - feats.mean(axis=0, keepdims=True)).astype(np.float32)


class SpeakerEmbedder:
    def __init__(self, model_path: Optional[Path] = None):
        import onnxruntime as ort

        path = Path(model_path or resolve_path(MODEL_FILE))
        if not path.exists():
            raise FileNotFoundError(f"brak modelu podobieństwa głosów: {path} (scripts/download.py --speaker-model)")
        self._session = ort.InferenceSession(str(path), providers=["CPUExecutionProvider"])

    def embed(self, audio16k: np.ndarray) -> np.ndarray:
        """Unit-length 256-d embedding of a mono 16 kHz signal (silence trimmed)."""
        x = _trim_silence(audio16k)
        out = self._session.run(["embs"], {"feats": fbank(x)[None]})[0][0]
        return out / (np.linalg.norm(out) + 1e-9)

    def embed_bytes(self, data: bytes) -> np.ndarray:
        return self.embed(decode_audio(data))


def _trim_silence(x: np.ndarray, threshold: float = 0.01) -> np.ndarray:
    loud = np.flatnonzero(np.abs(x) > threshold)
    return x[loud[0]: loud[-1] + 1] if len(loud) else x


def median_f0(x: np.ndarray, fmin: float = 55.0, fmax: float = 420.0, threshold: float = 0.12) -> Optional[float]:
    """Median pitch (Hz) of the voiced frames of a 16 kHz signal (YIN, 32 ms windows, 10 ms hop).

    YIN's cumulative-mean-normalised difference avoids the octave errors of plain
    autocorrelation, so a growling orc (~90 Hz) is told apart from a chipmunk gnome (~300 Hz)
    or a man who came out sounding like a woman.
    """
    win, hop = int(0.032 * SR), int(0.01 * SR)
    lag_min, lag_max = int(SR / fmax), int(SR / fmin)
    if len(x) < win + lag_max:
        return None
    energy_floor = 0.15 * np.sqrt(np.mean(x ** 2) + 1e-12)
    taus = np.arange(lag_max + 1)
    f0s = []
    for start in range(0, len(x) - win - lag_max, hop):
        seg = x[start:start + win + lag_max].astype(np.float64)
        frame = seg[:win]
        if np.sqrt(np.mean(frame ** 2)) < energy_floor:
            continue
        # difference function d(tau) = E(frame) + E(shifted frame) - 2 * crosscorr(tau)
        cs = np.concatenate([[0.0], np.cumsum(seg ** 2)])
        e_shift = cs[taus + win] - cs[taus]
        corr = np.array([np.dot(frame, seg[t:t + win]) for t in taus])
        d = e_shift[0] + e_shift - 2 * corr
        d[0] = 0.0
        cmnd = np.ones_like(d)
        running = np.cumsum(d[1:])
        cmnd[1:] = d[1:] * np.arange(1, len(d)) / np.maximum(running, 1e-12)
        below = np.flatnonzero(cmnd[lag_min:lag_max] < threshold)
        if not len(below):
            continue
        t = lag_min + below[0]
        while t + 1 < lag_max and cmnd[t + 1] < cmnd[t]:  # walk down to the local minimum
            t += 1
        f0s.append(SR / t)
    return float(np.median(f0s)) if len(f0s) >= 10 else None


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.dot(a, b) / ((np.linalg.norm(a) * np.linalg.norm(b)) + 1e-9))


def most_distinct(candidates: list[np.ndarray], chosen: list[np.ndarray],
                  others: Optional[list[np.ndarray]] = None, other_weight: float = 0.5) -> tuple[int, float]:
    """Index of the candidate least like the voices already chosen (and, weaker, like `others`)."""
    best, best_score = 0, float("inf")
    for i, c in enumerate(candidates):
        same = max((cosine(c, v) for v in chosen), default=0.0)
        other = max((cosine(c, v) for v in others or []), default=0.0)
        score = max(same, other_weight * other)
        if score < best_score:
            best, best_score = i, score
    return best, best_score
