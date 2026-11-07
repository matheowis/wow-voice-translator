"""Turns voice packets from the addon into speech.

packet -> filters (channel, own line, block list, duplicates, distance, daily budget)
       -> bounded queue (oldest dropped, stale lines skipped)
       -> worker: voice of the character -> text (EN: normalised for speech, PL: translated
          locally) -> ElevenLabs stream -> mixer with the channel/distance gain

Nothing here blocks the game or the hover translation: the addon only shows pixels, the
reader thread only enqueues, and every network/model step has a timeout and a fallback.
Chat text is not logged unless ``privacy.log_texts`` is on.
"""

from __future__ import annotations

import logging
import re
import threading
import time
import unicodedata
from collections import Counter, deque
from dataclasses import dataclass, field
from typing import Callable, Optional

from .. import protocol
from .distance import say_gain
from .elevenlabs import ElevenLabsError, TTSStats
from .resolver import split_sender

log = logging.getLogger(__name__)

SAY_KINDS = {"SAY"}
PARTY_KINDS = {"PARTY", "PARTY_LEADER"}
_NOISE_RE = re.compile(r"^[\W_]+$")


@dataclass
class VoiceJob:
    pkt: protocol.Packet
    channel: str  # say | party
    gain: float
    lang: str  # en | pl
    received: float = field(default_factory=time.monotonic)

    @property
    def speaker(self) -> str:
        return split_sender(self.pkt.sender, "")[2]


@dataclass
class VoiceEvent:
    """What the UI shows about one line (chat text only when privacy.log_texts allows it)."""

    when: float
    channel: str
    sender: str
    race: str
    sex: int
    distance: Optional[float]
    dist_method: int
    status: str  # spoken | skipped:<reason> | error
    gain: float = 0.0
    lang: str = "en"
    voice_id: str = ""
    ttfa_ms: float = 0.0
    chars: int = 0
    cost: Optional[float] = None
    note: str = ""
    text: str = ""


class VoiceManager:
    def __init__(self, cfg: dict, tts, resolver, mixer, normalizer, budget, translator=None,
                 on_event: Optional[Callable[[VoiceEvent], None]] = None, log_texts: bool = False):
        self.cfg = cfg
        self.tts = tts
        self.resolver = resolver
        self.mixer = mixer
        self.normalizer = normalizer
        self.budget = budget
        self.translator = translator
        self.on_event = on_event
        self.log_texts = log_texts
        self.enabled = bool(cfg.get("enabled", True))
        self.muted = False
        self._queue: deque[VoiceJob] = deque()
        self._cv = threading.Condition()
        self._speaking: set[str] = set()
        self._recent: dict[tuple[str, str], float] = {}
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self.stats: Counter = Counter()
        self.ttfa_ms: deque[float] = deque(maxlen=200)
        self.events: deque[VoiceEvent] = deque(maxlen=30)
        self._missing_voice_warned: set[str] = set()

    # ------------------------------------------------------------------ lifecycle
    def start(self) -> None:
        for i in range(max(1, int(self.cfg.get("max_concurrent", 2)))):
            t = threading.Thread(target=self._work, name=f"wvt-voice-{i}", daemon=True)
            t.start()
            self._threads.append(t)

    def stop(self) -> None:
        self._stop.set()
        with self._cv:
            self._queue.clear()
            self._cv.notify_all()
        self.mixer.stop_all()
        for t in self._threads:
            t.join(2)

    def silence(self) -> None:
        """Stop what is playing and forget what is queued (tray menu / mute)."""
        with self._cv:
            dropped = len(self._queue)
            self._queue.clear()
        self.stats["skipped:silenced"] += dropped
        self.mixer.stop_all()

    # ------------------------------------------------------------------ intake (reader thread)
    def on_packet(self, pkt: protocol.Packet) -> Optional[str]:
        """Queue a line for speech; returns the skip reason or None."""
        self.stats["received"] += 1
        reason, job = self._filter(pkt)
        if reason:
            self._skip(pkt, reason)
            return reason
        with self._cv:
            if len(self._queue) >= int(self.cfg.get("max_queue", 6)):
                old = self._queue.popleft()
                self._skip(old.pkt, "queue-full")
            self._queue.append(job)
            self._cv.notify()
        return None

    def enqueue_direct(self, pkt: protocol.Packet, gain: float = 1.0) -> None:
        """Test playback from the app window: no channel/duplicate/distance filters."""
        job = VoiceJob(pkt, "party", gain, "pl" if pkt.lang_pl else "en")
        with self._cv:
            self._queue.append(job)
            self._cv.notify()

    def _filter(self, pkt: protocol.Packet) -> tuple[Optional[str], Optional[VoiceJob]]:
        if not self.enabled or self.muted:
            return "off", None
        kind = pkt.chat_kind_name
        if kind in SAY_KINDS:
            channel = "say"
        elif kind in PARTY_KINDS:
            channel = "party"
        else:
            return "channel", None
        text = (pkt.text or "").strip()
        if pkt.is_secret or not text:
            return "empty", None
        if pkt.is_self and (pkt.lang_pl or not self.cfg.get("speak_own_messages", True)):
            return "own", None
        _, _, norm = split_sender(pkt.sender, "")
        blocked = {unicodedata.normalize("NFKC", b).casefold() for b in self.cfg.get("blocked_players", [])}
        if norm in blocked:
            return "blocked", None
        if text.startswith("/") or _NOISE_RE.match(text):
            return "noise", None
        now = time.monotonic()
        window = float(self.cfg.get("dedup_window_s", 15))
        self._recent = {k: t for k, t in self._recent.items() if now - t < window}
        key = (norm, " ".join(text.lower().split()))
        if key in self._recent:
            return "duplicate", None
        if channel == "party":
            gain = float(self.cfg.get("volume_party", 0.9))
        else:
            dist = 0.0 if pkt.is_self else pkt.distance
            gain = say_gain(dist, self.cfg.get("distance_curve") or [], self.cfg.get("unknown_distance", "play_quiet"),
                            float(self.cfg.get("quiet_gain", 0.35))) * float(self.cfg.get("volume_say", 1.0))
        if gain <= 0.01:
            return "out-of-range", None  # too far to hear: not synthesised, costs nothing
        if not self.budget.allows(min(len(text), int(self.cfg.get("max_chars_per_message", 220)))):
            return "budget", None
        self._recent[key] = now
        lang = "pl" if (pkt.lang_pl and not pkt.is_self) else "en"
        return None, VoiceJob(pkt, channel, gain, lang)

    # ------------------------------------------------------------------ workers
    def _next_job(self) -> Optional[VoiceJob]:
        with self._cv:
            while not self._stop.is_set():
                for i, job in enumerate(self._queue):
                    if job.speaker not in self._speaking:  # one line at a time per speaker
                        del self._queue[i]
                        self._speaking.add(job.speaker)
                        return job
                self._cv.wait(0.5)
        return None

    def _work(self) -> None:
        while not self._stop.is_set():
            job = self._next_job()
            if job is None:
                return
            try:
                if time.monotonic() - job.received > float(self.cfg.get("stale_after_s", 20)):
                    self._skip(job.pkt, "stale")
                else:
                    self._speak(job)
            except Exception as exc:  # never kill the worker
                self.stats["errors"] += 1
                log.warning("voice line failed: %s", exc if isinstance(exc, ElevenLabsError) else type(exc).__name__)
                self._record(job.pkt, "error", gain=job.gain, lang=job.lang, note=str(exc)[:120])
            finally:
                with self._cv:
                    self._speaking.discard(job.speaker)
                    self._cv.notify_all()

    def _speech_text(self, job: VoiceJob) -> tuple[str, str, str]:
        """(text to speak, language code, how it was prepared)."""
        text = job.pkt.text.strip()
        if job.lang == "pl" and self.translator is not None:
            try:
                res = self.translator.translate(text, "en-pl")
                if res.text:
                    return res.text, "pl", "translated"
            except Exception as exc:
                log.info("PL translation for voice failed (%s) - speaking English", type(exc).__name__)
        norm = self.normalizer.normalize(text)
        return norm.text, "en", norm.mode

    def _speak(self, job: VoiceJob) -> None:
        pkt = job.pkt
        assignment = self.resolver.resolve(pkt.sender, pkt.race, pkt.sex)
        if assignment is None:
            self._skip(pkt, "no-voices")
            return
        speak, lang_code, prep = self._speech_text(job)
        limit = int(self.cfg.get("max_chars_per_message", 220))
        if len(speak) > limit:
            speak = speak[:limit].rsplit(" ", 1)[0]
        if not speak or not self.budget.allows(len(speak)):
            self._skip(pkt, "budget" if speak else "empty")
            return
        stats_box: list[TTSStats] = []
        cancel = threading.Event()
        utter = self.mixer.play(job.gain)
        try:
            for chunk in self.tts.stream_tts(assignment.voice_id, speak, lang_code, cancel=cancel,
                                             on_stats=stats_box.append):
                if self._stop.is_set() or utter.cancelled:
                    cancel.set()
                    break
                utter.feed(chunk)
        except ElevenLabsError as exc:
            utter.cancel()
            if exc.status == 404 and assignment.voice_id not in self._missing_voice_warned:
                self._missing_voice_warned.add(assignment.voice_id)
                log.warning("voice %s does not exist in ElevenLabs any more", assignment.voice_id)
            raise
        finally:
            utter.finish()
        stats = stats_box[0] if stats_box else None
        self.budget.add(len(speak), stats.cost if stats else None)
        self.stats["spoken"] += 1
        if stats:
            self.ttfa_ms.append(stats.ttfa_ms)
        self._record(pkt, "spoken", gain=job.gain, lang=lang_code, voice_id=assignment.voice_id,
                     ttfa_ms=stats.ttfa_ms if stats else 0.0, chars=len(speak),
                     cost=stats.cost if stats else None, note=prep, text=speak)
        utter.wait(timeout=60)  # keeps this worker busy while the line plays

    # ------------------------------------------------------------------ bookkeeping
    def _skip(self, pkt: protocol.Packet, reason: str) -> None:
        self.stats[f"skipped:{reason}"] += 1
        if reason not in ("off", "channel", "duplicate"):
            self._record(pkt, f"skipped:{reason}")

    def _record(self, pkt: protocol.Packet, status: str, **kw) -> None:
        text = kw.pop("text", "")
        ev = VoiceEvent(when=time.time(), channel=pkt.chat_kind_name.lower(), sender=pkt.sender, race=pkt.race,
                        sex=pkt.sex, distance=pkt.distance, dist_method=pkt.dist_method, status=status,
                        text=text if self.log_texts else "", **kw)
        self.events.append(ev)
        if self.log_texts and text:
            log.info("voice %s %s: %s", ev.channel, ev.sender, text)
        if self.on_event:
            try:
                self.on_event(ev)
            except Exception:
                log.exception("voice event handler failed")

    def latency_summary(self) -> str:
        if not self.ttfa_ms:
            return "brak pomiarów"
        xs = sorted(self.ttfa_ms)
        p50 = xs[len(xs) // 2]
        p95 = xs[min(len(xs) - 1, int(len(xs) * 0.95))]
        return f"pierwszy dźwięk p50 {p50:.0f} ms, p95 {p95:.0f} ms ({len(xs)} wypowiedzi)"
