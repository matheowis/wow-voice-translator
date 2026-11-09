"""The player's quest log, as the addon reports it, and where its objectives are.

* The addon sends one TYPE_QUEST packet per quest.  ``sync`` changes when the set of quests
  changes (a quest accepted, abandoned or turned in) and the addon then sends every quest again.
  Within one sync it re-sends only quests whose objectives, state or map marker changed, plus
  everything once a minute, so an app started mid-game catches up within a minute.
* The quest list = the quests of the newest sync, plus quests of older syncs that were not
  re-sent yet - until the newest sync has all ``total`` quests (then the old ones are gone).
* Where: the player's position (TYPE_POS, sent while moving) against the game's own map marker
  of the quest's next objective, on the same map, in yards (the map's world size), as one of
  eight Polish compass directions.  A marker on another map gives that map's name instead.
"""

from __future__ import annotations

import math
import re
import threading
import time
from dataclasses import dataclass
from typing import Optional

from . import protocol

DIRECTIONS = ("na północ", "na północny wschód", "na wschód", "na południowy wschód", "na południe",
              "na południowy zachód", "na zachód", "na północny zachód")


@dataclass
class Where:
    distance_yd: Optional[float]  # None when only the zone is known
    direction: str  # "" when unknown
    other_zone: str = ""  # the marker is on this other map

    def polish(self) -> str:
        if self.other_zone:
            return f"cel jest w innej strefie: {self.other_zone}"
        if self.distance_yd is None:
            return "nie znam odległości"
        if self.distance_yd < 15:
            return "jesteś praktycznie na miejscu"
        d = int(round(self.distance_yd / (5 if self.distance_yd < 100 else 10)) * (5 if self.distance_yd < 100 else 10))
        return f"około {d} jardów" + (f" {self.direction}" if self.direction else "")


def compass(dx: float, dy: float) -> str:
    """Map offset in yards (x east, y SOUTH - map coordinates grow downwards) -> Polish direction."""
    angle = (math.degrees(math.atan2(dx, -dy)) + 360.0) % 360.0  # 0 = north, clockwise
    return DIRECTIONS[int((angle + 22.5) // 45) % 8]


_OBJECTIVE_RE = (re.compile(r"^\s*(\d+)\s*/\s*(\d+)\s+(.+)$"), re.compile(r"^\s*(.+?):\s*(\d+)\s*/\s*(\d+)\s*$"))


def spoken_objective(text: str, done: bool = False) -> str:
    """"3/8 Kobold Candle" -> "Kobold Candle 3 z 8" (a TTS voice reads "3/8" as a fraction)."""
    m = _OBJECTIVE_RE[0].match(text)
    if m:
        out = f"{m.group(3)} {m.group(1)} z {m.group(2)}"
    else:
        m = _OBJECTIVE_RE[1].match(text)
        out = f"{m.group(1)} {m.group(2)} z {m.group(3)}" if m else text.strip()
    return out + (" (zrobione)" if done and not m else "")


class QuestBook:
    def __init__(self):
        self._lock = threading.Lock()
        self._quests: dict[int, protocol.QuestEntry] = {}
        self.sync: Optional[int] = None
        self.total = 0
        self.updated_at = 0.0
        self.pos: Optional[protocol.PlayerPos] = None
        self.pos_at = 0.0

    def on_packet(self, pkt: protocol.Packet) -> None:
        now = time.time()
        if pkt.type == protocol.TYPE_POS and pkt.pos is not None:
            with self._lock:
                self.pos, self.pos_at = pkt.pos, now
            return
        if pkt.type != protocol.TYPE_QUEST or pkt.quest is None:
            return
        q = pkt.quest
        with self._lock:
            self.sync, self.total, self.updated_at = q.sync, q.total, now
            if q.total == 0:  # the quest log is empty
                self._quests.clear()
                return
            self._quests[q.quest_id] = q
            current = [x for x in self._quests.values() if x.sync == q.sync]
            if len(current) >= q.total:  # the new list is complete: forget the old one
                self._quests = {x.quest_id: x for x in current}

    @property
    def known(self) -> bool:
        return self.updated_at > 0

    def quests(self) -> list[protocol.QuestEntry]:
        with self._lock:
            items = list(self._quests.values())
            sync, total = self.sync, self.total
        current = [q for q in items if q.sync == sync]
        if len(current) < total:  # still arriving: keep what was not re-sent yet
            have = {q.quest_id for q in current}
            current += [q for q in items if q.sync != sync and q.quest_id not in have]
        return sorted(current, key=lambda q: (q.index, q.quest_id))

    def where(self, q: protocol.QuestEntry) -> Optional[Where]:
        if not q.has_poi:
            return None
        pos = self.pos
        if q.flags & protocol.QFLAG_POI_OTHER_MAP or (pos is not None and pos.map_id != q.poi_map):
            return Where(None, "", q.zone or "nieznana")
        if pos is None or not pos.width or not pos.height:
            return Where(float(q.distance_yd) if q.distance_yd is not None else None, "")
        dx = (q.poi_x - pos.x) * pos.width
        dy = (q.poi_y - pos.y) * pos.height
        return Where(math.hypot(dx, dy), compass(dx, dy))

    def nearest(self, quests: Optional[list[protocol.QuestEntry]] = None,
                complete: Optional[bool] = None) -> Optional[tuple[protocol.QuestEntry, Where]]:
        best = None
        for q in quests if quests is not None else self.quests():
            if complete is not None and q.complete != complete:
                continue
            w = self.where(q)
            if w is None or w.distance_yd is None:
                continue
            if best is None or w.distance_yd < best[1].distance_yd:
                best = (q, w)
        return best


# --------------------------------------------------------------------------- which quest is meant

_STOP = {"the", "a", "an", "of", "to", "in", "on", "for", "my", "i", "do", "how", "many", "much", "where", "what",
         "is", "are", "left", "have", "has", "still", "quest", "quests", "need", "go", "get", "can", "should", "which",
         "it", "this", "that", "there", "me", "you", "with", "and", "or", "from", "at", "by", "find", "be", "was",
         "ile", "gdzie", "jak", "co", "mam", "mi", "jest", "sa", "ten", "ta", "te", "tego", "quest", "questa", "questu"}


def _stems(text: str) -> set[str]:
    out = set()
    for w in re.findall(r"[a-ząćęłńóśźż]+", (text or "").lower()):
        if w in _STOP or len(w) < 3:
            continue
        if len(w) > 4 and w.endswith("ies"):  # bodies -> body
            w = w[:-3] + "y"
        elif len(w) > 4 and re.search(r"(s|x|ch|sh)es$", w):  # boxes -> box
            w = w[:-2]
        elif len(w) > 3 and w.endswith("s") and not w.endswith("ss"):  # candles -> candle
            w = w[:-1]
        out.add(w)
    return out


def match_quests(question: str, quests: list[protocol.QuestEntry]) -> list[tuple[int, protocol.QuestEntry]]:
    """Quests sharing words with the question (English translation + the Polish original), best first."""
    asked = _stems(question)
    scored = []
    for q in quests:
        words = _stems(" ".join([q.title, q.zone] + [t for _, t in q.objectives]))
        score = len(asked & words) * 2 + sum(1 for a in asked for w in words if len(a) >= 5 and a != w
                                             and (a.startswith(w) or w.startswith(a)))
        if score:
            scored.append((score, q))
    scored.sort(key=lambda s: -s[0])
    return scored
