"""Read the downloaded QuestieDB Forever files into Python dicts (no Lua code is run).

:func:`load_tables` parses the raw entity tables and applies the Static corrections in the
addon's order with QuestieDB's operation semantics: a plain field replaces the value, ``X_add`` /
``X_remove`` add or remove list items (starter/finisher slots, spawn maps per zone).
Dynamic (faction/class dependent) corrections are not applied.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from .luadata import NAME, OP, LuaError, Parser, Unresolved, find_tokens, read_lua, tokenize
from .questie import CORRECTIONS, ENTITIES, ENUMS, RAW

log = logging.getLogger(__name__)

# Forever race/class/NPC-flag constants (QuestieDB src/corrections/enum/expansions.lua: Forever race IDs
# 95/96 = Skyborne Alliance/Horde use mask bits 32/33; classes fall back to the Classic table)
RACE_KEYS = {
    "NONE": 0, "HUMAN": 1, "ORC": 2, "DWARF": 4, "NIGHT_ELF": 8, "UNDEAD": 16, "TAUREN": 32, "GNOME": 64,
    "TROLL": 128, "GOBLIN": 256, "SKYBORNE_ALLIANCE": 1 << 32, "SKYBORNE_HORDE": 1 << 33,
}
RACE_KEYS["ALL_ALLIANCE"] = 1 + 4 + 8 + 64 + RACE_KEYS["SKYBORNE_ALLIANCE"]
RACE_KEYS["ALL_HORDE"] = 2 + 16 + 32 + 128 + RACE_KEYS["SKYBORNE_HORDE"]
CLASS_KEYS = {"NONE": 0, "WARRIOR": 1, "PALADIN": 2, "HUNTER": 4, "ROGUE": 8, "PRIEST": 16, "DEATH_KNIGHT": 32,
              "SHAMAN": 64, "MAGE": 128, "WARLOCK": 256, "MONK": 512, "DRUID": 1024, "ALL_CLASSES": 1503}
NPC_FLAGS = {"NONE": 0, "GOSSIP": 1, "QUEST_GIVER": 2, "VENDOR": 4, "FLIGHT_MASTER": 8, "TRAINER": 16,
             "SPIRIT_HEALER": 32, "SPIRIT_GUIDE": 64, "INNKEEPER": 128, "BANKER": 256, "PETITIONER": 512,
             "TABARD_DESIGNER": 1024, "BATTLEMASTER": 2048, "AUCTIONEER": 4096, "STABLEMASTER": 8192,
             "REPAIR": 16384, "BARBER": 33554432, "TRANSMOGRIFIER": 536870912}

KEYS_NAME = {"Quest": "questKeys", "Npc": "npcKeys", "Object": "objectKeys", "Item": "itemKeys"}
DATA_NAME = {"Quest": "questData", "Npc": "npcData", "Object": "objectData", "Item": "itemData"}
SLOT_FIELDS = {"Quest": {"startedBy", "finishedBy", "objectives"}}
MAP_FIELDS = {"Npc": {"spawns", "waypoints"}, "Object": {"spawns", "waypoints"}}


def _table_after(tokens: list, pattern: list, start: int = 0, resolve=None, records: bool = False):
    """Parse the table assigned right after ``pattern``; a long string holding ``return {...}`` is unwrapped."""
    i = find_tokens(tokens, pattern, start)
    if i < 0:
        return None
    j = i + len(pattern)
    kind, value, _ = tokens[j]
    if kind == "str":  # QuestieDB keeps big tables as "[[return {...}]]" strings
        inner = tokenize(value.encode("utf-8").decode("latin-1"))
        k = find_tokens(inner, [(NAME, "return"), (OP, "{")])
        if k < 0:
            return None
        return Parser(inner, resolve, k + 2).entities(records=records)
    if kind == OP and value == "{":
        return Parser(tokens, resolve, j + 1).entities(records=records)
    return None


def _keys(tokens: list, entity: str) -> dict[str, int]:
    t = _table_after(tokens, [(NAME, "QuestieDB"), (OP, "."), (NAME, KEYS_NAME[entity]), (OP, "=")])
    return {k: v for k, v in (t or {}).items() if isinstance(k, str) and isinstance(v, int)}


def _enums(src: Path) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for rel in ENUMS:
        path = src / rel
        if not path.exists():
            continue
        toks = tokenize(read_lua(path))
        i = 0
        while True:
            i = find_tokens(toks, [(NAME, "constants"), (OP, "."), (NAME, None), (OP, "="), (OP, "{")], i)
            if i < 0:
                break
            name = toks[i + 2][1]
            try:
                out[name] = {k: v for k, v in Parser(toks, None, i + 5).entities().items() if isinstance(k, str)}
            except LuaError:
                pass
            i += 5
    return out


class _Resolver:
    """Resolves ``questKeys.name``, ``zoneIDs.ELWYNN_FOREST``, ``raceIDs.HUMAN + ...`` in corrections."""

    def __init__(self, keys: dict[str, dict[str, int]], enums: dict[str, dict]):
        self.aliases: dict[str, str] = {}
        self.targets: dict[str, tuple[dict, bool]] = {
            "QuestieDB.questKeys": (keys["Quest"], True), "QuestieDB.npcKeys": (keys["Npc"], True),
            "QuestieDB.objectKeys": (keys["Object"], True), "QuestieDB.itemKeys": (keys["Item"], True),
            "QuestieDB.raceKeys": (RACE_KEYS, False), "QuestieDB.classKeys": (CLASS_KEYS, False),
            "QuestieDB.npcFlags": (NPC_FLAGS, False),
            "ZoneDB.zoneIDs": (enums.get("zoneIDs", {}), False),
            "QuestieDB.sortKeys": (enums.get("sortKeys", {}), False),
            "QuestieDB.specialFlags": (enums.get("specialFlags", {}), False),
            "QuestieDB.questFlags": (enums.get("questFlags", {}), False),
            "QuestieDB.factionIDs": (enums.get("factionIDs", {}), False),
            "QuestieProfessions.professionKeys": (enums.get("professionKeys", {}), False),
            "QuestieProfessions.specializationKeys": (enums.get("specializationKeys", {}), False),
            "QuestieProfessions.rankNames": (enums.get("rankNames", {}), False),
        }

    def __call__(self, parts: list[str]):
        if parts[0] in self.aliases:
            target, rest = self.aliases[parts[0]], parts[1:]
        elif len(parts) >= 3:
            target, rest = parts[0] + "." + parts[1], parts[2:]
        else:
            raise Unresolved(".".join(parts))
        table, is_keys = self.targets.get(target, (None, False))
        if table is None or len(rest) != 1:
            raise Unresolved(".".join(parts))
        name = rest[0]
        if name in table:
            return table[name]
        if is_keys:
            for op in ("add", "remove"):
                base = name[: -len(op) - 1]
                if name.endswith("_" + op) and base in table:
                    return (table[base], op)
        raise Unresolved(".".join(parts))


def _corrections(path: Path, resolver: _Resolver) -> tuple[dict, int, list[str]]:
    """The table returned by ``function X:Load()`` of a correction file."""
    toks = tokenize(read_lua(path))
    j = find_tokens(toks, [(NAME, "function"), (NAME, None), (OP, ":"), (NAME, "Load"), (OP, "("), (OP, ")")])
    if j < 0:
        return {}, 0, ["no Load()"]
    k, n = j + 6, len(toks)
    resolver.aliases = {}
    while k < n - 1 and not (toks[k][0] == NAME and toks[k][1] == "return"):
        if (toks[k][0] == NAME and toks[k][1] == "local" and k + 3 < n and toks[k + 1][0] == NAME
                and toks[k + 2][1] == "="):
            parts, m = [], k + 3
            while m < n and toks[m][0] == NAME:
                parts.append(toks[m][1])
                if m + 1 < n and toks[m + 1][0] == OP and toks[m + 1][1] == ".":
                    m += 2
                else:
                    break
            if parts:
                resolver.aliases[toks[k + 1][1]] = ".".join(parts)
        k += 1
    if k + 1 >= n or toks[k + 1][1] != "{":
        return {}, 0, ["Load() does not return a table"]
    p = Parser(toks, resolver, k + 2)
    data = p.entities(records=True)
    return data, p.skipped, p.skipped_examples


def _canon(v: Any) -> Any:
    if isinstance(v, dict):
        return tuple((k, _canon(x)) for k, x in sorted(v.items(), key=lambda kv: str(kv[0])))
    return v


def _list_op(base: Any, operand: Any, op: str) -> dict:
    items = [v for _, v in sorted(base.items(), key=lambda kv: str(kv[0]))] if isinstance(base, dict) \
        else ([] if base is None else [base])
    extra = [v for _, v in sorted(operand.items(), key=lambda kv: str(kv[0]))] if isinstance(operand, dict) else [operand]
    if op == "add":
        have = {_canon(x) for x in items}
        for x in extra:
            if _canon(x) not in have:
                items.append(x)
                have.add(_canon(x))
    else:
        drop = {_canon(x) for x in extra}
        items = [x for x in items if _canon(x) not in drop]
    return {i + 1: v for i, v in enumerate(items)}


def _apply(records: dict, corrections: dict, keys: dict[str, int], entity: str) -> int:
    names = {v: k for k, v in keys.items()}
    slots, maps = SLOT_FIELDS.get(entity, set()), MAP_FIELDS.get(entity, set())
    touched = 0
    for eid, corr in corrections.items():
        if not isinstance(corr, dict) or not isinstance(eid, int):
            continue
        rec = records.setdefault(eid, {})
        touched += 1
        for key, value in corr.items():
            if isinstance(key, tuple):
                idx, op = key
                field = names.get(idx, "")
                base = rec.get(idx)
                if field in slots and isinstance(value, dict):
                    cur = dict(base) if isinstance(base, dict) else {}
                    for slot, ids in value.items():
                        cur[slot] = _list_op(cur.get(slot), ids, op)
                    rec[idx] = cur
                elif field in maps and isinstance(value, dict):
                    cur = dict(base) if isinstance(base, dict) else {}
                    for zone, coords in value.items():
                        cur[zone] = _list_op(cur.get(zone), coords, op)
                    rec[idx] = cur
                else:
                    rec[idx] = _list_op(base, value, op)
            elif isinstance(key, int):
                rec[key] = value
    return touched


def load_tables(src: Path) -> dict:
    """Raw Forever tables + all Static corrections: {"keys", "data": {entity: {id: {field: value}}}, "enums", "stats"}."""
    keys, data, stats = {}, {}, {}
    for e in ENTITIES:
        toks = tokenize(read_lua(src / RAW[e]))
        keys[e] = _keys(toks, e)
        data[e] = _table_after(toks, [(NAME, "QuestieDB"), (OP, "."), (NAME, DATA_NAME[e]), (OP, "=")]) or {}
        stats[e] = {"raw": len(data[e])}
    enums = _enums(src)
    resolver = _Resolver(keys, enums)
    for e in ENTITIES:
        for rel in CORRECTIONS[e]:
            path = src / rel
            if not path.exists():
                continue
            corr, skipped, examples = _corrections(path, resolver)
            touched = _apply(data[e], corr, keys[e], e)
            stats[e][Path(rel).stem] = {"entries": touched, "skipped_fields": skipped}
            if skipped:
                log.info("%s: pominięte pola %d (np. %s)", Path(rel).name, skipped, "; ".join(examples[:2]))
    return {"keys": keys, "data": data, "enums": enums, "stats": stats}


def load_addon_lists(src: Path | None) -> tuple[set[int], set[int]]:
    """(blacklisted quest ids, holiday quest ids) from Questie's own lists; conditional entries are kept."""
    black: set[int] = set()
    events: set[int] = set()
    if src is None:
        return black, events
    path = src / "Database/Corrections/QuestieQuestBlacklist.lua"
    if path.exists():
        toks = tokenize(read_lua(path))

        def resolve(parts: list[str]):
            if parts == ["HIDE_ON_MAP"]:
                return "HIDE_ON_MAP"
            raise Unresolved(".".join(parts))  # "Expansions.Current >= ..." - other game versions

        for name in ("questsToBlacklist",):
            t = _table_after(toks, [(NAME, "local"), (NAME, name), (OP, "=")], resolve=resolve) or {}
            black.update(k for k, v in t.items() if isinstance(k, int) and (v is True or v == "HIDE_ON_MAP"))
    holidays = src / "Database/Corrections/Holidays/quests"
    if holidays.exists():
        for path in holidays.glob("*.lua"):
            toks = tokenize(read_lua(path))
            for i in range(len(toks) - 4):  # {"Lunar Festival", 8619, ...}
                if toks[i][1] == "{" and toks[i + 1][0] == "str" and toks[i + 2][1] == "," and toks[i + 3][0] == "num":
                    events.add(int(toks[i + 3][1]))
    return black, events


def load_support(src: Path) -> dict:
    """Zone -> UiMap ids, dungeon entrances and quest XP."""
    out: dict[str, Any] = {}
    toks = tokenize(read_lua(src / "support/Forever/Zones/areaIdToUiMapId.lua"))
    zdb = [(NAME, "ZoneDB"), (OP, "."), (NAME, "private"), (OP, ".")]
    base = _table_after(toks, zdb + [(NAME, "areaIdToUiMapId"), (OP, "=")]) or {}
    over = _table_after(toks, zdb + [(NAME, "areaIdToUiMapIdOverride"), (OP, "=")]) or {}
    out["area_to_map"] = {k: v for k, v in {**base, **over}.items() if isinstance(k, int) and isinstance(v, int)}
    toks = tokenize(read_lua(src / "support/Forever/Zones/dungeons.lua"))
    out["dungeons"] = _table_after(toks, [(NAME, "local"), (NAME, "dungeons"), (OP, "=")]) or {}
    xp: dict[int, tuple[int, int]] = {}
    for rel in ("support/Forever/QuestXP/xpDB-classic.lua", "support/Forever/QuestXP/xpDB-traces.lua"):
        path = src / rel
        if not path.exists():
            continue
        toks = tokenize(read_lua(path))
        for qid, v in (_table_after(toks, [(NAME, "QuestXP"), (OP, "."), (NAME, "db"), (OP, "=")]) or {}).items():
            if isinstance(qid, int) and isinstance(v, dict) and 2 in v:
                xp[qid] = (int(v.get(1, 0)), int(v[2]))
        pat = [(NAME, "QuestXP"), (OP, "."), (NAME, "db"), (OP, "["), ("num", None), (OP, "]"), (OP, "="), (OP, "{")]
        i = 0
        while True:
            i = find_tokens(toks, pat, i)
            if i < 0:
                break
            try:
                v = Parser(toks, None, i + len(pat)).table()
                if 2 in v:
                    xp[toks[i + 4][1]] = (int(v.get(1, 0)), int(v[2]))
            except LuaError:
                pass
            i += len(pat)
    out["xp"] = xp
    return out
