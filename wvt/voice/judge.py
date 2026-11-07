"""Automatic checks of Voice Design previews, learned from the user's ratings.

* **perceived gender** - the centre of the voices the user rated 4-5, men vs women, in speaker
  embedding space.  On round 2 this alone flagged every man the user called "baba",
  "dziewczyna?" or a child, and the gnome woman called "facet".  Men of the heavy races
  (orc, tauren, dwarf, undead) must sit clearly on the male side.
* **intelligibility** - Parakeet transcribes the preview line; a garbled or mumbled voice
  shows up as a high word error rate.
* **race fit** (soft) - closer to the user's good voices of the same race and gender than to
  the good voices of other races ("to nie ork" voices were closer to other races).
* **distinctness** - the largest similarity to the voices already in the pool.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from .similarity import cosine

# maximum "female-ness" score allowed for men (score: + female centre, - male centre) ...
MALE_MAX = {"Orc": -0.2, "Tauren": -0.2, "Dwarf": -0.15, "Undead": -0.15}
MALE_MAX_DEFAULT = 0.25
# ... and minimum for women
FEMALE_MIN = {"Gnome": 0.2}
FEMALE_MIN_DEFAULT = 0.1
MAX_WER = 0.35


@dataclass
class Verdict:
    ok: bool
    score: float  # lower is better
    reasons: list[str] = field(default_factory=list)
    details: dict = field(default_factory=dict)


class VoiceJudge:
    def __init__(self, references: list[tuple[str, str, np.ndarray]], stt=None):
        """references: (race, gender, embedding) of voices the user rated well."""
        self.refs = references
        men = [e for _, g, e in references if g == "Male"]
        women = [e for _, g, e in references if g == "Female"]
        self.male_c = np.mean(men, axis=0) if men else None
        self.female_c = np.mean(women, axis=0) if women else None
        self.stt = stt
        self._stt_lock = threading.Lock()  # one transcription at a time (pools are designed in parallel)

    def gender_score(self, emb: np.ndarray) -> Optional[float]:
        """+1 at the centre of the good women, -1 at the centre of the good men."""
        if self.male_c is None or self.female_c is None:
            return None
        w = self.female_c - self.male_c
        mid = (self.female_c + self.male_c) / 2
        return float((emb - mid) @ w / (np.linalg.norm(w) ** 2) * 2)

    def race_margin(self, emb: np.ndarray, race: str, gender: str) -> Optional[float]:
        own = [e for r, g, e in self.refs if r == race and g == gender]
        other = [e for r, g, e in self.refs if r != race and g == gender]
        if len(own) < 2 or not other:
            return None
        return cosine(emb, np.mean(own, axis=0)) - cosine(emb, np.mean(other, axis=0))

    def intelligibility(self, audio16k: np.ndarray, line: str) -> Optional[float]:
        if self.stt is None:
            return None
        from ..cli import wer

        try:
            with self._stt_lock:
                text = self.stt.transcribe(audio16k)
            return float(wer(line, text))
        except Exception:
            return None

    def assess(self, audio16k: np.ndarray, emb: np.ndarray, race: str, gender: str, line: str,
               pool: list[np.ndarray], others: list[np.ndarray]) -> Verdict:
        reasons, details = [], {}
        penalty = 0.0
        g = self.gender_score(emb)
        if g is not None:
            details["gender"] = round(g, 2)
            if gender == "Male" and g > MALE_MAX.get(race, MALE_MAX_DEFAULT):
                reasons.append(f"brzmi zbyt kobieco/dziecinnie ({g:+.2f})")
                penalty += 1.0 + (g - MALE_MAX.get(race, MALE_MAX_DEFAULT))
            if gender == "Female" and g < FEMALE_MIN.get(race, FEMALE_MIN_DEFAULT):
                reasons.append(f"brzmi zbyt męsko ({g:+.2f})")
                penalty += 1.0 + (FEMALE_MIN.get(race, FEMALE_MIN_DEFAULT) - g)
        e = self.intelligibility(audio16k, line)
        if e is not None:
            details["wer"] = round(e, 2)
            if e > MAX_WER:
                reasons.append(f"niewyraźny (WER {e:.2f})")
                penalty += 1.0 + e
        same = max((cosine(emb, v) for v in pool), default=0.0)
        other = max((cosine(emb, v) for v in others), default=0.0)
        details["pool"] = round(same, 2)
        distinct = max(same, 0.5 * other)
        margin = self.race_margin(emb, race, gender)
        if margin is not None:
            details["race"] = round(margin, 2)
        score = distinct - 0.4 * (margin or 0.0) + penalty
        return Verdict(ok=not reasons, score=score, reasons=reasons, details=details)
