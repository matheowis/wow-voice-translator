"""Microphone capture with pre-roll, silence detection and resampling.

Audio stays in memory only — nothing is ever written to disk.
"""

from __future__ import annotations

import collections
import logging
import math
import threading
import time
from dataclasses import dataclass
from typing import Optional

import numpy as np

log = logging.getLogger(__name__)

TARGET_SR = 16000


def resample(x: np.ndarray, sr_in: int, sr_out: int = TARGET_SR) -> np.ndarray:
    """Band-limited resampling (windowed-sinc low-pass + interpolation)."""
    if sr_in == sr_out or len(x) == 0:
        return x.astype(np.float32, copy=False)
    if sr_in > sr_out:
        cutoff = 0.45 * sr_out / sr_in  # normalised to sr_in
        taps = 64
        n = np.arange(-taps, taps + 1)
        h = 2 * cutoff * np.sinc(2 * cutoff * n) * np.hamming(len(n))
        h /= h.sum()
        x = np.convolve(x, h, mode="same")
    duration = len(x) / sr_in
    n_out = int(round(duration * sr_out))
    t_out = np.arange(n_out) / sr_out
    t_in = np.arange(len(x)) / sr_in
    return np.interp(t_out, t_in, x).astype(np.float32)


@dataclass
class SpeechStats:
    duration: float
    speech_seconds: float
    rms: float
    peak: float


def frame_rms(x: np.ndarray, sr: int = TARGET_SR, frame_ms: int = 30) -> np.ndarray:
    n = max(1, int(sr * frame_ms / 1000))
    usable = len(x) // n * n
    if usable == 0:
        return np.zeros(0, dtype=np.float32)
    frames = x[:usable].reshape(-1, n)
    return np.sqrt(np.mean(frames.astype(np.float64) ** 2, axis=1)).astype(np.float32)


def analyze(x: np.ndarray, threshold: float, sr: int = TARGET_SR) -> SpeechStats:
    rms = frame_rms(x, sr)
    speech = float(np.sum(rms > threshold) * 0.03)
    return SpeechStats(
        duration=len(x) / sr,
        speech_seconds=speech,
        rms=float(np.sqrt(np.mean(x.astype(np.float64) ** 2))) if len(x) else 0.0,
        peak=float(np.max(np.abs(x))) if len(x) else 0.0,
    )


def dbfs(v: float) -> float:
    return 20.0 * math.log10(max(float(v), 1e-9))


def soft_clip(y: np.ndarray, knee: float = 0.9) -> np.ndarray:
    """Leave samples below ``knee`` alone and squash louder ones smoothly into (-1, 1) - no hard clipping."""
    out = np.asarray(y, dtype=np.float32).copy()
    over = np.abs(out) > knee
    if over.any():
        s = np.sign(out[over])
        a = np.abs(out[over]) - knee
        out[over] = s * (knee + (1.0 - knee) * np.tanh(a / (1.0 - knee)))
    return out


def input_gain(x: np.ndarray, threshold: float, gain_db: float = 0.0, auto: bool = True, target: float = 0.05,
               max_auto_db: float = 30.0, sr: int = TARGET_SR) -> tuple[np.ndarray, float]:
    """Gain before speech detection: (amplified audio, applied gain in dB).

    ``gain_db`` is a fixed boost (``audio.input_gain_db``).  ``auto`` lifts a quiet recording so its loudest
    frames reach ``target`` RMS (at most ``max_auto_db``), but never lifts the noise floor above half the
    silence threshold - a recording without speech stays silence.
    """
    g = 10.0 ** (gain_db / 20.0)
    y = np.asarray(x, dtype=np.float32) * g
    if auto and len(y):
        r = frame_rms(y, sr)
        if len(r):
            loud, floor = float(np.percentile(r, 95)), float(np.percentile(r, 10))
            if loud > 0:
                a = min(10.0 ** (max_auto_db / 20.0), target / loud)
                if floor > 0:
                    a = min(a, 0.5 * threshold / floor)
                if a > 1.0:
                    y = y * a
                    g *= a
    if g > 1.0:
        y = soft_clip(y)
    return y, dbfs(g)


def trim_and_normalize(x: np.ndarray, threshold: float, sr: int = TARGET_SR, pad_ms: int = 250,
                       max_gain: float = 16.0) -> np.ndarray:
    """Cut leading/trailing silence and bring quiet speech to a sane level (speech RMS ~0.08)."""
    rms = frame_rms(x, sr)
    voiced = np.where(rms > threshold)[0]
    if len(voiced) == 0:
        return x
    frame = int(sr * 0.03)
    pad = int(sr * pad_ms / 1000)
    start = max(0, voiced[0] * frame - pad)
    end = min(len(x), (voiced[-1] + 1) * frame + pad)
    y = x[start:end].astype(np.float32)
    speech_rms = float(np.sqrt(np.mean(rms[voiced] ** 2)))
    if speech_rms > 0:
        gain = min(max_gain, 0.08 / speech_rms)
        if gain > 1.0:
            y = soft_clip(y * gain)
    return y


class AudioRecorder:
    """Keeps the input stream open (optional) so recording starts instantly."""

    def __init__(self, device=None, sample_rate: int = TARGET_SR, always_open: bool = True, pre_roll_ms: int = 300,
                 max_seconds: float = 30.0):
        self.device = device
        self.requested_sr = sample_rate
        self.always_open = always_open
        self.pre_roll_ms = pre_roll_ms
        self.max_seconds = max_seconds
        self._stream = None
        self._sr = sample_rate
        self._lock = threading.Lock()
        self._ring: collections.deque[np.ndarray] = collections.deque()
        self._ring_len = 0
        self._chunks: list[np.ndarray] = []
        self._recorded = 0
        self._recording = False
        self.level = 0.0  # last block RMS, for the UI meter
        self.last_error: Optional[str] = None

    # ------------------------------------------------------------------ devices
    @staticmethod
    def list_input_devices() -> list[tuple[int, str]]:
        import sounddevice as sd

        out = []
        default_api = sd.default.hostapi if hasattr(sd.default, "hostapi") else None
        for idx, dev in enumerate(sd.query_devices()):
            if dev["max_input_channels"] > 0:
                api = sd.query_hostapis(dev["hostapi"])["name"]
                if api.startswith("MME") or default_api is None:
                    out.append((idx, dev["name"]))
        return out

    @staticmethod
    def resolve_device(device):
        """Config value (None / index / name) -> PortAudio device index (MME preferred)."""
        import sounddevice as sd

        if device is None or isinstance(device, int):
            return device
        if isinstance(device, str) and device.isdigit():
            return int(device)
        name = str(device)
        matches = [i for i, d in enumerate(sd.query_devices()) if d["max_input_channels"] > 0 and d["name"] == name]
        if not matches:
            matches = [i for i, d in enumerate(sd.query_devices()) if d["max_input_channels"] > 0 and name.lower() in d["name"].lower()]
        if not matches:
            log.warning("microphone %r not found, using the default device", name)
            return None
        mme = [i for i in matches if sd.query_hostapis(sd.query_devices(i)["hostapi"])["name"].startswith("MME")]
        return (mme or matches)[0]

    # ------------------------------------------------------------------ stream
    def _callback(self, indata, frames, time_info, status):  # runs on PortAudio thread
        block = indata[:, 0].copy() if indata.ndim == 2 else indata.copy()
        self.level = float(np.sqrt(np.mean(block.astype(np.float64) ** 2))) if len(block) else 0.0
        with self._lock:
            if self._recording:
                if self._recorded < self.max_seconds * self._sr:
                    self._chunks.append(block)
                    self._recorded += len(block)
            else:
                self._ring.append(block)
                self._ring_len += len(block)
                limit = int(self._sr * self.pre_roll_ms / 1000)
                while self._ring and self._ring_len - len(self._ring[0]) >= limit:
                    self._ring_len -= len(self._ring.popleft())

    def open(self) -> None:
        import sounddevice as sd

        if self._stream is not None:
            return
        device = self.resolve_device(self.device)
        last_exc = None
        for sr in (self.requested_sr, None):
            try:
                if sr is None:
                    info = sd.query_devices(device, "input")
                    sr = int(info["default_samplerate"])
                stream = sd.InputStream(device=device, channels=1, samplerate=sr, dtype="float32",
                                        blocksize=int(sr * 0.02), callback=self._callback)
                stream.start()
                self._stream = stream
                self._sr = sr
                self.last_error = None
                log.info("Microphone opened: %s @ %d Hz", device if device is not None else "default", sr)
                return
            except Exception as exc:  # unsupported rate -> retry with device default
                last_exc = exc
        self.last_error = f"Nie można otworzyć mikrofonu: {last_exc}"
        raise RuntimeError(self.last_error)

    def close(self) -> None:
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            finally:
                self._stream = None

    # ------------------------------------------------------------------ recording
    def start(self) -> None:
        if self._stream is None:
            self.open()
        with self._lock:
            self._chunks = list(self._ring) if self.always_open else []
            self._recorded = sum(len(c) for c in self._chunks)
            self._ring.clear()
            self._ring_len = 0
            self._recording = True

    def stop(self) -> np.ndarray:
        with self._lock:
            self._recording = False
            chunks, self._chunks = self._chunks, []
            self._recorded = 0
        if not self.always_open:
            self.close()
        audio = np.concatenate(chunks) if chunks else np.zeros(0, dtype=np.float32)
        return resample(audio, self._sr, TARGET_SR)

    def cancel(self) -> None:
        self.stop()

    @property
    def is_recording(self) -> bool:
        return self._recording

    def record_seconds(self, seconds: float) -> np.ndarray:
        """Blocking helper used by the settings "test" button and the CLI."""
        self.start()
        time.sleep(seconds)
        return self.stop()
