"""Polite HTTP for building the knowledge base: one request at a time per host, a pause between
requests, retries with back-off (honours Retry-After) and a generic User-Agent (no e-mail or
other personal data)."""

from __future__ import annotations

import logging
import time
from urllib.parse import urlparse

import httpx

log = logging.getLogger(__name__)

USER_AGENT = ("WoWVoiceTranslator-KB/0.1 (personal non-commercial knowledge base; "
              "github.com/matheowis/wow-voice-translator)")


class PoliteClient:
    def __init__(self, min_interval: float = 1.0, timeout: float = 60.0, retries: int = 4):
        self.min_interval = min_interval
        self.retries = retries
        self.client = httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=timeout, follow_redirects=True)
        self._last: dict[str, float] = {}
        self.requests = 0

    def _wait(self, host: str) -> None:
        last = self._last.get(host)
        if last is not None:
            gap = self.min_interval - (time.monotonic() - last)
            if gap > 0:
                time.sleep(gap)

    def get(self, url: str, params: dict | None = None) -> httpx.Response:
        host = urlparse(url).netloc
        delay = 5.0
        for attempt in range(self.retries + 1):
            self._wait(host)
            try:
                r = self.client.get(url, params=params)
            except httpx.TransportError as e:
                self._last[host] = time.monotonic()
                if attempt == self.retries:
                    raise
                log.warning("%s: %s, ponawiam za %.0f s", host, e, delay)
                time.sleep(delay)
                delay *= 2
                continue
            self._last[host] = time.monotonic()
            self.requests += 1
            if r.status_code in (429, 500, 502, 503, 504) and attempt < self.retries:
                wait = delay
                ra = r.headers.get("Retry-After")
                if ra and ra.isdigit():
                    wait = max(wait, float(ra))
                log.warning("%s: HTTP %s, ponawiam za %.0f s", host, r.status_code, wait)
                time.sleep(wait)
                delay *= 2
                continue
            r.raise_for_status()
            return r
        raise RuntimeError("unreachable")

    def close(self) -> None:
        self.client.close()
