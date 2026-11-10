"""Structured game data for WoW Forever (from QuestieDB): quests, NPCs, objects, items, zones.

:func:`build_world` turns the parsed QuestieDB tables into a compact dict (``data/kb/world.json.gz``,
written by ``python -m wvt.kb.build``); :class:`World` loads it in the app and answers the
questions the advisor needs: who starts a quest and where, what is available at my level, where is
the nearest trainer, which map (uiMapID) and coordinates to put a waypoint on.

Coordinates are QuestieDB's: percent (0-100) of the zone map of an AreaTable id; ``map_of(area)``
gives the uiMapID for ``C_Map.SetUserWaypoint``. Distances are known only when the target is on the
player's current map (the app gets the player's map, position and map size in yards).
"""

from __future__ import annotations

import gzip
import json
import math
import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional

from .questie_tables import CLASS_KEYS, NPC_FLAGS, RACE_KEYS

ALLIANCE_MASK = RACE_KEYS["ALL_ALLIANCE"]
HORDE_MASK = RACE_KEYS["ALL_HORDE"]
# WoW race ids (UnitRace) -> QuestieDB requiredRaces bit (Forever: Skyborne 95/96 use bits 32/33)
RACE_ID_MASK = {1: 1, 2: 2, 3: 4, 4: 8, 5: 16, 6: 32, 7: 64, 8: 128, 9: 256, 95: 1 << 32, 96: 1 << 33}
SERVICE_FLAGS = (NPC_FLAGS["FLIGHT_MASTER"] | NPC_FLAGS["TRAINER"] | NPC_FLAGS["INNKEEPER"] | NPC_FLAGS["BANKER"]
                 | NPC_FLAGS["AUCTIONEER"] | NPC_FLAGS["STABLEMASTER"] | NPC_FLAGS["REPAIR"]
                 | NPC_FLAGS["SPIRIT_HEALER"] | NPC_FLAGS["VENDOR"])
SPECIAL_REPEATABLE, SPECIAL_EVENT = 1, 2
MAX_SPAWNS_PER_ZONE = 30
JUNK_NAME = re.compile(r"(<\s*(NYI|TXT|UNUSED|TEST|PH|DEPRECATED)|\[(DEPRECATED|UNUSED|PH|TEST)|zzOLD|\bTEST\b|"
                       r"^\s*<|REUSE|Deprecated|\(old\)|UNUSED)", re.I)

# zone enum names whose plain title-casing is wrong
_NAME_FIXES = {
    "Un Goro Crater": "Un'Goro Crater", "Zul Farrak": "Zul'Farrak", "Zul Gurub": "Zul'Gurub",
    "Shen Dralas": "Shen'dralas", "Onyxias Lair": "Onyxia's Lair", "Ahn Qiraj": "Ahn'Qiraj",
    "Atal Hakkar": "Atal'Hakkar", "Krol Dok": "Krol'dok", "Shen Dar": "Shen'dar", "Grom Gol": "Grom'gol",
    "Mak Aru": "Mak'aru", "Thalassian": "Thalassian", "Dun Morogh": "Dun Morogh", "Rut Theran": "Rut'theran",
    "Kel Theril": "Kel'Theril", "Quel Thalas": "Quel'Thalas", "Sen Jin": "Sen'jin", "Zoram Gar": "Zoram'gar",
    "Hellfire": "Hellfire", "Uldaman": "Uldaman", "Darkspear": "Darkspear", "Ironforge": "Ironforge",
}
_SMALL = {"Of", "The", "And", "In", "On", "To", "A"}


def pretty_zone(enum_name: str) -> str:
    words = [w.capitalize() for w in enum_name.lower().split("_") if w]
    words = [w if i == 0 or w not in _SMALL else w.lower() for i, w in enumerate(words)]
    s = " ".join(words)
    for a, b in _NAME_FIXES.items():
        s = s.replace(a, b)
    return s


def fold(s: str) -> str:
    s = unicodedata.normalize("NFKD", s.casefold())
    return "".join(c for c in s if not unicodedata.combining(c)).replace("ł", "l")


def faction_of_mask(mask: int) -> str:
    """'A', 'H' or '' (both / unknown) for a requiredRaces mask."""
    if not mask:
        return ""
    a, h = bool(mask & ALLIANCE_MASK), bool(mask & HORDE_MASK)
    return "A" if a and not h else "H" if h and not a else ""


# --------------------------------------------------------------------------- build (offline)

def _ids(t: Any) -> list[int]:
    if isinstance(t, dict):
        return [int(v) for _, v in sorted(t.items(), key=lambda kv: str(kv[0])) if isinstance(v, (int, float))]
    if isinstance(t, (int, float)):
        return [int(t)]
    return []


def _strings(t: Any) -> list[str]:
    if isinstance(t, dict):
        return [v for _, v in sorted(t.items(), key=lambda kv: str(kv[0])) if isinstance(v, str) and v.strip()]
    return [t] if isinstance(t, str) and t.strip() else []


def _spawns(t: Any) -> dict[str, list[list[float]]]:
    out: dict[str, list[list[float]]] = {}
    if not isinstance(t, dict):
        return out
    for zone, coords in t.items():
        if not isinstance(zone, int) or not isinstance(coords, dict):
            continue
        pts = []
        for _, c in sorted(coords.items(), key=lambda kv: str(kv[0])):
            if isinstance(c, dict) and isinstance(c.get(1), (int, float)) and isinstance(c.get(2), (int, float)):
                x, y = float(c[1]), float(c[2])
                if 0 <= x <= 100 and 0 <= y <= 100:
                    pts.append([round(x, 1), round(y, 1)])
        if pts:
            if len(pts) > MAX_SPAWNS_PER_ZONE:  # keep an even sample of a big spawn cloud
                step = len(pts) / MAX_SPAWNS_PER_ZONE
                pts = [pts[int(i * step)] for i in range(MAX_SPAWNS_PER_ZONE)]
            out[str(zone)] = pts
    return out


def _objective_list(slot: Any) -> list[list]:
    out = []
    if isinstance(slot, dict):
        for _, o in sorted(slot.items(), key=lambda kv: str(kv[0])):
            if isinstance(o, dict) and isinstance(o.get(1), (int, float)):
                text = o.get(2) if isinstance(o.get(2), str) else None
                out.append([int(o[1]), text])
    return out


def build_world(tables: dict, support: dict, blacklist: Iterable[int] = (), event_quests: Iterable[int] = (),
                source: str = "") -> dict:
    K, D = tables["keys"], tables["data"]
    qk, nk, ok, ik = K["Quest"], K["Npc"], K["Object"], K["Item"]
    black, events = set(blacklist), set(event_quests)

    def g(rec: dict, keys: dict, name: str, default=None):
        return rec.get(keys.get(name, -1), default)

    quests: dict[str, dict] = {}
    need_npc: set[int] = set()
    need_obj: set[int] = set()
    need_item: set[int] = set()
    for qid, r in D["Quest"].items():
        name = g(r, qk, "name")
        if not isinstance(qid, int) or not isinstance(name, str) or not name.strip() or JUNK_NAME.search(name):
            continue
        if qid in black or qid in events:
            continue
        sb, fb, obj = g(r, qk, "startedBy") or {}, g(r, qk, "finishedBy") or {}, g(r, qk, "objectives") or {}
        q: dict[str, Any] = {"n": name.strip(), "lvl": int(g(r, qk, "questLevel") or 0),
                             "min": int(g(r, qk, "requiredLevel") or 0)}
        for key, field in (("races", "requiredRaces"), ("classes", "requiredClasses"), ("zone", "zoneOrSort"),
                           ("next", "nextQuestInChain"), ("sf", "specialFlags"), ("max", "requiredMaxLevel"),
                           ("parent", "parentQuest"), ("bc", "breadcrumbForQuestId"),
                           ("aw", "availableStartingWith"), ("until", "availableUntilCompleted"),
                           ("src", "sourceItemId")):
            v = g(r, qk, field)
            if isinstance(v, (int, float)) and v:
                q[key] = int(v)
        if isinstance(sb, dict):
            for key, slot in (("sn", 1), ("so", 2), ("si", 3)):
                ids = _ids(sb.get(slot))
                if ids:
                    q[key] = ids
        if isinstance(fb, dict):
            for key, slot in (("en", 1), ("eo", 2)):
                ids = _ids(fb.get(slot))
                if ids:
                    q[key] = ids
        for key, field in (("pg", "preQuestGroup"), ("ps", "preQuestSingle"), ("ex", "exclusiveTo"),
                           ("bcs", "breadcrumbs")):
            ids = _ids(g(r, qk, field))
            if ids:
                q[key] = ids
        txt = _strings(g(r, qk, "objectivesText"))
        if txt:
            q["txt"] = txt
        if isinstance(obj, dict):
            for key, slot in (("kill", 1), ("use", 2), ("loot", 3)):
                lst = _objective_list(obj.get(slot))
                if lst:
                    q[key] = lst
            credit = obj.get(5)
            if isinstance(credit, dict):
                lst = []
                for _, c in sorted(credit.items(), key=lambda kv: str(kv[0])):
                    if isinstance(c, dict):
                        base = c.get(2)
                        lst.append([_ids(c.get(1)), int(base) if isinstance(base, (int, float)) else None,
                                    c.get(3) if isinstance(c.get(3), str) else None])
                if lst:
                    q["credit"] = lst
        skill = g(r, qk, "requiredSkill")
        if isinstance(skill, dict) and skill.get(1):
            q["skill"] = [int(skill.get(1) or 0), int(skill.get(2) or 0)]
        xp = support.get("xp", {}).get(qid)
        if xp:
            q["xp"] = xp[1]
        quests[str(qid)] = q
        need_npc.update(q.get("sn", []) + q.get("en", []) + [k for k, _ in q.get("kill", [])])
        for ids, base, _ in q.get("credit", []):
            need_npc.update(ids[:10])
            if base:
                need_npc.add(base)
        need_obj.update(q.get("so", []) + q.get("eo", []) + [k for k, _ in q.get("use", [])])
        need_item.update([k for k, _ in q.get("loot", [])] + q.get("si", []) + ([q["src"]] if "src" in q else []))

    # rewards: items list the quests that give them (itemKeys.questRewards)
    reward_items: dict[int, list[int]] = defaultdict(list)
    for iid, r in D["Item"].items():
        for qid in _ids(g(r, ik, "questRewards")):
            if str(qid) in quests and isinstance(g(r, ik, "name"), str):
                reward_items[qid].append(iid)
    for qid, ids in reward_items.items():
        quests[str(qid)]["rw"] = sorted(set(ids))[:6]
        need_item.update(quests[str(qid)]["rw"])

    items: dict[str, dict] = {}
    for iid in need_item:
        r = D["Item"].get(iid)
        if not r or not isinstance(g(r, ik, "name"), str):
            continue
        it: dict[str, Any] = {"n": g(r, ik, "name")}
        for key, field in (("il", "itemLevel"), ("rl", "requiredLevel"), ("c", "class"), ("sc", "subClass")):
            v = g(r, ik, field)
            if isinstance(v, (int, float)):
                it[key] = int(v)
        nd, od = _ids(g(r, ik, "npcDrops"))[:15], _ids(g(r, ik, "objectDrops"))[:15]
        if nd:
            it["nd"] = nd
            need_npc.update(nd)
        if od:
            it["od"] = od
            need_obj.update(od)
        vendors = _ids(g(r, ik, "vendors"))[:8]
        if vendors:
            it["v"] = vendors
            need_npc.update(vendors)
        items[str(iid)] = it

    npcs: dict[str, dict] = {}
    for nid, r in D["Npc"].items():
        name = g(r, nk, "name")
        if not isinstance(nid, int) or not isinstance(name, str):
            continue
        flags = int(g(r, nk, "npcFlags") or 0)
        if nid not in need_npc and not (flags & SERVICE_FLAGS and flags & ~NPC_FLAGS["VENDOR"] & SERVICE_FLAGS):
            continue
        n: dict[str, Any] = {"n": name}
        sub = g(r, nk, "subName")
        if isinstance(sub, str) and sub:
            n["sub"] = sub
        lo, hi = g(r, nk, "minLevel"), g(r, nk, "maxLevel")
        if isinstance(lo, (int, float)):
            n["lv"] = [int(lo), int(hi if isinstance(hi, (int, float)) else lo)]
        rank = g(r, nk, "rank")
        if isinstance(rank, (int, float)) and rank:
            n["r"] = int(rank)
        zone = g(r, nk, "zoneID")
        if isinstance(zone, (int, float)) and zone:
            n["z"] = int(zone)
        sp = _spawns(g(r, nk, "spawns"))
        if sp:
            n["sp"] = sp
        fr = g(r, nk, "friendlyToFaction")
        if isinstance(fr, str) and fr:
            n["f"] = fr
        if flags:
            n["fl"] = flags
        npcs[str(nid)] = n

    objects: dict[str, dict] = {}
    for oid in need_obj:
        r = D["Object"].get(oid)
        if not r or not isinstance(g(r, ok, "name"), str):
            continue
        o: dict[str, Any] = {"n": g(r, ok, "name")}
        sp = _spawns(g(r, ok, "spawns"))
        if sp:
            o["sp"] = sp
        zone = g(r, ok, "zoneID")
        if isinstance(zone, (int, float)) and zone:
            o["z"] = int(zone)
        objects[str(oid)] = o

    zone_ids = tables.get("enums", {}).get("zoneIDs", {})
    names: dict[str, str] = {}
    for enum_name, area in sorted(zone_ids.items(), key=lambda kv: len(kv[0])):
        if isinstance(area, int) and str(area) not in names:
            names[str(area)] = pretty_zone(enum_name)
    dungeons: dict[str, dict] = {}
    for area, d in (support.get("dungeons") or {}).items():
        if isinstance(d, dict) and isinstance(d.get(1), str):
            entry: dict[str, Any] = {"n": d[1]}
            if isinstance(d.get(3), int):
                entry["parent"] = d[3]
            ents = d.get(4)
            if isinstance(ents, dict):
                for _, e in sorted(ents.items(), key=lambda kv: str(kv[0])):
                    if isinstance(e, dict) and all(isinstance(e.get(i), (int, float)) for i in (1, 2, 3)):
                        entry["at"] = [int(e[1]), float(e[2]), float(e[3])]
                        break
            dungeons[str(area)] = entry
            names.setdefault(str(area), d[1])

    world = {"version": 1, "source": source, "quests": quests, "npcs": npcs, "objects": objects, "items": items,
             "zones": {"names": names, "map": {str(a): m for a, m in support.get("area_to_map", {}).items()},
                       "dungeons": dungeons}}
    world["zones"].update(_zone_stats(world))
    return world


def _zone_stats(world: dict) -> dict:
    """Per zone: typical quest levels (20th-80th percentile) and the busiest quest giver per faction."""
    levels: dict[int, list[int]] = defaultdict(list)
    givers: dict[tuple[int, str], Counter] = defaultdict(Counter)
    npcs = world["npcs"]
    for q in world["quests"].values():
        zone = q.get("zone", 0)
        if zone <= 0 or q.get("sf", 0) & (SPECIAL_REPEATABLE | SPECIAL_EVENT) or q.get("classes") or q.get("skill"):
            continue
        if q["lvl"] > 0:
            levels[zone].append(q["lvl"])
        side = faction_of_mask(q.get("races", 0))
        for nid in q.get("sn", []):
            n = npcs.get(str(nid))
            if not n or str(zone) not in n.get("sp", {}):
                continue
            fr = n.get("f", "")
            for fac in ("A", "H"):
                if (not side or side == fac) and fac in fr:
                    givers[(zone, fac)][nid] += 1
    out_levels = {}
    for zone, lv in levels.items():
        if len(lv) < 3:
            continue
        lv.sort()
        lo, hi = lv[int(0.2 * (len(lv) - 1))], lv[int(0.8 * (len(lv) - 1))]
        out_levels[str(zone)] = [lo, hi, len(lv)]
    hubs: dict[str, dict] = defaultdict(dict)
    for (zone, fac), cnt in givers.items():
        nid, count = cnt.most_common(1)[0]
        hubs[str(zone)][fac] = [nid, count]
    return {"levels": out_levels, "hubs": dict(hubs)}


def save_world(world: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with gzip.open(tmp, "wt", encoding="utf-8") as fh:
        json.dump(world, fh, ensure_ascii=False, separators=(",", ":"))
    tmp.replace(path)


# --------------------------------------------------------------------------- runtime

@dataclass
class Spot:
    kind: str  # "npc" / "object"
    eid: int
    name: str
    area: int
    x: float  # percent of the zone map
    y: float
    ui_map: int = 0
    distance: Optional[float] = None  # yards, when on the player's map

    @property
    def waypoint(self) -> Optional[tuple[int, float, float]]:
        return (self.ui_map, self.x, self.y) if self.ui_map else None


@dataclass
class Player:
    level: int = 0
    race_id: int = 0
    class_id: int = 0
    faction: str = ""  # "A" / "H" / ""
    done: frozenset = frozenset()  # completed quest ids
    done_known: bool = False  # the addon sent the completed list

    @property
    def race_mask(self) -> int:
        return RACE_ID_MASK.get(self.race_id, 0)

    @property
    def class_mask(self) -> int:
        return 1 << (self.class_id - 1) if self.class_id > 0 else 0


class World:
    def __init__(self, data: dict):
        self.data = data
        self.quests: dict[int, dict] = {int(k): v for k, v in data.get("quests", {}).items()}
        self.npcs: dict[int, dict] = {int(k): v for k, v in data.get("npcs", {}).items()}
        self.objects: dict[int, dict] = {int(k): v for k, v in data.get("objects", {}).items()}
        self.items: dict[int, dict] = {int(k): v for k, v in data.get("items", {}).items()}
        z = data.get("zones", {})
        self.names: dict[int, str] = {int(k): v for k, v in z.get("names", {}).items()}
        self.area_map: dict[int, int] = {int(k): int(v) for k, v in z.get("map", {}).items() if v}
        self.map_areas: dict[int, list[int]] = defaultdict(list)
        for a, m in self.area_map.items():
            self.map_areas[m].append(a)
        self.dungeons: dict[int, dict] = {int(k): v for k, v in z.get("dungeons", {}).items()}
        self.levels: dict[int, list[int]] = {int(k): v for k, v in z.get("levels", {}).items()}
        self.hubs: dict[int, dict] = {int(k): v for k, v in z.get("hubs", {}).items()}
        self._npc_names: dict[str, list[int]] = defaultdict(list)
        for nid, n in self.npcs.items():
            self._npc_names[fold(n["n"])].append(nid)
        self._quest_names: dict[str, list[int]] = defaultdict(list)
        for qid, q in self.quests.items():
            self._quest_names[fold(q["n"])].append(qid)
        self._item_names: dict[str, list[int]] = defaultdict(list)
        for iid, it in self.items.items():
            self._item_names[fold(it["n"])].append(iid)

    @classmethod
    def load(cls, path: Path) -> "World":
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            return cls(json.load(fh))

    # ---- names and maps
    def map_of(self, area: int) -> int:
        return self.area_map.get(area, 0)

    def area_name(self, area: int) -> str:
        return self.names.get(area, "")

    def find_area(self, name: str) -> int:
        """AreaTable id of a zone by its name as the game shows it ("Elwynn Forest"), 0 if unknown."""
        if not hasattr(self, "_area_by_name"):
            self._area_by_name = {}
            for area, n in sorted(self.names.items()):
                self._area_by_name.setdefault(fold(n), area)
        return self._area_by_name.get(fold(name or ""), 0)

    def zone_of_map(self, ui_map: int) -> int:
        """The AreaTable id whose map is ``ui_map`` (the zone, not a sub-zone, when there are several)."""
        areas = self.map_areas.get(ui_map) or []
        if not areas:
            return 0
        with_levels = [a for a in areas if a in self.levels]
        return (with_levels or sorted(areas))[0]

    # ---- places
    def spots(self, kind: str, eid: int, pos=None) -> list[Spot]:
        table = self.npcs if kind == "npc" else self.objects
        e = table.get(eid)
        if not e:
            return []
        out = []
        for area_s, pts in e.get("sp", {}).items():
            area = int(area_s)
            ui_map = self.map_of(area)
            for x, y in pts:
                s = Spot(kind, eid, e["n"], area, x, y, ui_map)
                if pos is not None and ui_map and ui_map == getattr(pos, "map_id", None) and pos.width > 0:
                    s.distance = math.hypot((x / 100 - pos.x) * pos.width, (y / 100 - pos.y) * pos.height)
                out.append(s)
        return out

    def nearest(self, kind: str, eid: int, pos=None) -> Optional[Spot]:
        spots = self.spots(kind, eid, pos)
        if not spots:
            return None
        here = [s for s in spots if s.distance is not None]
        if here:
            return min(here, key=lambda s: s.distance)
        if pos is not None:  # same zone as the player, even without a distance
            same = [s for s in spots if s.ui_map and s.ui_map == getattr(pos, "map_id", None)]
            if same:
                return same[0]
        return spots[0]

    def starter_spot(self, qid: int, pos=None, faction: str = "") -> Optional[Spot]:
        q = self.quests.get(qid)
        if not q:
            return None
        best: Optional[Spot] = None
        for nid in q.get("sn", []):
            n = self.npcs.get(nid)
            if not n or (faction and n.get("f") and faction not in n["f"]):
                continue
            s = self.nearest("npc", nid, pos)
            if s and (best is None or (s.distance or 1e9) < (best.distance or 1e9)):
                best = s
        for oid in q.get("so", []):
            s = self.nearest("object", oid, pos)
            if s and (best is None or (s.distance or 1e9) < (best.distance or 1e9)):
                best = s
        return best

    def finisher_spot(self, qid: int, pos=None) -> Optional[Spot]:
        q = self.quests.get(qid)
        if not q:
            return None
        spots = [s for nid in q.get("en", []) if (s := self.nearest("npc", nid, pos))]
        spots += [s for oid in q.get("eo", []) if (s := self.nearest("object", oid, pos))]
        if not spots:
            return None
        return min(spots, key=lambda s: s.distance if s.distance is not None else 1e9)

    # ---- quest availability (QuestieDB semantics)
    def available(self, q: dict, qid: int, p: Player, active: set[int]) -> bool:
        if qid in p.done or qid in active:
            return False
        if q.get("sf", 0) & (SPECIAL_REPEATABLE | SPECIAL_EVENT) or q.get("skill"):
            return False
        if q.get("min", 0) > p.level or (q.get("max") and p.level > q["max"]):
            return False
        races = q.get("races", 0)
        if races and p.race_mask and not races & p.race_mask:
            return False
        if races and not p.race_mask and p.faction:
            side = faction_of_mask(races)
            if side and side != p.faction:
                return False
        classes = q.get("classes", 0)
        if classes and classes != CLASS_KEYS["ALL_CLASSES"] and (not p.class_mask or not classes & p.class_mask):
            return False
        if any(x not in p.done for x in q.get("pg", [])):
            return False
        ps = q.get("ps", [])
        if ps and not any(x in p.done for x in ps):
            return False
        if any(x in p.done or x in active for x in q.get("ex", [])):
            return False
        nxt = q.get("next")
        if nxt and (nxt in p.done or nxt in active):
            return False
        if q.get("aw") and q["aw"] not in active and q["aw"] not in p.done:
            return False
        if q.get("until") and q["until"] in p.done:
            return False
        if q.get("parent") and q["parent"] not in active:
            return False
        bc = q.get("bc")
        if bc and (bc in p.done or bc in active):
            return False
        return True

    def find_npcs(self, query: str, faction: str = "") -> list[int]:
        """NPC ids by name: exact, then all words contained, then fuzzy (best first)."""
        key = fold(query).strip(" ?!.")
        if not key:
            return []
        hits = list(self._npc_names.get(key, []))
        if not hits:
            words = [w for w in re.findall(r"[\w']+", key) if len(w) > 2]
            if words:
                hits = [nid for name, ids in self._npc_names.items() if all(w in name for w in words) for nid in ids]
        if not hits:
            import difflib
            close = difflib.get_close_matches(key, list(self._npc_names), n=3, cutoff=0.8)
            hits = [nid for name in close for nid in self._npc_names[name]]
        if faction:
            friendly = [n for n in hits if faction in self.npcs[n].get("f", faction)]
            hits = friendly or hits
        return hits

    def find_item_in(self, text: str) -> list[int]:
        """Ids of the item named somewhere in ``text`` ("gdzie kupię Rhapsody Malt?"): the longest exact
        name first, then a close match of 2-4 words (speech recognition spells English names its own way)."""
        import difflib

        words = re.findall(r"[a-z0-9']+", fold(text))
        for n in range(min(6, len(words)), 0, -1):
            hits = [" ".join(words[i:i + n]) for i in range(len(words) - n + 1)]
            hits = [g for g in hits if g in self._item_names and (n > 1 or len(g) >= 5)]
            if hits:
                return list(self._item_names[max(hits, key=len)])
        names = list(self._item_names)
        for n in (4, 3, 2):
            for i in range(len(words) - n + 1):
                gram = " ".join(words[i:i + n])
                if len(gram) < 8:
                    continue
                close = difflib.get_close_matches(gram, names, n=1, cutoff=0.84)
                if close:
                    return list(self._item_names[close[0]])
        return []

    def find_quests(self, query: str) -> list[int]:
        key = fold(query).strip(" ?!.")
        hits = list(self._quest_names.get(key, []))
        if not hits:
            words = [w for w in re.findall(r"[\w']+", key) if len(w) > 2]
            if words:
                hits = [qid for name, ids in self._quest_names.items() if all(w in name for w in words) for qid in ids]
        return hits
