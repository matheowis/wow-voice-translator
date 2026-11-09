"""The "Głosy" tab of the main window: status, volumes, budget, test playback, recent lines."""

from __future__ import annotations

import threading
import time
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from .. import protocol
from .races import GENDERS, POOL_RACES

if TYPE_CHECKING:  # pragma: no cover
    from ..app import TranslatorApp

UNKNOWN_POLICIES = [("play_quiet", "cicho"), ("play_normal", "normalnie"), ("mute", "pomiń")]
NORMALIZER_MODES = [("hybrid", "reguły + lokalny LLM"), ("rules", "tylko reguły"), ("off", "bez zmian")]
SKIP_REASONS = {
    "out-of-range": "za daleko",
    "budget": "limit dzienny",
    "duplicate": "powtórka",
    "own": "własna wiadomość",
    "blocked": "zablokowany gracz",
    "noise": "komenda/znaki",
    "empty": "pusta/ukryta",
    "stale": "za stara",
    "queue-full": "kolejka pełna (bliżsi mają pierwszeństwo)",
    "crowd": "tłok – czytam tylko najbliższych",
    "flood": "zalew wiadomości od tego gracza",
    "no-voices": "brak głosów (Voice Design)",
    "silenced": "przerwane",
}


def describe_event(ev) -> str:
    sex = {2: "M", 3: "K"}.get(ev.sex, "?")
    dist = "" if ev.distance is None else f" {ev.distance:.0f} yd"
    who = f"{ev.sender or '?'} ({ev.race or 'rasa ?'} {sex}{dist})"
    stamp = time.strftime("%H:%M:%S", time.localtime(ev.when))
    if ev.status == "spoken":
        cost = f", {ev.cost:.0f} kr." if ev.cost is not None else ""
        extra = f" – „{ev.text}”" if ev.text else ""
        return f"{stamp} {ev.channel} {who} → mówi {ev.lang.upper()} ({ev.ttfa_ms:.0f} ms{cost}){extra}"
    if ev.status.startswith("skipped:"):
        reason = ev.status.split(":", 1)[1]
        return f"{stamp} {ev.channel} {who} → pominięte: {SKIP_REASONS.get(reason, reason)}"
    return f"{stamp} {ev.channel} {who} → błąd: {ev.note}"


class VoiceTab(QWidget):
    def __init__(self, app: "TranslatorApp"):
        super().__init__()
        self.app = app
        v = app.cfg["voice"]
        lay = QVBoxLayout(self)

        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        f = QFont("Consolas")
        f.setPointSize(9)
        self.status.setFont(f)
        lay.addWidget(self.status)

        hint = QLabel("Które kanały czytać, wybierasz w grze: panel <b>Say / Party / Glos EN-PL</b> nad czatem "
                      "albo <code>/wvt say</code>, <code>/wvt party</code>, <code>/wvt lang pl</code>. "
                      "Wyłączone kanały nic nie kosztują.")
        hint.setWordWrap(True)
        lay.addWidget(hint)

        box = QGroupBox("Ustawienia")
        form = QFormLayout(box)
        self.enabled = QCheckBox("czytaj wiadomości na głos (wyłącznik po stronie aplikacji)")
        self.enabled.setChecked(bool(v.get("enabled", True)))
        form.addRow(self.enabled)
        self.vol_say = self._slider(v.get("volume_say", 1.0))
        form.addRow("Głośność /say:", self.vol_say)
        self.vol_party = self._slider(v.get("volume_party", 0.9))
        form.addRow("Głośność /party:", self.vol_party)
        self.unknown = QComboBox()
        for key, label in UNKNOWN_POLICIES:
            self.unknown.addItem(label, key)
        self.unknown.setCurrentIndex(max(0, self.unknown.findData(v.get("unknown_distance", "play_quiet"))))
        form.addRow("/say, gdy odległość nieznana:", self.unknown)
        self.norm_mode = QComboBox()
        for key, label in NORMALIZER_MODES:
            self.norm_mode.addItem(label, key)
        self.norm_mode.setCurrentIndex(max(0, self.norm_mode.findData(app.cfg.get("normalizer.mode", "hybrid"))))
        form.addRow("Przygotowanie tekstu (EN):", self.norm_mode)
        self.budget = QSpinBox()
        self.budget.setRange(0, 5_000_000)
        self.budget.setSingleStep(5000)
        self.budget.setValue(int(v.get("daily_char_budget", 30000)))
        self.budget.setSuffix(" znaków/dzień (0 = bez limitu)")
        form.addRow("Limit ElevenLabs:", self.budget)
        self.own = QCheckBox("w trybie EN moja postać powtarza moje wiadomości swoim głosem")
        self.own.setChecked(bool(v.get("speak_own_messages", True)))
        form.addRow(self.own)
        row = QHBoxLayout()
        b_save = QPushButton("Zapisz")
        b_save.clicked.connect(self.on_save)
        b_stop = QPushButton("Przerwij odczyt")
        b_stop.clicked.connect(lambda: self.app.voice and self.app.voice.silence())
        b_reload = QPushButton("Odśwież listę głosów")
        b_reload.clicked.connect(lambda: self.app.voice and self.app.voice.resolver.reload_profiles())
        row.addWidget(b_save)
        row.addWidget(b_stop)
        row.addWidget(b_reload)
        row.addStretch(1)
        form.addRow(row)
        lay.addWidget(box)

        test = QGroupBox("Próbka głosu")
        trow = QHBoxLayout(test)
        self.t_race = QComboBox()
        self.t_race.addItems(list(POOL_RACES) + ["Unknown"])  # Unknown: a sender without race
        self.t_gender = QComboBox()
        self.t_gender.addItems(list(GENDERS))
        self.t_lang = QComboBox()
        self.t_lang.addItems(["EN", "PL"])
        self.t_text = QLineEdit("Well met, friend! brb, need to repair my gear.")
        b_play = QPushButton("Odtwórz")
        b_play.clicked.connect(self.on_test)
        for wdg in (self.t_race, self.t_gender, self.t_lang):
            trow.addWidget(wdg)
        trow.addWidget(self.t_text, 1)
        trow.addWidget(b_play)
        lay.addWidget(test)

        lay.addWidget(QLabel("Ostatnie wiadomości:"))
        self.events = QListWidget()
        lay.addWidget(self.events, 1)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self.refresh)
        self._timer.start(1000)
        self._reload_at = 0.0

    @staticmethod
    def _slider(value: float) -> QSlider:
        s = QSlider(Qt.Orientation.Horizontal)
        s.setRange(0, 150)
        s.setValue(int(round(float(value) * 100)))
        return s

    def add_event(self, ev) -> None:
        self.events.insertItem(0, describe_event(ev))
        while self.events.count() > 50:
            self.events.takeItem(self.events.count() - 1)

    def refresh(self) -> None:
        if not self.isVisible():
            return
        a = self.app
        m = a.voice
        lines = [f"Głosy:     {a.voice_status}"]
        if m is not None:
            now = time.time()
            if now > self._reload_at:  # profiles may be added by "python -m wvt.voice.design"
                self._reload_at = now + 30
                threading.Thread(target=m.resolver.reload_profiles, daemon=True).start()
            last = a.reader.last_voice_at if a.reader else 0
            lines += [
                f"Model:     {m.tts.model_id} ({m.tts.output_format}), głosów w pulach: {m.resolver.profile_count}",
                f"Dziś:      {m.budget.used_chars} znaków / {m.budget.limit or '∞'}, ok. {m.budget.used_credits:.0f} kredytów",
                f"Opóźnienie: {m.latency_summary()}",
                f"Odebrane: {m.stats['received']}, przeczytane {m.stats['spoken']}, błędy {m.stats['errors']}, "
                f"ostatni pakiet {time.strftime('%H:%M:%S', time.localtime(last)) if last else '—'}",
            ]
        self.status.setText("\n".join(lines))

    def on_save(self) -> None:
        cfg = self.app.cfg
        cfg.set("voice.enabled", self.enabled.isChecked())
        cfg.set("voice.volume_say", self.vol_say.value() / 100)
        cfg.set("voice.volume_party", self.vol_party.value() / 100)
        cfg.set("voice.unknown_distance", self.unknown.currentData())
        cfg.set("voice.daily_char_budget", self.budget.value())
        cfg.set("voice.speak_own_messages", self.own.isChecked())
        cfg.set("normalizer.mode", self.norm_mode.currentData())
        cfg.save()
        m = self.app.voice
        if m is not None:
            m.enabled = self.enabled.isChecked()
            m.budget.limit = self.budget.value()
            m.normalizer.mode = self.norm_mode.currentData()
        self.app.act_voice.setChecked(self.enabled.isChecked())

    def on_test(self) -> None:
        m = self.app.voice
        if m is None:
            self.add_event_text("Głosy nie są gotowe: " + self.app.voice_status)
            return
        race, gender = self.t_race.currentText(), self.t_gender.currentText()
        pkt = protocol.Packet(type=protocol.TYPE_VOICE, seq=0, chat_kind=3,  # party: full volume, no distance
                              sex={"Male": 2, "Female": 3}[gender], race="Scourge" if race == "Undead" else
                              ("" if race == "Unknown" else race), sender=f"Probka{race}{gender}-Test",
                              text=self.t_text.text().strip() or "Well met!",
                              flags=protocol.VFLAG_LANG_PL if self.t_lang.currentText() == "PL" else 0)
        if not m.budget.allows(len(pkt.text)):
            self.add_event_text("Próbka pominięta: dzienny limit znaków")
            return
        m.enqueue_direct(pkt, gain=float(self.app.cfg.get("voice.volume_party", 0.9)))

    def add_event_text(self, text: str) -> None:
        self.events.insertItem(0, time.strftime("%H:%M:%S ") + text)
