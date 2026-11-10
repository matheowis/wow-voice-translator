"""What is in your bags: the addon's BAG lines (TYPE_CHAT of kind BAG) put back together.

The addon sends the whole content when it changes (at most every 3 s) and again every 5 minutes, in parts:
the race field says ``"sync:part/parts"`` and every text line is one item,
``itemID<TAB>count<TAB>quality<TAB>classID<TAB>subclassID<TAB>equipLoc index<TAB>name``.  A new list replaces
the old one only when all of its parts have arrived.
"""

from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass
from typing import Optional

from . import protocol

_HEAD_RE = re.compile(r"^(\d+):(\d+)/(\d+)$")


@dataclass
class BagItem:
    item_id: int
    count: int
    quality: int  # 0 grey ... 5 legendary
    class_id: int  # 0 consumable, 2 weapon, 4 armor, 7 trade goods, 12 quest...
    subclass_id: int
    equip_loc: str  # "INVTYPE_2HWEAPON"..., "" = not worn
    name: str

    @property
    def junk(self) -> bool:
        return self.quality == 0


def parse_line(line: str) -> Optional[BagItem]:
    parts = line.split("\t")
    if len(parts) < 7:
        return None
    try:
        iid, count, quality, cls, sub, loc = (int(x) for x in parts[:6])
    except ValueError:
        return None
    name = "\t".join(parts[6:]).strip()
    if not name:
        return None
    loc_name = protocol.EQUIP_LOCS[loc] if 0 <= loc < len(protocol.EQUIP_LOCS) else ""
    return BagItem(iid, max(1, count), quality, cls, sub, loc_name, name)


class BagBook:
    def __init__(self):
        self._lock = threading.Lock()
        self._sync: Optional[int] = None
        self._parts: dict[int, list[BagItem]] = {}
        self._items: list[BagItem] = []
        self.updated_at = 0.0

    def on_packet(self, pkt: protocol.Packet) -> bool:
        """True when the packet was a part of the bag contents (it is then not a chat line)."""
        if pkt.type not in (protocol.TYPE_CHAT, protocol.TYPE_VOICE) or pkt.chat_kind != protocol.KIND_BAG:
            return False
        m = _HEAD_RE.match((pkt.race or "").strip())
        if not m:
            return True
        sync, part, parts = (int(x) for x in m.groups())
        items = [it for it in (parse_line(ln) for ln in (pkt.text or "").split("\n") if ln.strip()) if it]
        with self._lock:
            if sync != self._sync:
                self._sync, self._parts = sync, {}
            self._parts[part] = items
            if len(self._parts) >= parts:
                self._items = [it for p in sorted(self._parts) for it in self._parts[p]]
                self.updated_at = time.time()
        return True

    @property
    def known(self) -> bool:
        return self.updated_at > 0

    @property
    def items(self) -> list[BagItem]:
        with self._lock:
            return list(self._items)

    def count(self, name: str) -> int:
        key = name.strip().lower()
        return sum(it.count for it in self.items if it.name.lower() == key)

    def junk(self) -> list[BagItem]:
        return [it for it in self.items if it.junk]
