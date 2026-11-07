"""ElevenLabs HTTP client: streaming text-to-speech and Voice Design.

The API key is only ever placed in the ``xi-api-key`` header of this client; it is never
logged or put in error messages.  Streaming uses ``POST /v1/text-to-speech/{voice}/stream``
with raw PCM output, which `eleven_v4_turbo` accepts (measured: first audio ~290 ms).
"""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass
from typing import Callable, Iterator, Optional

import httpx

log = logging.getLogger(__name__)

BASE_URL = "https://api.elevenlabs.io"
RETRY_STATUS = {429, 500, 502, 503, 504}
BACKOFF_S = (0.5, 1.5, 3.0)


class ElevenLabsError(RuntimeError):
    def __init__(self, message: str, status: Optional[int] = None, retryable: bool = False):
        super().__init__(message)
        self.status = status
        self.retryable = retryable


@dataclass
class TTSStats:
    ttfa_ms: float
    total_ms: float
    chars: int
    cost: Optional[float]  # credits, from the "character-cost" response header
    request_id: str = ""


def _detail(raw: bytes) -> str:
    try:
        d = json.loads(raw.decode("utf-8", errors="replace")).get("detail")
        if isinstance(d, dict):
            return str(d.get("message") or d.get("status") or d)[:200]
        if isinstance(d, list) and d:
            return str(d[0].get("msg", d[0]))[:200]
    except (ValueError, AttributeError):
        pass
    return raw.decode("utf-8", errors="replace")[:200]


class ElevenLabsClient:
    def __init__(self, api_key: str, model_id: str = "eleven_v4_turbo", output_format: str = "pcm_24000",
                 timeout_s: float = 15.0, max_retries: int = 2, base_url: str = BASE_URL,
                 transport: Optional[httpx.BaseTransport] = None, sleep: Callable[[float], None] = time.sleep):
        if not api_key:
            raise ElevenLabsError("brak klucza ELEVENLABS_API_KEY w pliku .env")
        self.model_id = model_id
        self.output_format = output_format
        self.max_retries = max_retries
        self._sleep = sleep
        self._http = httpx.Client(base_url=base_url, headers={"xi-api-key": api_key},
                                  timeout=httpx.Timeout(timeout_s, connect=5.0), transport=transport)
        self.last_stats: Optional[TTSStats] = None

    def __repr__(self) -> str:  # never show the key
        return f"ElevenLabsClient(model={self.model_id!r}, format={self.output_format!r})"

    # ------------------------------------------------------------------ text to speech
    def stream_tts(self, voice_id: str, text: str, language_code: Optional[str] = None,
                   cancel: Optional[threading.Event] = None,
                   on_stats: Optional[Callable[[TTSStats], None]] = None) -> Iterator[bytes]:
        """Yield raw audio chunks. Retries 429/5xx/timeouts only before the first byte."""
        body: dict = {"text": text, "model_id": self.model_id}
        if language_code:
            body["language_code"] = language_code
        params = {"output_format": self.output_format}
        t0 = time.perf_counter()
        attempt = 0
        while True:
            yielded = False
            try:
                with self._http.stream("POST", f"/v1/text-to-speech/{voice_id}/stream", params=params, json=body) as r:
                    if r.status_code != 200:
                        raise ElevenLabsError(f"HTTP {r.status_code}: {_detail(r.read())}", r.status_code,
                                              r.status_code in RETRY_STATUS)
                    cost_hdr = r.headers.get("character-cost")
                    request_id = r.headers.get("request-id", "")
                    first: Optional[float] = None
                    for chunk in r.iter_bytes():
                        if cancel is not None and cancel.is_set():
                            return
                        if not chunk:
                            continue
                        if first is None:
                            first = time.perf_counter() - t0
                        yielded = True
                        yield chunk
                    stats = TTSStats(ttfa_ms=(first or 0) * 1000, total_ms=(time.perf_counter() - t0) * 1000,
                                     chars=len(text), cost=float(cost_hdr) if cost_hdr else None, request_id=request_id)
                    self.last_stats = stats
                    if on_stats:
                        on_stats(stats)
                    return
            except ElevenLabsError as exc:
                if not exc.retryable or attempt >= self.max_retries:
                    raise
                err: Exception = exc
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                if yielded:
                    raise ElevenLabsError(f"połączenie przerwane w trakcie ({type(exc).__name__})") from None
                if attempt >= self.max_retries:
                    raise ElevenLabsError(f"brak odpowiedzi ElevenLabs ({type(exc).__name__})", retryable=True) from None
                err = exc
            delay = BACKOFF_S[min(attempt, len(BACKOFF_S) - 1)]
            log.info("ElevenLabs retry %d after %s (%.1fs)", attempt + 1, type(err).__name__, delay)
            if cancel is not None and cancel.wait(delay):
                return
            if cancel is None:
                self._sleep(delay)
            attempt += 1

    # ------------------------------------------------------------------ voice design
    def design_voice(self, description: str, preview_text: str, model_id: str = "eleven_ttv_v3",
                     seed: Optional[int] = None, guidance_scale: Optional[float] = None,
                     loudness: Optional[float] = None) -> tuple[list[dict], Optional[float]]:
        body: dict = {"voice_description": description, "model_id": model_id, "text": preview_text}
        for k, v in (("seed", seed), ("guidance_scale", guidance_scale), ("loudness", loudness)):
            if v is not None:
                body[k] = v
        r = self._http.post("/v1/text-to-voice/design", json=body, timeout=180)
        if r.status_code != 200:
            raise ElevenLabsError(f"Voice Design HTTP {r.status_code}: {_detail(r.content)}", r.status_code,
                                  r.status_code in RETRY_STATUS)
        cost = r.headers.get("character-cost")
        return r.json().get("previews", []), (float(cost) if cost else None)

    def create_voice(self, name: str, description: str, generated_voice_id: str,
                     labels: Optional[dict] = None) -> str:
        body: dict = {"voice_name": name, "voice_description": description, "generated_voice_id": generated_voice_id}
        if labels:
            body["labels"] = labels
        r = self._http.post("/v1/text-to-voice", json=body, timeout=120)
        if r.status_code != 200:
            raise ElevenLabsError(f"Create voice HTTP {r.status_code}: {_detail(r.content)}", r.status_code,
                                  r.status_code in RETRY_STATUS)
        return r.json()["voice_id"]

    def close(self) -> None:
        self._http.close()
