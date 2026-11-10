"""Pixel-signal protocol between the WoW addon and the desktop app.

WoW addons cannot open files, sockets or the clipboard, so the addon draws a small
grid of solid-colour cells in the top-left corner of the game's client area while the
mouse rests on a chat line.  The app captures that region (plain GDI screen capture,
no memory access) and decodes it.

Geometry (all in *physical* pixels, relative to the WoW client area origin):
  * the grid is ``COLS`` cells wide, at most ``MAX_ROWS`` rows high,
  * every cell is ``CELL_PX`` x ``CELL_PX`` pixels, cell ``k`` sits at
    column ``k % COLS``, row ``k // COLS``.

Cell colours carry 6 bits: each RGB channel uses one of 4 levels (0, 85, 170, 255),
value = r*16 + g*4 + b.  Cells 0..3 are calibration cells with level 0..3 on every
channel, so the decoder tolerates gamma/brightness post-processing.

Byte stream (big endian) packed 3 bytes -> 4 cells:
  magic 'W','V' | version u8 | type u8 | seq u16 | ... type specific ... | fletcher16 u16
The checksum covers every byte after the magic and before the checksum itself.

A second grid of the same geometry sits ``VOICE_GRID_X_CELLS`` cells to the right of the
first one.  It carries TYPE_VOICE packets: new /say and /party messages the player chose
(with the in-game toggles) to hear spoken.  The addon shows each one for ~0.2 s, one after
another, so hover packets and voice packets never compete for the same pixels.

The same grid also carries what the voice assistant needs: chat lines nobody asked to hear
(TYPE_CHAT - whispers, /yell, /say while its voice toggle is off...; same layout as TYPE_VOICE,
but a type of its own, so an older app that does not know it can never read them aloud) and,
every few seconds, a TYPE_STATE packet with the player's level, money, zone, bags and group;
TYPE_QUEST packets (one per quest in the quest log: objectives, progress, the game's own map marker of
the next objective), TYPE_POS packets (where the player stands, so the app can tell the direction
and distance to a quest marker at the moment you ask), TYPE_PLAYER (class, race, faction) and
TYPE_DONE (the ids of completed quests, in parts, so the assistant knows which quests are left).
TYPE_CHAT lines of public channels (General, Trade, LookingForGroup...) carry the channel's short
name in the race field, which is otherwise empty for lines nobody hears; the player's own loot
and money lines arrive as TYPE_CHAT of kind LOOT / MONEY, and a looted item that can be worn also
as TYPE_LOOT with LFLAG_PICKED (roll id 0) so the assistant can tell whether it is an upgrade.
NPC speech (MONSTER_SAY / YELL / WHISPER) arrives as TYPE_CHAT with the NPC's name as the sender.
The contents of the bags arrive as TYPE_CHAT of kind BAG: the race field says "sync:part/parts", the
text has one item per line, "itemID<TAB>count<TAB>quality<TAB>classID<TAB>subclassID<TAB>equipLoc
index<TAB>name"; a wearable item in the bags also comes once as TYPE_LOOT with LFLAG_BAG (its stats
and what you wear in that slot, never commented on).

This module is the single source of truth; ``addon/WoWVoiceTranslator/Protocol.lua``
implements the same encoder and ``tests/test_protocol.py`` runs the Lua code through
``lupa`` to prove that both sides agree.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

MAGIC = b"WV"
VERSION = 1
COLS = 32
MAX_ROWS = 20
CELL_PX = 4
CALIB_CELLS = 4
MAX_CELLS = COLS * MAX_ROWS
MAX_BYTES = ((MAX_CELLS - CALIB_CELLS) // 4) * 3  # 477
REGION_W = COLS * CELL_PX
REGION_H = MAX_ROWS * CELL_PX

TYPE_HOVER = 1  # full packet: geometry + text of the hovered chat line
TYPE_KEEP = 2  # compact "still hovering the same line" packet
TYPE_TEST = 3  # /wvt test – connection check, same layout as HOVER
TYPE_VOICE = 4  # new /say or /party message to read aloud (second grid)
TYPE_STATE = 5  # player state for the voice assistant (second grid)
TYPE_CHAT = 6  # a chat line only for the assistant's log, never spoken (layout of TYPE_VOICE)
TYPE_QUEST = 7  # one quest of the quest log (assistant)
TYPE_POS = 8  # the player's map position (assistant)
TYPE_PLAYER = 9  # class, race, faction, sex of the player (assistant: what quests fit)
TYPE_DONE = 10  # completed quest ids, in parts (assistant: which quests are left to take)
TYPE_LOOT = 11  # an item of a group loot roll + what the player wears in that slot (assistant: need or greed)

FLAG_SECRET = 0x01  # message text is a Midnight "secret value" – unreadable for addons
FLAG_TRUNCATED = 0x02  # text had to be shortened to fit the grid
VFLAG_SELF = 0x04  # voice packet: the player wrote this message
VFLAG_LANG_PL = 0x08  # voice packet: in-game toggle says "speak Polish translations"

# TYPE_STATE flags
SFLAG_GROUP = 0x01  # in a party or raid
SFLAG_RAID = 0x02
SFLAG_LEADER = 0x04  # the player leads the group
SFLAG_RESTRICTED = 0x08  # an addon restriction is active (combat, boss, M+, PvP match): no kick/lead/raid/ready
SFLAG_COMBAT = 0x10
MAX_NAME_BYTES = 48
MAX_ZONE_BYTES = 64
MAX_MEMBERS = 4
# magic 2 + version/type 2 + seq 2 + flags 1 + level 1 + xp 4 + xp_max 4 + gold 4 + silver_copper 2
# + bag_free 1 + bag_slots 1 + group_size 1
STATE_FIXED_BYTES = 25

# TYPE_QUEST flags
QFLAG_COMPLETE = 0x01  # ready to turn in
QFLAG_FAILED = 0x02
QFLAG_TRACKED = 0x04  # shown in the objective tracker
QFLAG_POI = 0x08  # the game shows a map marker for it (poi_* fields are valid)
QFLAG_POI_OTHER_MAP = 0x10  # ...but on another map than the player's (another zone)
MAX_QUEST_TITLE_BYTES = 64
MAX_QUEST_ZONE_BYTES = 40
MAX_OBJECTIVES = 5
MAX_OBJECTIVE_BYTES = 60
# magic 2 + version/type 2 + seq 2 + sync 2 + index 1 + total 1 + quest id 4 + level 1 + flags 1 + group 1
# + poi map 4 + poi x 2 + poi y 2 + distance 2
QUEST_FIXED_BYTES = 27
POS_BYTES = 18  # magic 2 + version/type 2 + seq 2 + map 4 + x 2 + y 2 + width 2 + height 2 (+ checksum 2)
PLAYER_BYTES = 10  # magic 2 + version/type 2 + seq 2 + class 1 + race 1 + faction 1 + sex 1 (+ checksum 2)
FACTION_NONE, FACTION_ALLIANCE, FACTION_HORDE = 0, 1, 2
# magic 2 + version/type 2 + seq 2 + sync 2 + part 1 + parts 1 + count 1, then count x u24 quest id
DONE_FIXED_BYTES = 11
MAX_DONE_IDS = (MAX_BYTES - DONE_FIXED_BYTES - 2) // 3  # 154 per packet

# TYPE_LOOT
LFLAG_BOP, LFLAG_NEED, LFLAG_GREED, LFLAG_DE = 1, 2, 4, 8  # binds on pickup / the game offers need, greed, disenchant
LFLAG_PICKED = 16  # not a roll: an item the player just looted (assistant: is it better than what you wear?)
LFLAG_BAG = 32  # not a roll: a wearable item found in the bags (its stats, for questions; never commented on)
MAX_LOOT_NAME_BYTES = 64
MAX_WORN_NAME_BYTES = 40
MAX_LOOT_STATS = 12
# magic 2 + version/type 2 + seq 2 + roll id 4 + flags 1 + quality 1 + item level 2 + min level 1 + class 1
# + subclass 1 + equip location 1 + count 1 + name length 1
LOOT_FIXED_BYTES = 20
# index -> GetItemInfoInstant equipLoc (Protocol.lua P.EQUIP_LOCS, 1-based; 0 = not equippable)
EQUIP_LOCS = (
    "", "INVTYPE_HEAD", "INVTYPE_NECK", "INVTYPE_SHOULDER", "INVTYPE_BODY", "INVTYPE_CHEST", "INVTYPE_ROBE",
    "INVTYPE_WAIST", "INVTYPE_LEGS", "INVTYPE_FEET", "INVTYPE_WRIST", "INVTYPE_HAND", "INVTYPE_FINGER",
    "INVTYPE_TRINKET", "INVTYPE_CLOAK", "INVTYPE_WEAPON", "INVTYPE_SHIELD", "INVTYPE_2HWEAPON",
    "INVTYPE_WEAPONMAINHAND", "INVTYPE_WEAPONOFFHAND", "INVTYPE_HOLDABLE", "INVTYPE_RANGED", "INVTYPE_THROWN",
    "INVTYPE_RANGEDRIGHT", "INVTYPE_RELIC", "INVTYPE_TABARD", "INVTYPE_BAG", "INVTYPE_AMMO", "INVTYPE_QUIVER",
)
# stat codes of TYPE_LOOT (Core.lua STAT_PATTERNS); "dps" travels x10
STAT_CODES = {1: "str", 2: "agi", 3: "sta", 4: "int", 5: "spi", 6: "armor", 7: "dps", 8: "ap", 9: "rap", 10: "sp",
              11: "heal", 12: "crit", 13: "hit", 14: "spell_crit", 15: "spell_hit", 16: "def", 17: "mp5",
              18: "block", 19: "dodge", 20: "parry"}
STAT_IDS = {v: k for k, v in STAT_CODES.items()}

# voice grid position: right of the hover grid, same top edge
VOICE_GRID_X_CELLS = COLS + 2
VOICE_ORIGIN_X = VOICE_GRID_X_CELLS * CELL_PX
CAPTURE_W = VOICE_ORIGIN_X + REGION_W  # one capture covers both grids

# how the addon measured the distance to the speaker
DIST_UNKNOWN = 0xFFFF  # encoded "no distance"
DIST_NONE = 0  # no unit token for the sender (not target/group/nameplate)
DIST_EXACT = 1  # UnitDistanceSquared / UnitPosition, in yards
DIST_RANGE = 2  # CheckInteractDistance bucket (estimate)
DIST_NAMEPLATE = 3  # only "has a visible nameplate", i.e. further than the interact ranges
DIST_SELF = 4  # the player
DIST_METHODS = {0: "none", 1: "exact", 2: "range", 3: "nameplate", 4: "self"}

MAX_RACE_BYTES = 24
MAX_SENDER_BYTES = 48
# magic 2 + version/type 2 + seq 2 + flags 1 + kind 1 + sex 1 + distance 2 + method 1 + race_len 1
VOICE_FIXED_BYTES = 13

LEVEL_VALUES = (0, 85, 170, 255)

# chat kind ids (must match KIND table in Core.lua)
CHAT_KINDS = {
    0: "OTHER",
    1: "SAY",
    2: "YELL",
    3: "PARTY",
    4: "PARTY_LEADER",
    5: "RAID",
    6: "RAID_LEADER",
    7: "RAID_WARNING",
    8: "GUILD",
    9: "OFFICER",
    10: "WHISPER",
    11: "WHISPER_INFORM",
    12: "BN_WHISPER",
    13: "BN_WHISPER_INFORM",
    14: "CHANNEL",
    15: "INSTANCE_CHAT",
    16: "INSTANCE_CHAT_LEADER",
    17: "EMOTE",
    18: "TEXT_EMOTE",
    19: "SYSTEM",
    20: "MONSTER_SAY",
    21: "MONSTER_YELL",
    22: "MONSTER_WHISPER",
    23: "COMMUNITIES_CHANNEL",
    24: "LOOT",  # "You receive loot: [Linen Cloth]x2." - only the player's own loot
    25: "MONEY",  # "You loot 5 Silver, 3 Copper."
    26: "BAG",  # a part of the bag contents (not chat): see the module docstring
}
KIND_BAG = 26


@dataclass
class Packet:
    type: int
    seq: int
    flags: int = 0
    # rectangle of the hovered chat line in WoW "base" UI units (screen is 768 units high,
    # origin bottom-left) – the app converts it to client pixels.
    left: float = 0.0
    bottom: float = 0.0
    width: float = 0.0
    height: float = 0.0
    font_height: float = 0.0
    color: tuple[int, int, int] = (255, 255, 255)
    phys_w: int = 0
    phys_h: int = 0
    chat_kind: int = 0
    prefix: str = ""
    text: str = ""
    # TYPE_VOICE only
    sender: str = ""
    race: str = ""  # englishRace token from GetPlayerInfoByGUID ("NightElf", "Scourge"...), "" = unknown
    sex: int = 0  # WoW convention: 2 male, 3 female, 0/1 unknown
    distance: Optional[float] = None  # yards, None = unknown
    dist_method: int = DIST_NONE
    extra: dict = field(default_factory=dict)
    # TYPE_STATE only
    state: Optional["GameState"] = None
    # TYPE_QUEST / TYPE_POS only
    quest: Optional["QuestEntry"] = None
    pos: Optional["PlayerPos"] = None
    # TYPE_PLAYER / TYPE_DONE only
    player: Optional["PlayerInfo"] = None
    done: Optional["DoneChunk"] = None
    loot: Optional["LootItem"] = None

    @property
    def is_secret(self) -> bool:
        return bool(self.flags & FLAG_SECRET)

    @property
    def is_self(self) -> bool:
        return bool(self.flags & VFLAG_SELF)

    @property
    def lang_pl(self) -> bool:
        return bool(self.flags & VFLAG_LANG_PL)

    @property
    def no_speak(self) -> bool:
        return self.type == TYPE_CHAT

    @property
    def chat_kind_name(self) -> str:
        return CHAT_KINDS.get(self.chat_kind, "OTHER")

    def identity(self) -> tuple:
        """Value used by the app to decide whether the hovered line changed."""
        return (self.seq, self.prefix, self.text, round(self.left, 1), round(self.bottom, 1))


@dataclass
class GameState:
    """What the addon reports about the player (TYPE_STATE)."""

    flags: int = 0
    level: int = 0
    xp: int = 0
    xp_max: int = 0
    gold: int = 0
    silver_copper: int = 0  # silver * 100 + copper
    bag_free: int = 0
    bag_slots: int = 0
    group_size: int = 0  # GetNumGroupMembers(), 0 when solo
    name: str = ""
    zone: str = ""
    subzone: str = ""
    members: list[str] = field(default_factory=list)  # other party members (not in a raid)

    @property
    def in_group(self) -> bool:
        return bool(self.flags & SFLAG_GROUP)

    @property
    def in_raid(self) -> bool:
        return bool(self.flags & SFLAG_RAID)

    @property
    def is_leader(self) -> bool:
        return bool(self.flags & SFLAG_LEADER)

    @property
    def restricted(self) -> bool:
        return bool(self.flags & SFLAG_RESTRICTED)

    @property
    def free_party_slots(self) -> int:
        if self.in_raid:
            return max(0, 40 - self.group_size)
        return max(0, 5 - max(1, self.group_size))


@dataclass
class QuestEntry:
    """One quest of the player's quest log (TYPE_QUEST)."""

    sync: int = 0  # changes when the set of quests changes (accepted, abandoned, turned in)
    index: int = 0  # position in that sync, 1-based...
    total: int = 0  # ...of this many quests
    quest_id: int = 0
    level: int = 0
    flags: int = 0
    group: int = 0  # suggested group size, 0/1 = solo
    poi_map: int = 0  # the game's marker for the next objective (uiMapID + normalised x, y)
    poi_x: float = 0.0
    poi_y: float = 0.0
    distance_yd: Optional[int] = None  # the game's own distance to the quest (measured when sent)
    title: str = ""
    zone: str = ""  # map of the marker, or the quest log header
    objectives: list[tuple[bool, str]] = field(default_factory=list)  # (done, "3/8 Kobold Candle")

    @property
    def complete(self) -> bool:
        return bool(self.flags & QFLAG_COMPLETE)

    @property
    def failed(self) -> bool:
        return bool(self.flags & QFLAG_FAILED)

    @property
    def tracked(self) -> bool:
        return bool(self.flags & QFLAG_TRACKED)

    @property
    def has_poi(self) -> bool:
        return bool(self.flags & QFLAG_POI)


@dataclass
class PlayerPos:
    """Where the player stands (TYPE_POS): uiMapID, normalised x/y, the map's size in yards."""

    map_id: int = 0
    x: float = 0.0
    y: float = 0.0
    width: float = 0.0
    height: float = 0.0


@dataclass
class PlayerInfo:
    """Who the player is (TYPE_PLAYER): WoW class id (UnitClass), race id (UnitRace), faction, sex."""

    class_id: int = 0  # 1 Warrior, 2 Paladin, 3 Hunter, 4 Rogue, 5 Priest, 7 Shaman, 8 Mage, 9 Warlock, 11 Druid
    race_id: int = 0  # 1 Human, 2 Orc, 3 Dwarf, 4 Night Elf, 5 Undead, 6 Tauren, 7 Gnome, 8 Troll, 95/96 Skyborne
    faction: int = FACTION_NONE
    sex: int = 0  # 2 male, 3 female

    @property
    def faction_code(self) -> str:
        return {FACTION_ALLIANCE: "A", FACTION_HORDE: "H"}.get(self.faction, "")


@dataclass
class DoneChunk:
    """Part ``part`` of ``parts`` of the completed-quest list number ``sync``; parts == 0: ids just turned in."""

    sync: int = 0
    part: int = 0
    parts: int = 0
    ids: list[int] = field(default_factory=list)


@dataclass
class WornItem:
    """What the player wears in a slot the rolled item would take (TYPE_LOOT)."""

    item_level: int = 0
    subclass_id: int = 0
    name: str = ""
    stats: dict[str, float] = field(default_factory=dict)


@dataclass
class LootItem:
    """An item of a group loot roll (TYPE_LOOT): GetLootRollItemInfo + GetItemInfoInstant + GetItemStats."""

    roll_id: int = 0
    flags: int = 0
    quality: int = 0  # 0 poor, 1 common, 2 uncommon, 3 rare, 4 epic, 5 legendary
    item_level: int = 0
    min_level: int = 0
    class_id: int = 0  # 2 weapon, 4 armor, 9 recipe, 15 miscellaneous...
    subclass_id: int = 0  # armor: 1 cloth, 2 leather, 3 mail, 4 plate, 6 shield; weapons: 0 axe, 7 sword...
    equip_loc: str = ""  # "INVTYPE_HAND"...
    count: int = 1
    name: str = ""
    stats: dict[str, float] = field(default_factory=dict)
    equipped: list[WornItem] = field(default_factory=list)

    @property
    def bop(self) -> bool:
        return bool(self.flags & LFLAG_BOP)

    @property
    def can_need(self) -> bool:
        return bool(self.flags & LFLAG_NEED)

    @property
    def can_greed(self) -> bool:
        return bool(self.flags & LFLAG_GREED)

    @property
    def picked(self) -> bool:
        """Not a roll: the player has just looted this item (roll_id is 0)."""
        return bool(self.flags & LFLAG_PICKED)

    @property
    def from_bag(self) -> bool:
        """Not a roll: a wearable item the bag scan found (roll_id is 0); its stats are kept for questions."""
        return bool(self.flags & LFLAG_BAG)


def _encode_stats(stats: dict[str, float]) -> bytes:
    items = [(STAT_IDS[k], v) for k, v in stats.items() if k in STAT_IDS][:MAX_LOOT_STATS]
    out = bytearray([len(items)])
    for code, v in sorted(items):
        raw = int(round(v * 10 if code == STAT_IDS["dps"] else v))
        out += bytes([code]) + (raw % 65536).to_bytes(2, "big")
    return bytes(out)


def _decode_stats(body: bytes, pos: int) -> tuple[dict[str, float], int]:
    n = body[pos]
    pos += 1
    stats: dict[str, float] = {}
    for _ in range(n):
        code, raw = body[pos], int.from_bytes(body[pos + 1:pos + 3], "big")
        pos += 3
        v = raw - 65536 if raw >= 32768 else raw
        name = STAT_CODES.get(code)
        if name:
            stats[name] = v / 10.0 if name == "dps" else float(v)
    return stats, pos


def _norm16(v: float) -> int:
    return max(0, min(65535, int(round(float(v) * 65535))))


# --------------------------------------------------------------------------- helpers

def fletcher16(data: bytes) -> int:
    s1 = 0
    s2 = 0
    for b in data:
        s1 = (s1 + b) % 255
        s2 = (s2 + s1) % 255
    return (s2 << 8) | s1


def utf8_truncate(raw: bytes, limit: int) -> bytes:
    """Cut ``raw`` to at most ``limit`` bytes without splitting a UTF-8 sequence."""
    if len(raw) <= limit:
        return raw
    cut = limit
    while cut > 0 and (raw[cut] & 0xC0) == 0x80:
        cut -= 1
    return raw[:cut]


def _u16(v: float) -> int:
    v = int(v)
    return 0 if v < 0 else 65535 if v > 65535 else v


def _u32(v: float) -> int:
    v = int(v)
    return 0 if v < 0 else 0xFFFFFFFF if v > 0xFFFFFFFF else v


def _fixed10(v: float) -> int:
    """Fixed point with one decimal, rounded half-up exactly like Core.lua."""
    return _u16(math.floor(v * 10 + 0.5))


def _dist_u16(v: Optional[float]) -> int:
    if v is None:
        return DIST_UNKNOWN
    return max(0, min(DIST_UNKNOWN - 1, int(math.floor(v * 10 + 0.5))))


# bytes before the prefix: magic 2 + version/type 2 + seq 2 + flags 1 + 5*u16 10
# + rgb 3 + phys 4 + kind 1 + prefix_len 1
HOVER_FIXED_BYTES = 26
MAX_PREFIX_BYTES = 80


# --------------------------------------------------------------------------- encoder

def encode_packet(p: Packet) -> bytes:
    """Encode a packet to bytes (reference implementation mirrored by Core.lua)."""
    body = bytearray()
    body += bytes([VERSION, p.type & 0xFF])
    body += _u16(p.seq).to_bytes(2, "big")
    if p.type in (TYPE_HOVER, TYPE_TEST):
        prefix = utf8_truncate(p.prefix.encode("utf-8"), MAX_PREFIX_BYTES)
        room = MAX_BYTES - HOVER_FIXED_BYTES - len(prefix) - 2 - 2
        raw_text = p.text.encode("utf-8")
        text = utf8_truncate(raw_text, room)
        flags = p.flags | (FLAG_TRUNCATED if len(text) < len(raw_text) else 0)
        body.append(flags & 0xFF)
        for v in (p.left, p.bottom, p.width, p.height, p.font_height):
            body += _fixed10(v).to_bytes(2, "big")
        body += bytes(max(0, min(255, int(c))) for c in p.color)
        body += _u16(p.phys_w).to_bytes(2, "big")
        body += _u16(p.phys_h).to_bytes(2, "big")
        body.append(p.chat_kind & 0xFF)
        body.append(len(prefix))
        body += prefix
        body += len(text).to_bytes(2, "big")
        body += text
    elif p.type in (TYPE_VOICE, TYPE_CHAT):
        race = utf8_truncate(p.race.encode("utf-8"), MAX_RACE_BYTES)
        sender = utf8_truncate(p.sender.encode("utf-8"), MAX_SENDER_BYTES)
        room = MAX_BYTES - VOICE_FIXED_BYTES - len(race) - 1 - len(sender) - 2 - 2
        raw_text = p.text.encode("utf-8")
        text = utf8_truncate(raw_text, room)
        flags = p.flags | (FLAG_TRUNCATED if len(text) < len(raw_text) else 0)
        body.append(flags & 0xFF)
        body.append(p.chat_kind & 0xFF)
        body.append(p.sex & 0xFF)
        body += _dist_u16(p.distance).to_bytes(2, "big")
        body.append(p.dist_method & 0xFF)
        body.append(len(race))
        body += race
        body.append(len(sender))
        body += sender
        body += len(text).to_bytes(2, "big")
        body += text
    elif p.type == TYPE_STATE:
        st = p.state or GameState()
        body.append(st.flags & 0xFF)
        body.append(max(0, min(255, int(st.level))))
        for v in (st.xp, st.xp_max, st.gold):
            body += _u32(v).to_bytes(4, "big")
        body += _u16(st.silver_copper).to_bytes(2, "big")
        for v in (st.bag_free, st.bag_slots, st.group_size):
            body.append(max(0, min(255, int(v))))
        for s_, limit in ((st.name, MAX_NAME_BYTES), (st.zone, MAX_ZONE_BYTES), (st.subzone, MAX_ZONE_BYTES)):
            raw = utf8_truncate(s_.encode("utf-8"), limit)
            body.append(len(raw))
            body += raw
        members = [utf8_truncate(m.encode("utf-8"), MAX_NAME_BYTES) for m in st.members[:MAX_MEMBERS]]
        body.append(len(members))
        for raw in members:
            body.append(len(raw))
            body += raw
    elif p.type == TYPE_QUEST:
        q = p.quest or QuestEntry()
        body += _u16(q.sync).to_bytes(2, "big")
        body += bytes([max(0, min(255, q.index)), max(0, min(255, q.total))])
        body += _u32(q.quest_id).to_bytes(4, "big")
        body += bytes([max(0, min(255, q.level)), q.flags & 0xFF, max(0, min(255, q.group))])
        body += _u32(q.poi_map).to_bytes(4, "big")
        body += _norm16(q.poi_x).to_bytes(2, "big") + _norm16(q.poi_y).to_bytes(2, "big")
        body += (DIST_UNKNOWN if q.distance_yd is None else _u16(min(q.distance_yd, 65534))).to_bytes(2, "big")
        for s_, limit in ((q.title, MAX_QUEST_TITLE_BYTES), (q.zone, MAX_QUEST_ZONE_BYTES)):
            raw = utf8_truncate(s_.encode("utf-8"), limit)
            body.append(len(raw))
            body += raw
        objs = q.objectives[:MAX_OBJECTIVES]
        body.append(len(objs))
        for done, text in objs:
            raw = utf8_truncate(text.encode("utf-8"), MAX_OBJECTIVE_BYTES)
            body += bytes([1 if done else 0, len(raw)])
            body += raw
    elif p.type == TYPE_POS:
        pos = p.pos or PlayerPos()
        body += _u32(pos.map_id).to_bytes(4, "big")
        body += _norm16(pos.x).to_bytes(2, "big") + _norm16(pos.y).to_bytes(2, "big")
        body += _u16(round(pos.width)).to_bytes(2, "big") + _u16(round(pos.height)).to_bytes(2, "big")
    elif p.type == TYPE_PLAYER:
        pl = p.player or PlayerInfo()
        body += bytes(max(0, min(255, int(v))) for v in (pl.class_id, pl.race_id, pl.faction, pl.sex))
    elif p.type == TYPE_LOOT:
        it = p.loot or LootItem()
        body += _u32(it.roll_id).to_bytes(4, "big")
        body += bytes([it.flags & 0xFF, max(0, min(255, it.quality))])
        body += _u16(it.item_level).to_bytes(2, "big")
        loc = EQUIP_LOCS.index(it.equip_loc) if it.equip_loc in EQUIP_LOCS else 0
        body += bytes(max(0, min(255, int(v))) for v in (it.min_level, it.class_id, it.subclass_id, loc, it.count))
        name = utf8_truncate(it.name.encode("utf-8"), MAX_LOOT_NAME_BYTES)
        body += bytes([len(name)]) + name + _encode_stats(it.stats)
        worn = it.equipped[:2]
        body.append(len(worn))
        for w in worn:
            wname = utf8_truncate(w.name.encode("utf-8"), MAX_WORN_NAME_BYTES)
            body += _u16(w.item_level).to_bytes(2, "big") + bytes([max(0, min(255, w.subclass_id)), len(wname)])
            body += wname + _encode_stats(w.stats)
    elif p.type == TYPE_DONE:
        d = p.done or DoneChunk()
        ids = d.ids[:MAX_DONE_IDS]
        body += _u16(d.sync).to_bytes(2, "big")
        body += bytes([max(0, min(255, d.part)), max(0, min(255, d.parts)), len(ids)])
        for qid in ids:
            body += max(0, min(0xFFFFFF, int(qid))).to_bytes(3, "big")
    out = bytearray(MAGIC) + body
    out += fletcher16(bytes(body)).to_bytes(2, "big")
    if len(out) > MAX_BYTES:
        raise ValueError("packet too large")
    return bytes(out)


def bytes_to_cells(data: bytes) -> list[int]:
    data = bytes(data) + b"\x00" * ((-len(data)) % 3)
    cells: list[int] = []
    for i in range(0, len(data), 3):
        n = (data[i] << 16) | (data[i + 1] << 8) | data[i + 2]
        cells += [(n >> 18) & 63, (n >> 12) & 63, (n >> 6) & 63, n & 63]
    return cells


def packet_cells(p: Packet) -> list[int]:
    calib = [k * 21 for k in range(CALIB_CELLS)]  # level k on r, g and b
    return calib + bytes_to_cells(encode_packet(p))


def cell_rgb(v: int) -> tuple[int, int, int]:
    return (LEVEL_VALUES[(v >> 4) & 3], LEVEL_VALUES[(v >> 2) & 3], LEVEL_VALUES[v & 3])


def render_cells(cells: list[int], background: Optional[np.ndarray] = None) -> np.ndarray:
    """Render cells to an RGB image of size REGION_H x REGION_W (used by tests/simulator)."""
    if background is None:
        img = np.zeros((REGION_H, REGION_W, 3), dtype=np.uint8)
    else:
        img = background.copy()
    for k, v in enumerate(cells):
        row, col = divmod(k, COLS)
        y, x = row * CELL_PX, col * CELL_PX
        img[y : y + CELL_PX, x : x + CELL_PX] = cell_rgb(v)
    return img


# --------------------------------------------------------------------------- decoder

class DecodeError(Exception):
    pass


def _sample_cells(img: np.ndarray, count: int, ox: int = 0, oy: int = 0) -> np.ndarray:
    """Return (count, 3) mean colour of the 2x2 centre of each cell."""
    ks = np.arange(count)
    rows, cols = np.divmod(ks, COLS)
    ys = oy + rows * CELL_PX + CELL_PX // 2 - 1
    xs = ox + cols * CELL_PX + CELL_PX // 2 - 1
    h, w = img.shape[:2]
    if ys.max(initial=0) + 1 >= h or xs.max(initial=0) + 1 >= w:
        raise DecodeError("region too small")
    a = img[ys, xs].astype(np.int32)
    b = img[ys + 1, xs].astype(np.int32)
    c = img[ys, xs + 1].astype(np.int32)
    d = img[ys + 1, xs + 1].astype(np.int32)
    return (a + b + c + d) // 4


def _calibrate(calib: np.ndarray) -> np.ndarray:
    """Return per-channel thresholds (3 x 3) from the 4 calibration cells."""
    thresholds = np.zeros((3, 3), dtype=np.int32)
    for ch in range(3):
        levels = calib[:, ch]
        if np.any(np.diff(levels) < 20):  # not monotonic or too compressed
            raise DecodeError("calibration failed")
        thresholds[ch] = (levels[:-1] + levels[1:]) // 2
    return thresholds


def _quantize(samples: np.ndarray, thresholds: np.ndarray) -> np.ndarray:
    lv = np.zeros(samples.shape, dtype=np.int32)
    for ch in range(3):
        lv[:, ch] = np.searchsorted(thresholds[ch], samples[:, ch], side="right")
    return lv[:, 0] * 16 + lv[:, 1] * 4 + lv[:, 2]


def _cells_to_bytes(values: np.ndarray) -> bytes:
    out = bytearray()
    for i in range(0, len(values) - 3, 4):
        n = (int(values[i]) << 18) | (int(values[i + 1]) << 12) | (int(values[i + 2]) << 6) | int(values[i + 3])
        out += bytes([(n >> 16) & 255, (n >> 8) & 255, n & 255])
    return bytes(out)


def decode_image(img: np.ndarray, ox: int = 0, oy: int = 0) -> Packet:
    """Decode a packet from an RGB image whose (ox, oy) is the client-area origin."""
    calib = _sample_cells(img, CALIB_CELLS, ox, oy)
    thresholds = _calibrate(calib)

    def read_bytes(n_bytes: int) -> bytes:
        n_cells = ((n_bytes + 2) // 3) * 4
        samples = _sample_cells(img, CALIB_CELLS + n_cells, ox, oy)[CALIB_CELLS:]
        return _cells_to_bytes(_quantize(samples, thresholds))[:n_bytes]

    head = read_bytes(6)
    if head[:2] != MAGIC:
        raise DecodeError("no magic")
    if head[2] != VERSION:
        raise DecodeError(f"unsupported version {head[2]}")
    ptype = head[3]
    if ptype == TYPE_KEEP:
        data = read_bytes(8)
    elif ptype in (TYPE_HOVER, TYPE_TEST):
        fixed = 2 + 4 + 1 + 10 + 3 + 4 + 1 + 1  # up to and including prefix_len
        data = read_bytes(fixed)
        prefix_len = data[-1]
        data = read_bytes(fixed + prefix_len + 2)
        text_len = int.from_bytes(data[-2:], "big")
        total = fixed + prefix_len + 2 + text_len + 2
        if total > MAX_BYTES:
            raise DecodeError("length out of range")
        data = read_bytes(total)
    elif ptype in (TYPE_VOICE, TYPE_CHAT):
        fixed = VOICE_FIXED_BYTES  # up to and including race_len
        data = read_bytes(fixed)
        race_len = data[-1]
        if race_len > MAX_RACE_BYTES:
            raise DecodeError("race length out of range")
        data = read_bytes(fixed + race_len + 1)
        sender_len = data[-1]
        if sender_len > MAX_SENDER_BYTES:
            raise DecodeError("sender length out of range")
        head_len = fixed + race_len + 1 + sender_len + 2
        data = read_bytes(head_len)
        text_len = int.from_bytes(data[-2:], "big")
        total = head_len + text_len + 2
        if total > MAX_BYTES:
            raise DecodeError("length out of range")
        data = read_bytes(total)
    elif ptype == TYPE_STATE:
        pos = STATE_FIXED_BYTES  # then name, zone, subzone (u8 length + bytes), member count, members
        data = read_bytes(pos + 1)
        for limit in (MAX_NAME_BYTES, MAX_ZONE_BYTES, MAX_ZONE_BYTES):
            n = data[pos]
            if n > limit:
                raise DecodeError("state text length out of range")
            pos += 1 + n
            data = read_bytes(pos + 1)
        count = data[pos]
        if count > MAX_MEMBERS:
            raise DecodeError("member count out of range")
        pos += 1
        for _ in range(count):
            data = read_bytes(pos + 1)
            n = data[pos]
            if n > MAX_NAME_BYTES:
                raise DecodeError("member name length out of range")
            pos += 1 + n
        total = pos + 2
        if total > MAX_BYTES:
            raise DecodeError("length out of range")
        data = read_bytes(total)
    elif ptype == TYPE_QUEST:
        pos = QUEST_FIXED_BYTES  # then title, zone (u8 length + bytes), objective count, objectives
        data = read_bytes(pos + 1)
        for limit in (MAX_QUEST_TITLE_BYTES, MAX_QUEST_ZONE_BYTES):
            n = data[pos]
            if n > limit:
                raise DecodeError("quest text length out of range")
            pos += 1 + n
            data = read_bytes(pos + 1)
        count = data[pos]
        if count > MAX_OBJECTIVES:
            raise DecodeError("objective count out of range")
        pos += 1
        for _ in range(count):
            data = read_bytes(pos + 2)
            n = data[pos + 1]
            if n > MAX_OBJECTIVE_BYTES:
                raise DecodeError("objective length out of range")
            pos += 2 + n
        total = pos + 2
        if total > MAX_BYTES:
            raise DecodeError("length out of range")
        data = read_bytes(total)
    elif ptype == TYPE_POS:
        data = read_bytes(POS_BYTES + 2)
    elif ptype == TYPE_PLAYER:
        data = read_bytes(PLAYER_BYTES + 2)
    elif ptype == TYPE_LOOT:
        pos = LOOT_FIXED_BYTES  # name length is the last fixed byte

        def skip_stats(at: int) -> int:
            d = read_bytes(at + 1)
            if d[at] > MAX_LOOT_STATS:
                raise DecodeError("loot stat count out of range")
            return at + 1 + 3 * d[at]

        data = read_bytes(pos)
        if data[-1] > MAX_LOOT_NAME_BYTES:
            raise DecodeError("loot name length out of range")
        pos = skip_stats(pos + data[-1])
        data = read_bytes(pos + 1)
        worn = data[pos]
        if worn > 2:
            raise DecodeError("worn item count out of range")
        pos += 1
        for _ in range(worn):
            data = read_bytes(pos + 4)
            if data[pos + 3] > MAX_WORN_NAME_BYTES:
                raise DecodeError("worn name length out of range")
            pos = skip_stats(pos + 4 + data[pos + 3])
        if pos + 2 > MAX_BYTES:
            raise DecodeError("length out of range")
        data = read_bytes(pos + 2)
    elif ptype == TYPE_DONE:
        data = read_bytes(DONE_FIXED_BYTES)
        count = data[-1]
        if count > MAX_DONE_IDS:
            raise DecodeError("done count out of range")
        data = read_bytes(DONE_FIXED_BYTES + 3 * count + 2)
    else:
        raise DecodeError(f"unknown type {ptype}")

    body, checksum = data[2:-2], int.from_bytes(data[-2:], "big")
    if fletcher16(body) != checksum:
        raise DecodeError("checksum mismatch")

    seq = int.from_bytes(body[2:4], "big")
    if ptype == TYPE_KEEP:
        return Packet(type=ptype, seq=seq)
    if ptype in (TYPE_VOICE, TYPE_CHAT):
        return _parse_voice(body, seq, ptype)
    if ptype == TYPE_STATE:
        return _parse_state(body, seq)
    if ptype == TYPE_QUEST:
        return _parse_quest(body, seq)
    if ptype == TYPE_POS:
        return _parse_pos(body, seq)
    if ptype == TYPE_PLAYER:
        return Packet(type=TYPE_PLAYER, seq=seq, player=PlayerInfo(body[4], body[5], body[6], body[7]))
    if ptype == TYPE_LOOT:
        return Packet(type=TYPE_LOOT, seq=seq, loot=_parse_loot(body))
    if ptype == TYPE_DONE:
        count = body[8]
        ids = [int.from_bytes(body[9 + 3 * i: 12 + 3 * i], "big") for i in range(count)]
        return Packet(type=TYPE_DONE, seq=seq,
                      done=DoneChunk(int.from_bytes(body[4:6], "big"), body[6], body[7], ids))

    pos = 4
    flags = body[pos]
    pos += 1
    nums = [int.from_bytes(body[pos + 2 * i : pos + 2 * i + 2], "big") / 10.0 for i in range(5)]
    pos += 10
    color = (body[pos], body[pos + 1], body[pos + 2])
    pos += 3
    phys_w = int.from_bytes(body[pos : pos + 2], "big")
    phys_h = int.from_bytes(body[pos + 2 : pos + 4], "big")
    pos += 4
    chat_kind = body[pos]
    pos += 1
    plen = body[pos]
    pos += 1
    prefix = body[pos : pos + plen].decode("utf-8", errors="replace")
    pos += plen
    tlen = int.from_bytes(body[pos : pos + 2], "big")
    pos += 2
    text = body[pos : pos + tlen].decode("utf-8", errors="replace")
    return Packet(
        type=ptype,
        seq=seq,
        flags=flags,
        left=nums[0],
        bottom=nums[1],
        width=nums[2],
        height=nums[3],
        font_height=nums[4],
        color=color,
        phys_w=phys_w,
        phys_h=phys_h,
        chat_kind=chat_kind,
        prefix=prefix,
        text=text,
    )


def _parse_voice(body: bytes, seq: int, ptype: int = TYPE_VOICE) -> Packet:
    pos = 4
    flags, kind, sex = body[pos], body[pos + 1], body[pos + 2]
    pos += 3
    dist_raw = int.from_bytes(body[pos : pos + 2], "big")
    pos += 2
    method = body[pos]
    pos += 1
    rlen = body[pos]
    pos += 1
    race = body[pos : pos + rlen].decode("utf-8", errors="replace")
    pos += rlen
    slen = body[pos]
    pos += 1
    sender = body[pos : pos + slen].decode("utf-8", errors="replace")
    pos += slen
    tlen = int.from_bytes(body[pos : pos + 2], "big")
    pos += 2
    text = body[pos : pos + tlen].decode("utf-8", errors="replace")
    return Packet(
        type=ptype,
        seq=seq,
        flags=flags,
        chat_kind=kind,
        sex=sex,
        distance=None if dist_raw == DIST_UNKNOWN else dist_raw / 10.0,
        dist_method=method,
        race=race,
        sender=sender,
        text=text,
    )


def _parse_state(body: bytes, seq: int) -> Packet:
    pos = 4
    st = GameState(flags=body[pos], level=body[pos + 1])
    pos += 2
    st.xp, st.xp_max, st.gold = (int.from_bytes(body[pos + 4 * i : pos + 4 * i + 4], "big") for i in range(3))
    pos += 12
    st.silver_copper = int.from_bytes(body[pos : pos + 2], "big")
    pos += 2
    st.bag_free, st.bag_slots, st.group_size = body[pos], body[pos + 1], body[pos + 2]
    pos += 3
    texts = []
    for _ in range(3):
        n = body[pos]
        pos += 1
        texts.append(body[pos : pos + n].decode("utf-8", errors="replace"))
        pos += n
    st.name, st.zone, st.subzone = texts
    count = body[pos]
    pos += 1
    for _ in range(count):
        n = body[pos]
        pos += 1
        st.members.append(body[pos : pos + n].decode("utf-8", errors="replace"))
        pos += n
    return Packet(type=TYPE_STATE, seq=seq, flags=st.flags, state=st)


def _parse_quest(body: bytes, seq: int) -> Packet:
    q = QuestEntry(sync=int.from_bytes(body[4:6], "big"), index=body[6], total=body[7],
                   quest_id=int.from_bytes(body[8:12], "big"), level=body[12], flags=body[13], group=body[14],
                   poi_map=int.from_bytes(body[15:19], "big"), poi_x=int.from_bytes(body[19:21], "big") / 65535,
                   poi_y=int.from_bytes(body[21:23], "big") / 65535)
    dist = int.from_bytes(body[23:25], "big")
    q.distance_yd = None if dist == DIST_UNKNOWN else dist
    pos = 25
    texts = []
    for _ in range(2):
        n = body[pos]
        texts.append(body[pos + 1 : pos + 1 + n].decode("utf-8", errors="replace"))
        pos += 1 + n
    q.title, q.zone = texts
    count = body[pos]
    pos += 1
    for _ in range(count):
        done, n = body[pos], body[pos + 1]
        q.objectives.append((bool(done), body[pos + 2 : pos + 2 + n].decode("utf-8", errors="replace")))
        pos += 2 + n
    return Packet(type=TYPE_QUEST, seq=seq, flags=q.flags, quest=q)


def _parse_loot(body: bytes) -> LootItem:
    it = LootItem(roll_id=int.from_bytes(body[4:8], "big"), flags=body[8], quality=body[9],
                  item_level=int.from_bytes(body[10:12], "big"), min_level=body[12], class_id=body[13],
                  subclass_id=body[14], count=body[16])
    loc = body[15]
    it.equip_loc = EQUIP_LOCS[loc] if loc < len(EQUIP_LOCS) else ""
    n = body[17]
    it.name = body[18:18 + n].decode("utf-8", errors="replace")
    it.stats, pos = _decode_stats(body, 18 + n)
    worn = body[pos]
    pos += 1
    for _ in range(worn):
        w = WornItem(item_level=int.from_bytes(body[pos:pos + 2], "big"), subclass_id=body[pos + 2])
        n = body[pos + 3]
        w.name = body[pos + 4:pos + 4 + n].decode("utf-8", errors="replace")
        w.stats, pos = _decode_stats(body, pos + 4 + n)
        it.equipped.append(w)
    return it


def _parse_pos(body: bytes, seq: int) -> Packet:
    pos = PlayerPos(map_id=int.from_bytes(body[4:8], "big"), x=int.from_bytes(body[8:10], "big") / 65535,
                    y=int.from_bytes(body[10:12], "big") / 65535, width=int.from_bytes(body[12:14], "big"),
                    height=int.from_bytes(body[14:16], "big"))
    return Packet(type=TYPE_POS, seq=seq, pos=pos)


def try_decode(img: np.ndarray, search: int = 3) -> Optional[Packet]:
    """Decode, tolerating a few pixels of misalignment of the grid origin."""
    for oy in range(search + 1):
        for ox in range(search + 1):
            try:
                return decode_image(img, ox, oy)
            except DecodeError:
                continue
    return None


def try_decode_at(img: np.ndarray, x0: int, search: int = 3, back: int = 2) -> Optional[Packet]:
    """Decode a grid whose origin should be near (x0, 0); rounding may move it a few px either way."""
    for oy in range(search + 1):
        for ox in range(max(0, x0 - back), x0 + search + 1):
            try:
                return decode_image(img, ox, oy)
            except DecodeError:
                continue
    return None


# --------------------------------------------------------------------------- geometry

def base_rect_to_client_px(p: Packet, client_w: int, client_h: int) -> tuple[int, int, int, int]:
    """Convert the packet's base-unit rectangle to (x, y, w, h) client pixels (y from top)."""
    if client_h <= 0:
        return (0, 0, 0, 0)
    scale = client_h / 768.0  # WoW base coordinate system is 768 units high
    x = round(p.left * scale)
    w = round(p.width * scale)
    h = round(p.height * scale)
    y = round(client_h - (p.bottom + p.height) * scale)
    return (x, y, w, h)


def font_px(p: Packet, client_h: int) -> float:
    return p.font_height * client_h / 768.0
