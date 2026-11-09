"""Push-to-talk pipeline: record -> Parakeet (PL) -> LLM (EN) -> WoW chat input.

UI-agnostic: progress is reported through the ``ui`` callbacks object, which the Qt
app implements with thread-safe signals.  One job at a time (spec: single-task queue).
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Optional, Protocol

import numpy as np

from . import audio as audio_mod
from .chat_insert import ChatInserter, build_chat_line, set_clipboard_text

log = logging.getLogger("wvt.pipeline")


class PipelineUI(Protocol):
    def status(self, kind: str, title: str, lines: list[str] | None = None, timeout: float = 0.0) -> None: ...
    def result(self, item: "HistoryItem") -> None: ...


@dataclass
class HistoryItem:
    channel: str
    polish: str
    english: str
    chat_line: str
    timings: dict = field(default_factory=dict)
    status: str = ""
    created: float = field(default_factory=time.time)


class PushToTalkPipeline:
    def __init__(self, cfg, recorder: audio_mod.AudioRecorder, stt, translator, inserter: ChatInserter,
                 ui: PipelineUI, wow_foreground: Callable[[], bool]):
        self.cfg = cfg
        self.recorder = recorder
        self.stt = stt
        self.translator = translator
        self.inserter = inserter
        self.ui = ui
        self.wow_foreground = wow_foreground
        self.history: deque[HistoryItem] = deque(maxlen=10)
        self._busy = threading.Lock()
        self._recording_channel: Optional[str] = None
        self._rec_started = 0.0
        self.models_ready = threading.Event()
        self.last_timings: dict = {}
        self.assistant = None  # wvt.assistant.Assistant: the "assistant" push-to-talk channel goes to her
        self.chat_log = None  # wvt.assistant.ChatLog: recent chat, the context of what you say next

    # ------------------------------------------------------------------ hotkey events
    @property
    def recording(self) -> bool:
        return self._recording_channel is not None

    def on_press(self, channel: str) -> None:
        if not self.models_ready.is_set():
            self.ui.status("busy", "Modele jeszcze się ładują…", timeout=2.5)
            return
        if self._busy.locked():
            self.ui.status("busy", "Poczekaj – poprzednia wypowiedź jeszcze się tłumaczy", timeout=2.0)
            return
        try:
            self.recorder.start()
        except Exception as exc:
            self.ui.status("error", "Mikrofon niedostępny", [str(exc)], timeout=6)
            return
        self._recording_channel = channel
        self._rec_started = time.perf_counter()
        self.ui.status("rec", f"Nagrywanie → {self._channel_label(channel)}", ["puść klawisz, aby przetłumaczyć · Esc anuluje"])

    def on_cancel(self) -> None:
        if self._recording_channel is None:
            return
        self.recorder.cancel()
        self._recording_channel = None
        self.ui.status("info", "Anulowano", timeout=1.5)

    def on_release(self, channel: str) -> None:
        if self._recording_channel is None:
            return
        channel = self._recording_channel
        self._recording_channel = None
        audio = self.recorder.stop()
        released_at = time.perf_counter()
        threading.Thread(target=self._process, args=(channel, audio, released_at), name="wvt-ptt", daemon=True).start()

    # ------------------------------------------------------------------ processing
    def _channel_label(self, channel: str) -> str:
        if channel == "assistant":
            return str(self.cfg.get("assistant.name", "Asystentka"))
        cmd = self.cfg.get(f"channels.{channel}", "")
        return f"{channel.upper()} ({cmd})" if cmd else channel.upper()

    # chat kinds that belong to the conversation of each push-to-talk channel
    CONTEXT_KINDS = {
        "say": ("SAY", "YELL"), "yell": ("SAY", "YELL"),
        "party": ("PARTY", "PARTY_LEADER"), "raid": ("RAID", "RAID_LEADER", "PARTY", "PARTY_LEADER"),
        "reply": ("WHISPER", "WHISPER_INFORM"),
    }

    def conversation(self, channel: str) -> list[str]:
        """The last lines of this channel's conversation ("Ann: ...", "You: ...") for the translator."""
        kinds = self.CONTEXT_KINDS.get(channel)
        if self.chat_log is None or not kinds:
            return []
        lines = self.chat_log.recent(n=int(self.cfg.get("translation.context_lines", 6)), kinds=kinds,
                                     max_age_s=float(self.cfg.get("translation.context_seconds", 180)))
        if channel == "reply" and lines:  # /r answers whoever whispered you last: only that conversation
            partner = next((ln.sender for ln in reversed(lines) if ln.kind == "WHISPER"), "")
            lines = [ln for ln in lines if ln.sender == partner]
        return [f"{'You' if ln.is_self else ln.sender.split('-')[0]}: {ln.text}" for ln in lines]

    def process_audio(self, audio: np.ndarray, channel: str, insert: bool = True) -> Optional[HistoryItem]:
        """Synchronous variant used by the CLI and the settings 'test' button."""
        return self._process(channel, audio, time.perf_counter(), insert=insert)

    def _process(self, channel: str, audio: np.ndarray, released_at: float, insert: bool = True) -> Optional[HistoryItem]:
        if not self._busy.acquire(blocking=False):
            self.ui.status("busy", "Poczekaj – poprzednia wypowiedź jeszcze się tłumaczy", timeout=2.0)
            return None
        timings: dict = {}
        polish = ""
        try:
            acfg = self.cfg["audio"]
            threshold = float(acfg.get("silence_rms_threshold", 0.006))
            stats = audio_mod.analyze(audio, threshold)
            timings["audio_s"] = round(stats.duration, 2)
            if stats.duration < 0.25 or stats.speech_seconds < float(acfg.get("min_speech_seconds", 0.35)):
                self.ui.status("info", "Nie usłyszałem mowy", [f"nagranie {stats.duration:.1f}s, poziom {stats.rms:.4f} – sprawdź mikrofon/próg ciszy"], timeout=3.5)
                return None
            clip = audio_mod.trim_and_normalize(audio, threshold)

            self.ui.status("busy", "Rozpoznawanie mowy…")
            t0 = time.perf_counter()
            polish = self.stt.transcribe(clip)
            timings["stt_ms"] = round((time.perf_counter() - t0) * 1000)
            if not polish:
                self.ui.status("info", "Nic nie rozpoznano", timeout=3)
                return None

            if channel == "assistant":  # her own key: no translation, she decides what to do
                if self.assistant is None:
                    self.ui.status("info", "Asystentka jest wyłączona", ["assistant.enabled w config.json"], timeout=4)
                    return None
                self.ui.status("busy", f"{self._channel_label(channel)} myśli…", [f"PL: {polish}"])
                t1 = time.perf_counter()
                reply, line = self.assistant.handle(polish)
                timings["assistant_ms"] = round((time.perf_counter() - t1) * 1000)
                self.last_timings = timings
                item = HistoryItem(channel, polish, reply, line, timings, "assistant")
                if self.cfg.get("privacy.keep_history", True):
                    self.history.appendleft(item)
                self.ui.result(item)
                return item

            self.ui.status("busy", "Tłumaczenie…", [f"PL: {polish}"])
            t1 = time.perf_counter()
            context = self.conversation(channel)
            res = self.translator.translate(polish, "pl-en", context=context)
            timings["context_lines"] = len(context)
            timings["translate_ms"] = round((time.perf_counter() - t1) * 1000)
            english = res.text
            if not english:
                raise RuntimeError("model zwrócił pustą odpowiedź")

            cmd = self.cfg.get(f"channels.{channel}", "")
            chat_line = build_chat_line(cmd, english, int(self.cfg.get("translation.max_chat_chars", 255)))
            # hovering our own message later shows the original Polish instantly
            self.translator.remember(chat_line[len(cmd) + 1:] if cmd else chat_line, polish, "en-pl")

            status = "preview"
            if insert:
                t2 = time.perf_counter()
                status = self.inserter.insert(chat_line, self.wow_foreground())
                timings["insert_ms"] = round((time.perf_counter() - t2) * 1000)
            timings["release_to_text_ms"] = round((time.perf_counter() - released_at) * 1000)
            self.last_timings = timings
            item = HistoryItem(channel, polish, english, chat_line, timings, status)
            if self.cfg.get("privacy.keep_history", True):
                self.history.appendleft(item)
            if self.cfg.get("privacy.log_texts", False):
                log.info("PTT %s | PL=%r | EN=%r | %s", channel, polish, english, timings)
            else:
                log.info("PTT %s | %d->%d chars | %s", channel, len(polish), len(english), timings)
            footer = {
                "inserted": "W czacie – Enter wysyła, Esc anuluje",
                "copied": "Skopiowano do schowka (WoW nie był aktywny) – wklej Ctrl+V",
                "preview": "Podgląd",
            }.get(status, status)
            self.ui.status("ok", footer, [f"PL: {polish}", f"EN: {chat_line}",
                                          f"⏱ STT {timings.get('stt_ms', 0)} ms · tłumaczenie {timings.get('translate_ms', 0)} ms"],
                           timeout=float(self.cfg.get("hud.show_seconds", 6)))
            self.ui.result(item)
            return item
        except Exception as exc:
            log.exception("PTT pipeline failed")
            lines = [str(exc)]
            if polish:
                set_clipboard_text(polish)
                lines.append(f"Transkrypcja skopiowana do schowka: {polish}")
            self.ui.status("error", "Błąd – nic nie zostało wysłane", lines, timeout=8)
            return None
        finally:
            self._busy.release()
