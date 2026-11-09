"""Session statistics from the addon's state packets: XP and gold per hour, time to the next level.

Only active time counts: a gap of more than 5 minutes between two state packets (game closed,
app restarted, a long loading screen) is left out. XP gained across a level-up adds the rest of the
old bar and the start of the new one. Kept in memory only (one session = one app run, or until
"zresetuj statystyki").
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Optional

MAX_GAP_S = 300.0
MIN_ACTIVE_S = 120.0  # below this a rate is noise


@dataclass
class Summary:
    active_s: float
    xp_gained: int
    money_delta: int  # copper
    levels: int
    xp_per_h: Optional[float]
    money_per_h: Optional[float]
    eta_s: Optional[float]  # time to the next level at the current pace


def money_text(copper: int) -> str:
    sign = "minus " if copper < 0 else ""
    c = abs(int(copper))
    gold, rest = divmod(c, 10000)
    silver, cop = divmod(rest, 100)
    parts = []
    if gold:
        parts.append(f"{gold} złota")
    if silver:
        parts.append(f"{silver} srebra")
    if cop and not gold:
        parts.append(f"{cop} miedzi")
    return sign + (" ".join(parts) if parts else "nic")


def duration_text(seconds: float) -> str:
    m = int(round(seconds / 60))
    h, m = divmod(m, 60)
    if h and m:
        return f"{h} godz. {m} min"
    if h:
        return f"{h} godz."
    return f"{max(m, 1)} min"


def big(n: float) -> str:
    return f"{int(round(n)):,}".replace(",", " ")


class SessionStats:
    def __init__(self, clock=time.time):
        self.clock = clock
        self._lock = threading.Lock()
        self.reset()

    def reset(self) -> None:
        with getattr(self, "_lock", threading.Lock()):
            self.started: Optional[float] = None
            self.active_s = 0.0
            self.xp_gained = 0
            self.levels = 0
            self.start_money: Optional[int] = None
            self.money = 0
            self._last: Optional[tuple[float, int, int, int]] = None  # (t, level, xp, xp_max)

    def on_state(self, st, now: Optional[float] = None) -> None:
        if st is None or st.level <= 0:
            return
        now = self.clock() if now is None else now
        money = st.gold * 10000 + st.silver_copper
        with self._lock:
            if self._last is None:
                self.started = now
                self.start_money = money
            else:
                t0, lvl0, xp0, max0 = self._last
                dt = now - t0
                if 0 < dt <= MAX_GAP_S:
                    self.active_s += dt
                if st.level == lvl0 and st.xp >= xp0:
                    self.xp_gained += st.xp - xp0
                elif st.level > lvl0:
                    self.levels += st.level - lvl0
                    self.xp_gained += max(0, max0 - xp0) + st.xp
            self.money = money
            self._last = (now, st.level, st.xp, st.xp_max)

    def summary(self, st=None) -> Summary:
        with self._lock:
            active = self.active_s
            xp, levels = self.xp_gained, self.levels
            delta = self.money - (self.start_money or self.money)
        hours = active / 3600.0
        xp_h = xp / hours if active >= MIN_ACTIVE_S else None
        money_h = delta / hours if active >= MIN_ACTIVE_S else None
        eta = None
        if st is not None and xp_h and st.xp_max > 0:
            eta = max(0, st.xp_max - st.xp) / xp_h * 3600
        return Summary(active, xp, delta, levels, xp_h, money_h, eta)

    def polish(self, st=None) -> str:
        s = self.summary(st)
        if s.active_s < MIN_ACTIVE_S:
            return "Sesja dopiero się zaczęła, daj mi parę minut, to policzę tempo."
        out = [f"Gramy {duration_text(s.active_s)}."]
        if s.xp_gained:
            out.append(f"Zdobyłeś {big(s.xp_gained)} doświadczenia, około {big(s.xp_per_h)} na godzinę"
                       + (f", i wbiłeś {s.levels} {'poziom' if s.levels == 1 else 'poziomy' if s.levels < 5 else 'poziomów'}"
                          if s.levels else "") + ".")
        out.append(f"Na plusie {money_text(s.money_delta)}, czyli {money_text(int(s.money_per_h))} na godzinę."
                   if s.money_delta > 0 else
                   ("Złota bez zmian." if s.money_delta == 0 else f"Wydałeś {money_text(-s.money_delta)}."))
        if s.eta_s is not None:
            out.append(f"W tym tempie następny poziom za {duration_text(s.eta_s)}.")
        return " ".join(out)

    def eta_text(self, st) -> str:
        s = self.summary(st)
        return f" W tym tempie wbijesz go za {duration_text(s.eta_s)}." if s.eta_s is not None else ""
