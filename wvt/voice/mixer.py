"""Plays streamed utterances through one output stream, several at once, each with its own gain.

The stream callback mixes the active utterances, moves every gain smoothly towards its
target (distance changes never click) and soft-limits the sum.
"""

from __future__ import annotations

import logging
import threading
from collections import deque
from typing import Optional

import numpy as np

log = logging.getLogger(__name__)

GAIN_STEP_PER_S = 3.0  # how fast a gain may move (full scale in ~0.33 s)
LIMIT_KNEE = 0.8


class Utterance:
    """One streamed line: feed() PCM s16le bytes, finish() at the end; the mixer pulls samples."""

    def __init__(self, gain: float, sample_rate: int, prebuffer_s: float = 0.1):
        self.gain = float(gain)
        self.gain_target = float(gain)
        self._chunks: deque[np.ndarray] = deque()
        self._buffered = 0
        self._remainder = b""
        self._lock = threading.Lock()
        self._prebuffer = int(prebuffer_s * sample_rate)
        self._primed = False
        self.input_done = False
        self.cancelled = False
        self.done = threading.Event()
        self.played_samples = 0

    def feed(self, pcm: bytes) -> None:
        data = self._remainder + pcm
        cut = len(data) - (len(data) % 2)  # a chunk may end in the middle of a 16-bit sample
        self._remainder = data[cut:]
        if cut:
            samples = np.frombuffer(data[:cut], dtype="<i2").astype(np.float32) / 32768.0
            with self._lock:
                self._chunks.append(samples)
                self._buffered += len(samples)

    def finish(self) -> None:
        self.input_done = True

    def set_gain(self, gain: float) -> None:
        self.gain_target = max(0.0, float(gain))

    def cancel(self) -> None:
        self.cancelled = True
        self.done.set()

    def wait(self, timeout: Optional[float] = None) -> bool:
        return self.done.wait(timeout)

    def pull(self, n: int) -> Optional[np.ndarray]:
        """Up to n samples; zeros while waiting for data; None when finished."""
        with self._lock:
            if not self._primed:
                if self._buffered >= self._prebuffer or (self.input_done and self._buffered > 0):
                    self._primed = True
                elif self.input_done:
                    self.done.set()
                    return None
                else:
                    return np.zeros(n, dtype=np.float32)
            out = np.zeros(n, dtype=np.float32)
            filled = 0
            while filled < n and self._chunks:
                c = self._chunks[0]
                take = min(n - filled, len(c))
                out[filled:filled + take] = c[:take]
                filled += take
                if take == len(c):
                    self._chunks.popleft()
                else:
                    self._chunks[0] = c[take:]
            self._buffered -= filled
            self.played_samples += filled
            if filled == 0 and self.input_done:
                self.done.set()
                return None
            return out


class AudioMixer:
    def __init__(self, sample_rate: int = 24000, device=None, master: float = 1.0, blocksize: int = 480):
        self.sample_rate = sample_rate
        self.device = device
        self.master = master
        self.blocksize = blocksize
        self._voices: list[Utterance] = []
        self._lock = threading.Lock()
        self._stream = None

    def _ensure_stream(self) -> None:
        if self._stream is not None:
            return
        import sounddevice as sd

        self._stream = sd.OutputStream(samplerate=self.sample_rate, channels=1, dtype="float32",
                                       blocksize=self.blocksize, device=self.device, latency="low",
                                       callback=self._callback)
        self._stream.start()
        log.info("audio output opened: %s @ %d Hz", self.device if self.device is not None else "default",
                 self.sample_rate)

    def play(self, gain: float) -> Utterance:
        u = Utterance(gain, self.sample_rate)
        self._ensure_stream()
        with self._lock:
            self._voices.append(u)
        return u

    @property
    def active(self) -> int:
        with self._lock:
            return len(self._voices)

    def mix(self, frames: int) -> np.ndarray:
        """Next block of the mix (also used by the tests without an audio device)."""
        out = np.zeros(frames, dtype=np.float32)
        with self._lock:
            voices = list(self._voices)
        finished = []
        step = GAIN_STEP_PER_S * frames / self.sample_rate
        for v in voices:
            if v.cancelled:
                finished.append(v)
                continue
            samples = v.pull(frames)
            if samples is None:
                finished.append(v)
                continue
            g0 = v.gain
            g1 = g0 + max(-step, min(step, v.gain_target - g0))
            v.gain = g1
            out += samples * (np.linspace(g0, g1, frames, dtype=np.float32) if g0 != g1 else g0)
        if finished:
            with self._lock:
                self._voices = [v for v in self._voices if v not in finished]
            for v in finished:
                v.done.set()
        out *= self.master
        over = np.abs(out) > LIMIT_KNEE
        if np.any(over):
            head = 1.0 - LIMIT_KNEE
            out[over] = np.sign(out[over]) * (LIMIT_KNEE + head * np.tanh((np.abs(out[over]) - LIMIT_KNEE) / head))
        return out

    def _callback(self, outdata, frames, _time_info, status) -> None:  # PortAudio thread
        if status:
            log.debug("audio output status: %s", status)
        outdata[:, 0] = self.mix(frames)

    def stop_all(self) -> None:
        with self._lock:
            voices, self._voices = self._voices, []
        for v in voices:
            v.cancel()

    def close(self) -> None:
        self.stop_all()
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:  # noqa: BLE001 - shutting down anyway
                pass
            self._stream = None
