"""A small local speech-to-text service, so other apps on this PC share our Whisper instead of loading their own.

The Gothic voice chat (``gothic2_openDialog/voicechat``) asks ``http://127.0.0.1:39283/health`` and, when it answers
``ok``, sends its recordings here instead of loading a second Whisper (two copies would take ~5 GB of VRAM):

* ``GET /health`` -> ``200 {"ok": true, "model": "whisper-large-v3-turbo", "device": "cuda-fp16"}``; ``ok`` is
  true only once the model is loaded;
* ``POST /stt`` with a WAV body (PCM 16-bit, mono, 16 kHz, ``Content-Type: audio/wav``), already trimmed to speech
  and boosted by the caller (no silence threshold of ours is applied) -> ``200 {"text": "...", "ms": 123}``;
  errors -> 4xx/5xx ``{"error": "..."}``.  Optional ``?prompt=<URL-encoded text>``: names Whisper should spell
  right ("Xardas, Lester, Khorinis" - the people the player talks to), the most important last (see
  :meth:`wvt.stt.SpeechToText.transcribe`).

Only 127.0.0.1 is served (and only requests that name it as the host).  Recognitions run one at a time under the
same lock as push-to-talk (:class:`wvt.stt.SpeechToText`), so a call from another app holds the WoW keys for at
most one utterance (~0.2 s).  Nothing is logged about the audio or the text.
"""

from __future__ import annotations

import io
import json
import logging
import socket
import threading
import time
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, Optional
from urllib.parse import parse_qs, urlsplit

import numpy as np

log = logging.getLogger(__name__)

MAX_SECONDS = 30  # Whisper's window
MAX_BODY = 16000 * 2 * MAX_SECONDS + 4096
LOCAL_HOSTS = {"127.0.0.1", "localhost"}


def read_wav(data: bytes) -> np.ndarray:
    """PCM 16-bit mono 16 kHz WAV -> float32 samples in [-1, 1]; ValueError for anything else."""
    try:
        with wave.open(io.BytesIO(data), "rb") as w:
            if w.getnchannels() != 1 or w.getsampwidth() != 2 or w.getframerate() != 16000:
                raise ValueError(f"need PCM 16-bit mono 16 kHz, got {w.getnchannels()} ch, {8 * w.getsampwidth()} bit, "
                                 f"{w.getframerate()} Hz")
            frames = w.readframes(w.getnframes())
    except (wave.Error, EOFError) as exc:
        raise ValueError(f"not a WAV file: {exc}") from None
    return np.frombuffer(frames, dtype="<i2").astype(np.float32) / 32768.0


class _Handler(BaseHTTPRequestHandler):
    server_version = "WVT-STT/1"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args) -> None:  # no access log: privacy
        pass

    def _reply(self, code: int, obj: dict) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _local(self) -> bool:
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]").lower()
        return host in LOCAL_HOSTS  # a web page that re-binds its own name to 127.0.0.1 is turned away

    def do_GET(self) -> None:
        if not self._local():
            return self._reply(403, {"error": "local requests only"})
        if self.path.split("?")[0] != "/health":
            return self._reply(404, {"error": "not found"})
        stt = self.server.stt_getter()
        ready = stt is not None and stt.model is not None
        self._reply(200, {"ok": ready, "model": stt.model_label if stt is not None else "",
                          "device": stt.device if ready else "loading"})

    def do_POST(self) -> None:
        if not self._local():
            return self._reply(403, {"error": "local requests only"})
        if self.path.split("?")[0] != "/stt":
            return self._reply(404, {"error": "not found"})
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            n = -1
        if n <= 0:
            return self._reply(400, {"error": "a WAV body with Content-Length is needed"})
        if n > MAX_BODY:
            return self._reply(413, {"error": f"longer than {MAX_SECONDS} s"})
        data = self.rfile.read(n)
        stt = self.server.stt_getter()
        if stt is None or stt.model is None:
            return self._reply(503, {"error": "model is still loading"})
        try:
            audio = read_wav(data)
        except ValueError as exc:
            return self._reply(400, {"error": str(exc)})
        prompt = (parse_qs(urlsplit(self.path).query).get("prompt") or [""])[0][:2000]
        t0 = time.perf_counter()
        try:
            text = stt.transcribe(audio, prompt=prompt) if prompt else stt.transcribe(audio)
        except Exception as exc:  # the model failed: tell the caller, keep serving
            log.warning("STT service: recognition failed (%s)", type(exc).__name__)
            return self._reply(500, {"error": f"recognition failed: {type(exc).__name__}"})
        self.server.served += 1
        self._reply(200, {"text": text, "ms": round((time.perf_counter() - t0) * 1000)})


class _ExclusiveServer(ThreadingHTTPServer):
    """The port is ours alone: on Windows SO_REUSEADDR (Python's default for HTTP servers) lets a second program
    bind the same port, and requests would then reach either of them."""

    allow_reuse_address = False
    daemon_threads = True

    def server_bind(self) -> None:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


class SttServer:
    def __init__(self, stt_getter: Callable[[], object], port: int = 39283, host: str = "127.0.0.1"):
        self.stt_getter = stt_getter
        self.port = port
        self.host = host
        self.httpd: Optional[ThreadingHTTPServer] = None

    def start(self) -> bool:
        try:
            httpd = _ExclusiveServer((self.host, self.port), _Handler)
        except OSError as exc:
            log.warning("STT service: port %d is busy (%s) - other apps will load their own model", self.port, exc)
            return False
        httpd.stt_getter = self.stt_getter
        httpd.served = 0
        self.httpd = httpd
        self.port = httpd.server_address[1]
        threading.Thread(target=httpd.serve_forever, name="wvt-stt-http", daemon=True).start()
        log.info("STT service on http://%s:%d (health, stt)", self.host, self.port)
        return True

    @property
    def served(self) -> int:
        return getattr(self.httpd, "served", 0)

    def stop(self) -> None:
        if self.httpd is not None:
            self.httpd.shutdown()
            self.httpd.server_close()
            self.httpd = None
