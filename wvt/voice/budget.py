"""Daily cap on characters sent to ElevenLabs. Only counts are stored, never the text."""

from __future__ import annotations

import json
import os
import threading
from datetime import date
from pathlib import Path
from typing import Callable, Optional


class DailyBudget:
    def __init__(self, path: Path, daily_chars: int, today: Callable[[], date] = date.today):
        self.path = Path(path)
        self.limit = int(daily_chars)
        self._today = today
        self._lock = threading.Lock()
        try:
            self._data: dict = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}
        except (OSError, ValueError):
            self._data = {}

    def _day(self) -> dict:
        return self._data.setdefault(self._today().isoformat(), {"chars": 0, "credits": 0.0, "lines": 0})

    @property
    def used_chars(self) -> int:
        with self._lock:
            return int(self._day()["chars"])

    @property
    def used_credits(self) -> float:
        with self._lock:
            return float(self._day()["credits"])

    def allows(self, chars: int) -> bool:
        if self.limit <= 0:
            return True
        with self._lock:
            return self._day()["chars"] + chars <= self.limit

    def add(self, chars: int, credits: Optional[float] = None) -> None:
        with self._lock:
            day = self._day()
            day["chars"] += int(chars)
            day["credits"] += float(credits if credits is not None else chars)
            day["lines"] += 1
            keep = sorted(self._data)[-14:]
            self._data = {k: self._data[k] for k in keep}
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._data, indent=1), encoding="utf-8")
            os.replace(tmp, self.path)
