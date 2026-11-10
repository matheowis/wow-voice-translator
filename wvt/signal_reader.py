"""Reads the addon's pixel signal from the top-left corner of the WoW client area.

Plain GDI screen capture of a ~130x85 px region (~270x85 px when voices are on: the
voice grid sits right of the hover grid), 25 times per second, only while WoW is the
foreground window. No process memory is touched.
"""

from __future__ import annotations

import collections
import ctypes
import logging
import threading
import time
from typing import Callable, Optional

import numpy as np

from . import protocol, winapi
from .wow_window import WowWindow, WowWindowTracker

log = logging.getLogger(__name__)

SEARCH = 3  # pixels of tolerance for the grid origin
TYPE_NAMES = {protocol.TYPE_VOICE: "voice", protocol.TYPE_STATE: "state", protocol.TYPE_CHAT: "chat",
              protocol.TYPE_QUEST: "quest", protocol.TYPE_POS: "pos", protocol.TYPE_PLAYER: "player",
              protocol.TYPE_DONE: "done", protocol.TYPE_LOOT: "loot"}


# everything the second (voice) grid carries
ASSISTANT_GRID_TYPES = (protocol.TYPE_VOICE, protocol.TYPE_STATE, protocol.TYPE_CHAT, protocol.TYPE_QUEST,
                        protocol.TYPE_POS, protocol.TYPE_PLAYER, protocol.TYPE_DONE, protocol.TYPE_LOOT)

class ScreenCapture:
    """Reusable GDI capture of a small screen region -> RGB numpy array."""

    def __init__(self) -> None:
        self._w = self._h = 0
        self._mem_dc = None
        self._bmp = None
        self._screen_dc = None

    def _ensure(self, w: int, h: int) -> None:
        if self._mem_dc and w == self._w and h == self._h:
            return
        self.close()
        self._screen_dc = winapi.user32.GetDC(None)
        self._mem_dc = winapi.gdi32.CreateCompatibleDC(self._screen_dc)
        self._bmp = winapi.gdi32.CreateCompatibleBitmap(self._screen_dc, w, h)
        winapi.gdi32.SelectObject(self._mem_dc, self._bmp)
        self._w, self._h = w, h

    def grab(self, x: int, y: int, w: int, h: int) -> np.ndarray:
        self._ensure(w, h)
        if not winapi.gdi32.BitBlt(self._mem_dc, 0, 0, w, h, self._screen_dc, x, y, winapi.SRCCOPY):
            raise OSError("BitBlt failed")
        bmi = winapi.BITMAPINFO()
        bmi.bmiHeader.biSize = ctypes.sizeof(winapi.BITMAPINFOHEADER)
        bmi.bmiHeader.biWidth = w
        bmi.bmiHeader.biHeight = -h  # top-down
        bmi.bmiHeader.biPlanes = 1
        bmi.bmiHeader.biBitCount = 32
        bmi.bmiHeader.biCompression = winapi.BI_RGB
        buf = (ctypes.c_ubyte * (w * h * 4))()
        if not winapi.gdi32.GetDIBits(self._mem_dc, self._bmp, 0, h, buf, ctypes.byref(bmi), winapi.DIB_RGB_COLORS):
            raise OSError("GetDIBits failed")
        arr = np.frombuffer(buf, dtype=np.uint8).reshape(h, w, 4)
        return arr[:, :, [2, 1, 0]].copy()  # BGRA -> RGB

    def close(self) -> None:
        if self._bmp:
            winapi.gdi32.DeleteObject(self._bmp)
        if self._mem_dc:
            winapi.gdi32.DeleteDC(self._mem_dc)
        if self._screen_dc:
            winapi.user32.ReleaseDC(None, self._screen_dc)
        self._bmp = self._mem_dc = self._screen_dc = None


class HoverStateMachine:
    """Turns a stream of decoded packets (or None) into start/stop events."""

    def __init__(self, on_start: Callable[[protocol.Packet], None], on_stop: Callable[[], None], misses_to_stop: int = 3):
        self.on_start = on_start
        self.on_stop = on_stop
        self.misses_to_stop = misses_to_stop
        self.current: Optional[protocol.Packet] = None
        self._misses = 0

    def feed(self, pkt: Optional[protocol.Packet]) -> None:
        if pkt is None or (pkt.type == protocol.TYPE_KEEP and (self.current is None or pkt.seq != self.current.seq)):
            if self.current is not None:
                self._misses += 1
                if self._misses >= self.misses_to_stop:
                    self.current = None
                    self._misses = 0
                    self.on_stop()
            return
        self._misses = 0
        if pkt.type == protocol.TYPE_KEEP:
            return  # still hovering the same line
        if self.current is None or pkt.identity() != self.current.identity():
            self.current = pkt
            self.on_start(pkt)


class VoiceDeduper:
    """A voice packet stays on screen for several captures; pass each one on once."""

    def __init__(self, memory: int = 64):
        self._seen: list[tuple] = []
        self._memory = memory

    def is_new(self, pkt: protocol.Packet) -> bool:
        # seq restarts after /reload, so not alone; state packets have their own counter
        ident = (pkt.type, pkt.seq, pkt.sender, pkt.text, repr(pkt.state), repr(pkt.quest), repr(pkt.pos),
                 repr(pkt.player), repr(pkt.done), repr(pkt.loot))
        if ident in self._seen:
            return False
        self._seen.append(ident)
        del self._seen[:-self._memory]
        return True


class SignalReader:
    def __init__(self, tracker: WowWindowTracker, poll_hz: float,
                 on_start: Callable[[protocol.Packet, WowWindow], None], on_stop: Callable[[], None],
                 on_voice: Optional[Callable[[protocol.Packet], None]] = None):
        self.tracker = tracker
        self.interval = 1.0 / max(1.0, poll_hz)
        self._on_start = on_start
        self._window: Optional[WowWindow] = None
        self.machine = HoverStateMachine(lambda p: self._on_start(p, self._window), on_stop)
        self.on_voice = on_voice
        self.voice_dedup = VoiceDeduper()
        self.enabled = True
        self.require_foreground = True  # False only for the dev simulator
        self.last_packet_at = 0.0
        self.last_packet: Optional[protocol.Packet] = None
        self.last_voice_at = 0.0
        self.voice_packets = 0
        self.capture_errors = 0
        # once a minute in the log: what was read, how long WoW was in front (no texts - privacy)
        self.counts: collections.Counter = collections.Counter()
        self._beat_at = time.monotonic()
        self._front_s = 0.0
        self._frames = 0
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if not winapi.IS_WINDOWS:
            return
        self._thread = threading.Thread(target=self._run, name="wvt-signal", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(2)

    def _run(self) -> None:
        cap = ScreenCapture()
        try:
            while not self._stop.is_set():
                t0 = time.perf_counter()
                try:  # nothing may end this thread: hover, voices and Iskra's game data all come through it
                    self._step(cap)
                except Exception:
                    self.counts["errors"] += 1
                    log.exception("signal reader step failed")
                self._heartbeat()
                dt = time.perf_counter() - t0
                self._stop.wait(max(0.0, self.interval - dt))
        finally:
            cap.close()

    def _step(self, cap: "ScreenCapture") -> None:
        pkt = None
        voice = None
        if self.enabled:
            win = self.tracker.find(max_age=2.0)
            if win is not None and (not self.require_foreground or self.tracker.foreground_is_wow()):
                self._window = win
                self._frames += 1
                self._front_s += self.interval
                x, y, w, h = win.client
                width = (protocol.CAPTURE_W if self.on_voice else protocol.REGION_W) + SEARCH + 1
                try:
                    img = cap.grab(x, y, min(w, width), min(h, protocol.REGION_H + SEARCH + 1))
                    pkt = protocol.try_decode(img, search=SEARCH)
                    if self.on_voice and img.shape[1] >= protocol.CAPTURE_W:
                        voice = protocol.try_decode_at(img, protocol.VOICE_ORIGIN_X, search=SEARCH)
                except (OSError, protocol.DecodeError) as exc:
                    self.capture_errors += 1
                    log.debug("capture/decode failed: %s", exc)
        if (voice is not None and voice.type in ASSISTANT_GRID_TYPES
                and self.voice_dedup.is_new(voice)):
            self.last_voice_at = time.time()
            self.voice_packets += 1
            kind = voice.chat_kind_name if voice.type in (protocol.TYPE_CHAT, protocol.TYPE_VOICE) else ""
            self.counts[TYPE_NAMES.get(voice.type, str(voice.type)) + (f":{kind}" if kind else "")] += 1
            try:
                self.on_voice(voice)
            except Exception:
                log.exception("voice handler failed")
        if pkt is not None and pkt.type == protocol.TYPE_VOICE:
            pkt = None  # never treat a voice packet as a hover
        if pkt is not None:
            if self.last_packet is None or pkt.seq != self.last_packet.seq:
                self.counts["hover"] += 1
            self.last_packet_at = time.time()
            self.last_packet = pkt
        try:
            self.machine.feed(pkt)
        except Exception:
            log.exception("hover handler failed")

    def _heartbeat(self, every: float = 60.0) -> None:
        now = time.monotonic()
        if now - self._beat_at < every:
            return
        span = now - self._beat_at
        if self._frames or self.counts:
            parts = ", ".join(f"{k} {n}" for k, n in sorted(self.counts.items())) or "nothing"
            log.info("signal: WoW in front %.0f%% of %.0f s, %d frames, read: %s, capture errors %d",
                     min(100.0, 100.0 * self._front_s / span), span, self._frames, parts, self.capture_errors)
        self.counts.clear()
        self._beat_at, self._front_s, self._frames = now, 0.0, 0
