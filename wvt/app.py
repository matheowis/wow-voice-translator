"""Desktop app: tray icon, main window, overlays, hotkeys, hover translation."""

from __future__ import annotations

import logging
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QFont, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSystemTrayIcon,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from . import __version__, protocol
from .audio import AudioRecorder, analyze, trim_and_normalize
from .chat_insert import ChatInserter, build_chat_line, set_clipboard_text
from .config import ROOT, Config
from .hotkeys import Binding, HotkeyManager, KeySpec, keyspec_to_config, parse_key
from .logging_setup import setup_logging
from .overlay import HoverOverlay, HudOverlay
from .pipeline import HistoryItem, PushToTalkPipeline
from .signal_reader import SignalReader
from .wow_window import WowWindow, WowWindowTracker

log = logging.getLogger("wvt.app")

CHANNEL_CHOICES = ["say", "party", "raid", "instance", "guild", "yell", "reply", "assistant"]


def make_icon(color: str = "#ffd100") -> QIcon:
    pm = QPixmap(64, 64)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setBrush(QColor(20, 20, 28))
    p.setPen(QColor(color))
    p.drawRoundedRect(2, 2, 60, 60, 14, 14)
    p.setBrush(QColor(color))
    p.setPen(Qt.PenStyle.NoPen)
    p.drawRoundedRect(24, 10, 16, 28, 8, 8)  # microphone capsule
    p.setPen(QColor(color))
    p.setBrush(Qt.BrushStyle.NoBrush)
    pen = p.pen()
    pen.setWidth(4)
    p.setPen(pen)
    p.drawArc(16, 18, 32, 28, 200 * 16, 140 * 16)
    p.drawLine(32, 46, 32, 54)
    p.drawLine(24, 54, 40, 54)
    p.end()
    return QIcon(pm)


class Bridge(QObject):
    """Thread-safe signals from worker/hook threads to the Qt main thread."""

    status = Signal(str, str, list, float)
    result = Signal(object)
    hotkey = Signal(str, str)
    hover_start = Signal(object, object)
    hover_stop = Signal()
    hover_text = Signal(object, str, str)
    models = Signal(str, bool)
    key_captured = Signal(object)
    voice_event = Signal(object)
    voices_enabled = Signal(bool)  # the assistant mutes/unmutes the character voices


class QtPipelineUI:
    def __init__(self, bridge: Bridge):
        self.bridge = bridge

    def status(self, kind, title, lines=None, timeout=0.0):
        self.bridge.status.emit(kind, title, list(lines or []), float(timeout))

    def result(self, item):
        self.bridge.result.emit(item)


class MainWindow(QWidget):
    def __init__(self, app: "TranslatorApp"):
        super().__init__()
        self.app = app
        self.setWindowTitle(f"WoW Voice Translator {__version__}")
        self.setWindowIcon(make_icon())
        self.resize(760, 560)
        from .voice.ui import VoiceTab

        tabs = QTabWidget()
        tabs.addTab(self._build_translate_tab(), "Tłumacz")
        tabs.addTab(self._build_settings_tab(), "Ustawienia")
        self.voice_tab = VoiceTab(app)
        tabs.addTab(self.voice_tab, "Głosy")
        tabs.addTab(self._build_diag_tab(), "Diagnostyka")
        lay = QVBoxLayout(self)
        lay.addWidget(tabs)

    # ------------------------------------------------------------------ translate tab
    def _build_translate_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        top = QHBoxLayout()
        top.addWidget(QLabel("Kanał:"))
        self.channel_combo = QComboBox()
        self.channel_combo.addItems(CHANNEL_CHOICES)
        self.channel_combo.setCurrentText("party")
        top.addWidget(self.channel_combo)
        top.addStretch(1)
        self.status_label = QLabel("Ładowanie modeli…")
        top.addWidget(self.status_label)
        lay.addLayout(top)

        lay.addWidget(QLabel("Polski (transkrypcja – możesz poprawić):"))
        self.pl_edit = QPlainTextEdit()
        self.pl_edit.setMaximumHeight(90)
        lay.addWidget(self.pl_edit)
        row = QHBoxLayout()
        b_tr = QPushButton("Przetłumacz PL → EN")
        b_tr.clicked.connect(self.on_translate_clicked)
        row.addWidget(b_tr)
        row.addStretch(1)
        lay.addLayout(row)

        lay.addWidget(QLabel("Angielski (możesz poprawić przed wstawieniem):"))
        self.en_edit = QPlainTextEdit()
        self.en_edit.setMaximumHeight(90)
        lay.addWidget(self.en_edit)
        row2 = QHBoxLayout()
        b_copy = QPushButton("Kopiuj")
        b_copy.clicked.connect(self.on_copy_clicked)
        b_ins = QPushButton("Wstaw do czatu WoW")
        b_ins.clicked.connect(self.on_insert_clicked)
        row2.addWidget(b_copy)
        row2.addWidget(b_ins)
        row2.addStretch(1)
        self.len_label = QLabel("")
        row2.addWidget(self.len_label)
        lay.addLayout(row2)
        self.en_edit.textChanged.connect(self._update_len)

        lay.addWidget(QLabel("Ostatnie tłumaczenia (tylko w pamięci):"))
        self.history = QListWidget()
        self.history.itemClicked.connect(self.on_history_clicked)
        lay.addWidget(self.history, 1)
        hint = QLabel(self.app.hotkey_hint())
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #888")
        lay.addWidget(hint)
        self.hint_label = hint
        return w

    def _update_len(self) -> None:
        line = build_chat_line(self.app.cfg.get(f"channels.{self.channel_combo.currentText()}", ""), self.en_edit.toPlainText())
        n = len(line.encode("utf-8"))
        self.len_label.setText(f"{n}/255 B")

    def on_translate_clicked(self) -> None:
        text = self.pl_edit.toPlainText().strip()
        if not text:
            return
        self.en_edit.setPlainText("…")

        def job():
            try:
                res = self.app.translator.translate(text, "pl-en")
                self.app.bridge.hover_text.emit(("manual",), res.text, "")
            except Exception as exc:
                self.app.bridge.hover_text.emit(("manual",), "", str(exc))

        self.app.hover_pool.submit(job)

    def set_manual_translation(self, text: str, error: str) -> None:
        self.en_edit.setPlainText(text if text else f"[błąd] {error}")

    def on_copy_clicked(self) -> None:
        cmd = self.app.cfg.get(f"channels.{self.channel_combo.currentText()}", "")
        set_clipboard_text(build_chat_line(cmd, self.en_edit.toPlainText()))

    def on_insert_clicked(self) -> None:
        cmd = self.app.cfg.get(f"channels.{self.channel_combo.currentText()}", "")
        line = build_chat_line(cmd, self.en_edit.toPlainText())
        if not line.strip():
            return
        win = self.app.tracker.find(max_age=0)
        if win is None:
            set_clipboard_text(line)
            QMessageBox.information(self, "WoW", "Nie znaleziono okna WoW – tekst skopiowano do schowka.")
            return
        # bring WoW to the front, then insert like push-to-talk does
        try:
            from . import winapi

            winapi.user32.SetForegroundWindow(win.hwnd)
        except Exception:
            pass
        QTimer.singleShot(250, lambda: threading.Thread(
            target=lambda: self.app.inserter.insert(line, self.app.tracker.foreground_is_wow()), daemon=True).start())

    def on_history_clicked(self, item: QListWidgetItem) -> None:
        h: HistoryItem = item.data(Qt.ItemDataRole.UserRole)
        self.pl_edit.setPlainText(h.polish)
        self.en_edit.setPlainText(h.english)
        self.channel_combo.setCurrentText(h.channel)

    def add_history(self, h: HistoryItem) -> None:
        self.pl_edit.setPlainText(h.polish)
        self.en_edit.setPlainText(h.english)
        it = QListWidgetItem(f"[{h.channel}] {h.english}   ←   {h.polish}")
        it.setData(Qt.ItemDataRole.UserRole, h)
        self.history.insertItem(0, it)
        while self.history.count() > 10:
            self.history.takeItem(self.history.count() - 1)

    # ------------------------------------------------------------------ settings tab
    def _build_settings_tab(self) -> QWidget:
        cfg = self.app.cfg
        w = QWidget()
        lay = QVBoxLayout(w)

        mic_box = QGroupBox("Mikrofon")
        f = QFormLayout(mic_box)
        self.mic_combo = QComboBox()
        self.mic_combo.addItem("Domyślny systemowy", None)
        try:
            for idx, name in AudioRecorder.list_input_devices():
                self.mic_combo.addItem(name, name)
        except Exception as exc:
            self.mic_combo.addItem(f"(błąd: {exc})", None)
        cur = cfg.get("audio.device")
        if cur is not None:
            i = self.mic_combo.findData(cur)
            if i >= 0:
                self.mic_combo.setCurrentIndex(i)
        f.addRow("Urządzenie:", self.mic_combo)
        self.threshold = QDoubleSpinBox()
        self.threshold.setDecimals(4)
        self.threshold.setRange(0.0005, 0.2)
        self.threshold.setSingleStep(0.001)
        self.threshold.setValue(float(cfg.get("audio.silence_rms_threshold", 0.006)))
        f.addRow("Próg ciszy (RMS):", self.threshold)
        test_row = QHBoxLayout()
        self.test_btn = QPushButton("Test: nagraj 4 s i rozpoznaj")
        self.test_btn.clicked.connect(self.on_mic_test)
        test_row.addWidget(self.test_btn)
        self.test_result = QLabel("")
        self.test_result.setWordWrap(True)
        test_row.addWidget(self.test_result, 1)
        f.addRow(test_row)
        lay.addWidget(mic_box)

        ptt_box = QGroupBox("Push-to-talk (przytrzymaj, mów po polsku, puść)")
        v = QVBoxLayout(ptt_box)
        self.ptt_table = QTableWidget(0, 2)
        self.ptt_table.setHorizontalHeaderLabels(["Klawisz", "Kanał"])
        self.ptt_table.horizontalHeader().setStretchLastSection(True)
        self.ptt_table.verticalHeader().setVisible(False)
        self.ptt_table.setMaximumHeight(140)
        for b in cfg.get("push_to_talk", []):
            self._add_ptt_row(b.get("key", ""), b.get("channel", "say"))
        v.addWidget(self.ptt_table)
        row = QHBoxLayout()
        b_add = QPushButton("Dodaj")
        b_add.clicked.connect(lambda: self._add_ptt_row("f9", "say"))
        b_cap = QPushButton("Przypisz klawisz (naciśnij po kliknięciu)…")
        b_cap.clicked.connect(self.on_capture_key)
        b_del = QPushButton("Usuń zaznaczony")
        b_del.clicked.connect(lambda: self.ptt_table.removeRow(self.ptt_table.currentRow()))
        row.addWidget(b_add)
        row.addWidget(b_cap)
        row.addWidget(b_del)
        row.addStretch(1)
        v.addLayout(row)
        self.only_wow = QCheckBox("Działaj tylko, gdy okno WoW jest aktywne (zalecane)")
        self.only_wow.setChecked(bool(cfg.get("wow.only_when_wow_active", True)))
        v.addWidget(self.only_wow)
        self.insert_method = QComboBox()
        self.insert_method.addItem("Otwórz czat klawiszem „/” i wklej (zalecane)", "slash")
        self.insert_method.addItem("Otwórz czat Enterem i wklej", "enter")
        self.insert_method.addItem("Tylko kopiuj do schowka", "clipboard")
        self.insert_method.setCurrentIndex(max(0, self.insert_method.findData(cfg.get("insert.method", "slash"))))
        fl = QFormLayout()
        fl.addRow("Wstawianie:", self.insert_method)
        v.addLayout(fl)
        lay.addWidget(ptt_box)

        hov_box = QGroupBox("Czat: tłumaczenie po najechaniu myszką (wymaga addonu)")
        hv = QFormLayout(hov_box)
        self.hover_enabled = QCheckBox("Włączone")
        self.hover_enabled.setChecked(bool(cfg.get("hover.enabled", True)))
        hv.addRow(self.hover_enabled)
        self.font_scale = QDoubleSpinBox()
        self.font_scale.setRange(0.6, 2.0)
        self.font_scale.setSingleStep(0.05)
        self.font_scale.setValue(float(cfg.get("hover.font_scale", 1.0)))
        hv.addRow("Skala czcionki:", self.font_scale)
        lay.addWidget(hov_box)

        save = QPushButton("Zapisz ustawienia")
        save.clicked.connect(self.on_save)
        lay.addWidget(save)
        lay.addStretch(1)
        return w

    def _add_ptt_row(self, key: str, channel: str) -> None:
        r = self.ptt_table.rowCount()
        self.ptt_table.insertRow(r)
        self.ptt_table.setItem(r, 0, QTableWidgetItem(key))
        combo = QComboBox()
        combo.addItems(CHANNEL_CHOICES)
        combo.setCurrentText(channel)
        self.ptt_table.setCellWidget(r, 1, combo)

    def on_capture_key(self) -> None:
        row = self.ptt_table.currentRow()
        if row < 0:
            QMessageBox.information(self, "Klawisz", "Najpierw zaznacz wiersz w tabeli.")
            return
        self.test_result.setText("Naciśnij klawisz lub przycisk myszy (4/5/środkowy)…")
        self._capture_row = row
        self.app.capture_key()

    def on_key_captured(self, spec: KeySpec) -> None:
        row = getattr(self, "_capture_row", -1)
        if row >= 0:
            self.ptt_table.setItem(row, 0, QTableWidgetItem(keyspec_to_config(spec)))
            self.test_result.setText(f"Przypisano: {spec.describe()} – pamiętaj o „Zapisz ustawienia”.")

    def on_mic_test(self) -> None:
        self.test_btn.setEnabled(False)
        self.test_result.setText("Nagrywam 4 s – powiedz coś po polsku…")

        def job():
            try:
                rec = AudioRecorder(self.mic_combo.currentData(), always_open=False)
                x = rec.record_seconds(4.0)
                st = analyze(x, self.threshold.value())
                msg = f"poziom RMS {st.rms:.4f}, mowa {st.speech_seconds:.1f}s"
                if self.app.stt is not None and self.app.stt.model is not None and st.speech_seconds > 0.2:
                    t0 = time.perf_counter()
                    text = self.app.stt.transcribe(trim_and_normalize(x, self.threshold.value()))
                    msg += f" | „{text}” ({(time.perf_counter() - t0) * 1000:.0f} ms)"
                self.app.bridge.hover_text.emit(("mictest",), msg, "")
            except Exception as exc:
                self.app.bridge.hover_text.emit(("mictest",), "", str(exc))

        threading.Thread(target=job, daemon=True).start()

    def set_mic_test(self, text: str, error: str) -> None:
        self.test_btn.setEnabled(True)
        self.test_result.setText(text or f"Błąd: {error}")

    def on_save(self) -> None:
        cfg = self.app.cfg
        bindings = []
        for r in range(self.ptt_table.rowCount()):
            key_item = self.ptt_table.item(r, 0)
            combo = self.ptt_table.cellWidget(r, 1)
            key = key_item.text().strip() if key_item else ""
            if not key:
                continue
            try:
                parse_key(key)
            except ValueError as exc:
                QMessageBox.warning(self, "Klawisz", str(exc))
                return
            bindings.append({"key": key, "channel": combo.currentText()})
        cfg.set("push_to_talk", bindings)
        cfg.set("audio.device", self.mic_combo.currentData())
        cfg.set("audio.silence_rms_threshold", round(self.threshold.value(), 5))
        cfg.set("wow.only_when_wow_active", self.only_wow.isChecked())
        cfg.set("insert.method", self.insert_method.currentData())
        cfg.set("hover.enabled", self.hover_enabled.isChecked())
        cfg.set("hover.font_scale", round(self.font_scale.value(), 2))
        cfg.save()
        self.app.apply_settings()
        self.hint_label.setText(self.app.hotkey_hint())
        QMessageBox.information(self, "Zapisano", f"Ustawienia zapisane w {cfg.path}")

    # ------------------------------------------------------------------ diagnostics tab
    def _build_diag_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        self.diag = QLabel("")
        self.diag.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.diag.setWordWrap(True)
        self.diag.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        f = QFont("Consolas")
        f.setPointSize(9)
        self.diag.setFont(f)
        lay.addWidget(self.diag, 1)
        row = QHBoxLayout()
        b_addon = QPushButton("Zainstaluj / zaktualizuj addon w WoW")
        b_addon.clicked.connect(self.on_install_addon)
        b_logs = QPushButton("Otwórz folder logów")
        b_logs.clicked.connect(lambda: os.startfile(str(ROOT / "logs")))
        row.addWidget(b_addon)
        row.addWidget(b_logs)
        row.addStretch(1)
        lay.addLayout(row)
        timer = QTimer(self)
        timer.timeout.connect(self.refresh_diag)
        timer.start(1000)
        return w

    def on_install_addon(self) -> None:
        from .cli import install_addon

        try:
            dst = install_addon(self.app.cfg)
            QMessageBox.information(self, "Addon", f"Zainstalowano w:\n{dst}\n\nW grze wpisz /reload, potem /wvt test.")
        except Exception as exc:
            QMessageBox.warning(self, "Addon", str(exc))

    def refresh_diag(self) -> None:
        if not self.isVisible():
            return
        from .gpu import vram_used_mb

        a = self.app
        v = vram_used_mb()
        sig = a.reader.last_packet_at if a.reader else 0
        lines = [
            f"Modele:      {a.model_state}",
            f"STT:         Parakeet TDT 0.6B v3 na {a.stt.device if a.stt else '-'}  (ładowanie {a.stt.load_seconds if a.stt else 0:.1f}s)",
            f"LLM:         {a.llm.model_name if a.llm else '-'}  (start {a.llm.load_seconds if a.llm else 0:.1f}s)",
            f"VRAM:        {v['used']:.0f} / {v['total']:.0f} MB ({v['name']})" if v else "VRAM:        brak danych NVML",
            f"WoW:         {'aktywny' if a.tracker.foreground_is_wow() else 'nieaktywny / nie znaleziono'}",
            f"Addon:       ostatni sygnał {time.strftime('%H:%M:%S', time.localtime(sig)) if sig else 'jeszcze nie odebrano (w grze: /wvt test)'}",
            f"Głosy:       {a.voice_status}",
            f"Ostatnie PTT: {a.pipeline.last_timings if a.pipeline else {}}",
            "",
            "Hotkeye:",
            a.hotkey_hint(),
        ]
        self.diag.setText("\n".join(lines))

    def closeEvent(self, event):  # noqa: N802 - minimize to tray instead of quitting
        event.ignore()
        self.hide()


class TranslatorApp:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.bridge = Bridge()
        from .devsim import SIM_TITLE

        self.tracker = WowWindowTracker(cfg.get("wow.process_names", []), [SIM_TITLE] if "--sim" in sys.argv else [])
        self.stt = None
        self.llm = None
        self.translator = None
        self.pipeline: Optional[PushToTalkPipeline] = None
        self.hotkeys: Optional[HotkeyManager] = None
        self.reader: Optional[SignalReader] = None
        self.voice = None  # VoiceManager, built in _init_voice
        self.assistant = None  # wvt.assistant.Assistant, built with the models
        from .assistant import ChatLog

        self.chat_log = ChatLog()  # recent chat from the addon: the assistant's memory and the translator's context
        self.voice_status = "uruchamianie…"
        self.model_state = "ładowanie…"
        self.hover_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="wvt-hover")
        self._hover_current = None
        self._hover_window: Optional[WowWindow] = None
        self._hover_pkt: Optional[protocol.Packet] = None

        a = cfg["audio"]
        self.recorder = AudioRecorder(a.get("device"), a.get("sample_rate", 16000), a.get("mic_always_open", True),
                                      a.get("pre_roll_ms", 300), a.get("max_record_seconds", 30))
        ins = cfg["insert"]
        self.inserter = ChatInserter(ins.get("method", "slash"), ins.get("key_delay_ms", 120), ins.get("restore_clipboard", False))

        self.hud = HudOverlay(cfg.get("hud.position", "top_center"))
        h = cfg["hover"]
        self.hover = HoverOverlay(h.get("font_family", "Arial Narrow"), h.get("font_scale", 1.0), h.get("background_opacity", 0.9))
        self.window = MainWindow(self)
        self._build_tray()

        b = self.bridge
        b.status.connect(self._on_status)
        b.result.connect(self.window.add_history)
        b.hotkey.connect(self._on_hotkey)
        b.hover_start.connect(self._on_hover_start)
        b.hover_stop.connect(self._on_hover_stop)
        b.hover_text.connect(self._on_hover_text)
        b.models.connect(self._on_models)
        b.key_captured.connect(self.window.on_key_captured)
        b.voice_event.connect(self.window.voice_tab.add_event)
        b.voices_enabled.connect(lambda on: self.act_voice.setChecked(on))

        self._level_timer = QTimer()
        self._level_timer.setInterval(100)
        self._level_timer.timeout.connect(self._update_level)
        self._level_timer.start()

    # ------------------------------------------------------------------ startup
    def start(self) -> None:
        self.tray.show()
        self._add_assistant_key()
        self._start_hotkeys()
        self.reader = SignalReader(self.tracker, float(self.cfg.get("hover.poll_hz", 25)),
                                   on_start=lambda p, w: self.bridge.hover_start.emit(p, w),
                                   on_stop=lambda: self.bridge.hover_stop.emit(),
                                   on_voice=self._on_voice_packet)
        self.reader.enabled = bool(self.cfg.get("hover.enabled", True))
        self.reader.require_foreground = "--sim" not in sys.argv
        self.reader.start()
        threading.Thread(target=self._init_voice, name="wvt-voice-init", daemon=True).start()
        threading.Thread(target=self._load_models, name="wvt-load", daemon=True).start()
        if self.cfg.get("audio.mic_always_open", True):
            try:
                self.recorder.open()
            except Exception as exc:
                self.bridge.status.emit("error", "Mikrofon niedostępny", [str(exc)], 8.0)

    def _load_models(self) -> None:
        from .cli import make_llm, make_stt
        from .translator import Translator

        try:
            # LLM first: the STT "auto" device choice then sees the real free VRAM
            self.bridge.models.emit("uruchamianie lokalnego LLM…", False)
            self.llm = make_llm(self.cfg)
            self.llm.start()
            translator = Translator(self.llm, self.cfg["translation"])
            translator.translate("Cześć, test.", "pl-en")  # fills the prompt cache
            translator.translate("hi, test", "en-pl")
            self.translator = translator  # hover translation works from here on
            if self.voice is not None:  # PL voice mode and the LLM step of the normalizer
                self.voice.translator = translator
                self.voice.normalizer.llm = self.llm
            self.bridge.models.emit("ładowanie Parakeet (STT)…", False)
            self.stt = make_stt(self.cfg)
            self.stt.load()
            self.stt.warmup()
            self.pipeline = PushToTalkPipeline(self.cfg, self.recorder, self.stt, self.translator, self.inserter,
                                               QtPipelineUI(self.bridge), self.tracker.foreground_is_wow)
            if self.cfg.get("assistant.enabled", True):
                from .assistant import Assistant

                self.assistant = Assistant(self.cfg, self.translator, self.llm, self.inserter, QtPipelineUI(self.bridge),
                                           self.tracker.foreground_is_wow, voice_getter=lambda: self.voice,
                                           set_voices_enabled=self.bridge.voices_enabled.emit, chat_log=self.chat_log)
                self.pipeline.assistant = self.assistant
            self.pipeline.chat_log = self.chat_log
            self.pipeline.models_ready.set()
            self.bridge.models.emit(f"gotowe (STT: {self.stt.device}, LLM: {self.llm.model_name})", True)
        except Exception as exc:
            log.exception("model loading failed")
            self.bridge.models.emit(f"BŁĄD: {exc}", False)
            self.bridge.status.emit("error", "Nie udało się załadować modeli", [str(exc), "Szczegóły: logs/wvt.log"], 15.0)

    def _init_voice(self) -> None:
        """ElevenLabs voices for /say and /party. Missing key or store never stops the app."""
        try:
            from .config import resolve_path
            from .envfile import load_env, secret
            from .voice.budget import DailyBudget
            from .voice.elevenlabs import ElevenLabsClient
            from .voice.manager import VoiceManager
            from .voice.mixer import AudioMixer
            from .voice.normalizer import TTSNormalizer
            from .voice.resolver import VoiceResolver
            from .voice.store import open_store

            v = self.cfg["voice"]
            load_env()
            api_key = secret(v.get("api_key_env", "ELEVENLABS_API_KEY"))
            if not api_key:
                self.voice_status = "wyłączone – brak ELEVENLABS_API_KEY w pliku .env"
                return
            data_dir = resolve_path(v["store"].get("fallback_dir", "data"))
            store = open_store(v["store"], data_dir)
            resolver = VoiceResolver(store, v.get("realm", "Forever"), v.get("race_aliases") or {},
                                     v.get("unknown_race_pool", "Human"))
            tts = ElevenLabsClient(api_key, v.get("model_id", "eleven_v4_turbo"), v.get("output_format", "pcm_24000"),
                                   float(v.get("request_timeout_s", 15)), int(v.get("max_retries", 2)))
            mixer = AudioMixer(int(v.get("sample_rate", 24000)), v.get("output_device"))
            normalizer = TTSNormalizer(self.cfg["normalizer"], llm=self.llm if self.translator else None)
            budget = DailyBudget(data_dir / "voice_usage.json", int(v.get("daily_char_budget", 30000)))
            manager = VoiceManager(v, tts, resolver, mixer, normalizer, budget, translator=self.translator,
                                   on_event=self.bridge.voice_event.emit,
                                   log_texts=bool(self.cfg.get("privacy.log_texts", False)))
            manager.start()
            self.voice = manager
            if self.translator is not None:  # models finished first
                manager.translator = self.translator
                manager.normalizer.llm = self.llm
            pools = resolver.profile_count
            self.voice_status = (f"gotowe – {pools} głosów, {store.description}" if pools else
                                 f"brak głosów – uruchom: python -m wvt.voice.design ({store.description})")
            log.info("voices ready: %d profiles, store %s", pools, store.kind)
        except Exception as exc:
            log.exception("voice init failed")
            self.voice_status = f"BŁĄD: {exc}"

    def _on_voice_packet(self, pkt: protocol.Packet) -> None:  # signal reader thread
        try:  # chat log (the assistant's memory, the translator's context) and game state
            if self.assistant is not None:
                self.assistant.on_packet(pkt)  # its log is self.chat_log
            else:
                self.chat_log.add_packet(pkt)
        except Exception:
            log.exception("chat log / assistant packet handler failed")
        if self.voice is not None and pkt.type == protocol.TYPE_VOICE:
            self.voice.on_packet(pkt)

    def _add_assistant_key(self) -> None:
        """Configs written before the assistant existed get her push-to-talk key once."""
        if not self.cfg.get("assistant.enabled", True) or self.cfg.get("assistant.default_key_added", False):
            return
        bindings = list(self.cfg.get("push_to_talk", []))
        if not any(b.get("channel") == "assistant" for b in bindings):
            bindings.append({"key": self.cfg.get("assistant.default_key", "mouse5"), "channel": "assistant"})
            self.cfg.set("push_to_talk", bindings)
        self.cfg.set("assistant.default_key_added", True)
        self.cfg.save()

    def _toggle_voice(self, on: bool) -> None:
        self.cfg.set("voice.enabled", on)
        if self.voice is not None:
            self.voice.enabled = on
            if not on:
                self.voice.silence()

    def _start_hotkeys(self) -> None:
        if self.hotkeys:
            self.hotkeys.stop()
        bindings = []
        for b in self.cfg.get("push_to_talk", []):
            try:
                bindings.append(Binding(parse_key(b["key"]), b.get("channel", "say")))
            except (ValueError, KeyError) as exc:
                log.warning("bad PTT binding %s: %s", b, exc)
        only_wow = bool(self.cfg.get("wow.only_when_wow_active", True))
        self.hotkeys = HotkeyManager(
            bindings,
            on_event=lambda kind, binding: self.bridge.hotkey.emit(kind, binding.channel if binding else ""),
            is_active=(self.tracker.foreground_is_wow if only_wow else (lambda: True)),
        )
        self.hotkeys.start()

    def apply_settings(self) -> None:
        a = self.cfg["audio"]
        new_dev = a.get("device")
        if new_dev != self.recorder.device:
            self.recorder.close()
            self.recorder.device = new_dev
            if a.get("mic_always_open", True):
                try:
                    self.recorder.open()
                except Exception as exc:
                    self._on_status("error", "Mikrofon niedostępny", [str(exc)], 8.0)
        self.inserter.method = self.cfg.get("insert.method", "slash")
        self.hover.font_scale = float(self.cfg.get("hover.font_scale", 1.0))
        if self.reader:
            self.reader.enabled = bool(self.cfg.get("hover.enabled", True))
        self.act_hover.setChecked(bool(self.cfg.get("hover.enabled", True)))
        self._start_hotkeys()

    def capture_key(self) -> None:
        if self.hotkeys:
            self.hotkeys.capture_next = lambda spec: self.bridge.key_captured.emit(spec)

    def hotkey_hint(self) -> str:
        parts = []
        for b in self.cfg.get("push_to_talk", []):
            try:
                spec = parse_key(b["key"])
                if b.get("channel") == "assistant":
                    parts.append(f"przytrzymaj {spec.describe()} → rozmowa z {self.cfg.get('assistant.name', 'asystentką')}")
                    continue
                parts.append(f"przytrzymaj {spec.describe()} → {b.get('channel', '?')} ({self.cfg.get('channels.' + b.get('channel', ''), '')})")
            except Exception:
                parts.append(f"{b.get('key')} → {b.get('channel')} (błędny klawisz)")
        return " · ".join(parts) + " · Esc w trakcie nagrywania anuluje"

    # ------------------------------------------------------------------ tray
    def _build_tray(self) -> None:
        self.tray = QSystemTrayIcon(make_icon())
        self.tray.setToolTip("WoW Voice Translator – ładowanie modeli…")
        menu = QMenu()
        act_show = QAction("Pokaż okno", menu)
        act_show.triggered.connect(self.show_window)
        menu.addAction(act_show)
        self.act_hover = QAction("Tłumaczenie po najechaniu w czacie", menu, checkable=True)
        self.act_hover.setChecked(bool(self.cfg.get("hover.enabled", True)))
        self.act_hover.toggled.connect(self._toggle_hover)
        menu.addAction(self.act_hover)
        self.act_ptt = QAction("Push-to-talk aktywny", menu, checkable=True)
        self.act_ptt.setChecked(True)
        menu.addAction(self.act_ptt)
        self.act_voice = QAction("Głosy postaci (ElevenLabs)", menu, checkable=True)
        self.act_voice.setChecked(bool(self.cfg.get("voice.enabled", True)))
        self.act_voice.toggled.connect(self._toggle_voice)
        menu.addAction(self.act_voice)
        act_silence = QAction("Przerwij odczyt", menu)
        act_silence.triggered.connect(lambda: self.voice and self.voice.silence())
        menu.addAction(act_silence)
        menu.addSeparator()
        act_quit = QAction("Zakończ", menu)
        act_quit.triggered.connect(self.quit)
        menu.addAction(act_quit)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(lambda reason: self.show_window() if reason == QSystemTrayIcon.ActivationReason.DoubleClick else None)
        self._tray_menu = menu

    def show_window(self) -> None:
        self.window.show()
        self.window.raise_()
        self.window.activateWindow()

    def _toggle_hover(self, on: bool) -> None:
        self.cfg.set("hover.enabled", on)
        if self.reader:
            self.reader.enabled = on
        if not on:
            self.hover.hide()

    def quit(self) -> None:
        try:
            if self.hotkeys:
                self.hotkeys.stop()
            if self.reader:
                self.reader.stop()
            if self.voice is not None:
                self.voice.stop()
                self.voice.mixer.close()
                self.voice.tts.close()
                self.voice.resolver.store.close()
            self.recorder.close()
            if self.llm:
                self.llm.stop()
        finally:
            self.tray.hide()
            QApplication.quit()

    # ------------------------------------------------------------------ slots
    def _on_models(self, text: str, ready: bool) -> None:
        self.model_state = text
        self.window.status_label.setText(("✅ " if ready else "⏳ ") + text)
        self.tray.setToolTip("WoW Voice Translator – " + text)
        if ready:
            self._on_status("ok", "WoW Voice Translator gotowy", [self.hotkey_hint()], 6.0)

    def _on_status(self, kind: str, title: str, lines: list, timeout: float) -> None:
        if not self.cfg.get("hud.enabled", True):
            return
        win = self.tracker.find(max_age=1.0)
        self.hud.set_anchor(win.client if win else None)
        self.hud.show_status(kind, title, lines, timeout)

    def _update_level(self) -> None:
        if self.pipeline and self.pipeline.recording:
            self.hud.level = self.recorder.level

    def _on_hotkey(self, kind: str, channel: str) -> None:
        if not self.act_ptt.isChecked():
            return
        if self.pipeline is None:
            self._on_status("busy", "Modele jeszcze się ładują…", [self.model_state], 2.5)
            return
        if kind == "press":
            self.pipeline.on_press(channel)
            if self.hotkeys:
                self.hotkeys.recording = self.pipeline.recording
        elif kind == "release":
            self.pipeline.on_release(channel)
            if self.hotkeys:
                self.hotkeys.recording = False
        elif kind == "cancel":
            self.pipeline.on_cancel()
            if self.hotkeys:
                self.hotkeys.recording = False

    # hover ------------------------------------------------------------
    def _line_geometry(self, pkt: protocol.Packet, win: WowWindow):
        cx, cy, cw, ch = win.client
        x, y, w, h = protocol.base_rect_to_client_px(pkt, cw, ch)
        return (cx + x, cy + y, w, h), protocol.font_px(pkt, ch)

    def _on_hover_start(self, pkt: protocol.Packet, win: Optional[WowWindow]) -> None:
        if win is None or not self.cfg.get("hover.enabled", True):
            return
        rect, fpx = self._line_geometry(pkt, win)
        self._hover_current = pkt.identity()
        self._hover_window = win
        self._hover_pkt = pkt
        if pkt.is_secret:
            self.hover.show_text(rect, fpx, pkt.color, pkt.prefix, "🔒", "wiadomość ukryta przez grę (ograniczenia addonów w instancji)")
            return
        if not pkt.text:
            return
        if pkt.chat_kind_name == "SYSTEM" and not self.cfg.get("hover.translate_system_messages", True):
            return
        note = "✓ połączenie z addonem działa" if pkt.type == protocol.TYPE_TEST else ""
        if self.translator is None:
            self.hover.show_text(rect, fpx, pkt.color, pkt.prefix, pkt.text, "⏳ modele się ładują")
            return
        cached = self.translator.cache.get(("en-pl", " ".join(pkt.text.split()))) or self.translator.exact_phrase(pkt.text)
        if cached is not None:
            self.hover.show_text(rect, fpx, pkt.color, pkt.prefix, cached, note)
            return
        self.hover.show_text(rect, fpx, pkt.color, pkt.prefix, "…", "tłumaczę")
        ident = pkt.identity()

        def job():
            if self._hover_current != ident:
                return  # mouse already moved on
            try:
                res = self.translator.translate(pkt.text, "en-pl")
                self.bridge.hover_text.emit(ident, res.text, note)
            except Exception as exc:
                self.bridge.hover_text.emit(ident, "", f"błąd: {exc}")

        self.hover_pool.submit(job)

    def _on_hover_text(self, ident, text: str, note: str) -> None:
        if ident == ("manual",):
            self.window.set_manual_translation(text, note)
            return
        if ident == ("mictest",):
            self.window.set_mic_test(text, note)
            return
        if ident != self._hover_current or self._hover_window is None:
            return
        pkt = self._hover_pkt
        rect, fpx = self._line_geometry(pkt, self._hover_window)
        self.hover.show_text(rect, fpx, pkt.color, pkt.prefix, text or pkt.text, note)

    def _on_hover_stop(self) -> None:
        self._hover_current = None
        self.hover.hide()


def run() -> int:
    # Qt 6 makes the process per-monitor DPI aware (v2), so all Win32 coordinates used by
    # the hotkey/capture code are physical pixels, like WoW's.
    import tempfile

    from PySide6.QtCore import QLockFile

    cfg = Config.load()
    setup_logging(cfg)
    log.info("WoW Voice Translator %s starting", __version__)
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName("WoW Voice Translator")
    # one instance only: a second one would fight over the hotkeys and the LLM port
    lock = QLockFile(os.path.join(tempfile.gettempdir(), "wow-voice-translator.lock"))
    if not lock.tryLock(200):
        QMessageBox.information(None, "WoW Voice Translator", "Aplikacja już działa (ikona w zasobniku).")
        return 0
    app._wvt_lock = lock  # keep the lock alive for the whole session
    tr = TranslatorApp(cfg)
    tr.start()
    if "--show" in sys.argv or not QSystemTrayIcon.isSystemTrayAvailable():
        tr.show_window()
    if "--tabs-demo" in sys.argv:  # used by the screenshot smoke test: cycle through the tabs
        tabs = tr.window.findChild(QTabWidget)
        timer = QTimer()
        timer.timeout.connect(lambda: tabs.setCurrentIndex((tabs.currentIndex() + 1) % tabs.count()))
        timer.start(2000)
        app._wvt_demo_timer = timer
    return app.exec()
