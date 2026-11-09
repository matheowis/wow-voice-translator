"""QuestieDB (github.com/Questie/QuestieDB) data for WoW Forever: download and parse.

QuestieDB keeps a separate Forever data set: Era tables with Forever coordinates
(``data/Forever``) plus layered corrections - inherited Classic fixes, a generated delta-base
with the new Forever quests/NPCs, player traces and hand-written Forever fixes. The addon
applies them in that order; :func:`load_world` does the same with the safe Lua reader, so no
downloaded code is run. Entries the reader cannot evaluate (rare dynamic code) are skipped and
counted.

Licence: QuestieDB/Questie data has no single clear licence (see their LICENSE.md). It is
downloaded on your PC for personal use and never committed to this repository.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from . import RAW_DIR
from .luadata import LuaError, NAME, OP, Parser, Unresolved, find_tokens, read_lua, to_list, tokenize
from .net import PoliteClient

log = logging.getLogger(__name__)

REPO = "Questie/QuestieDB"
ENTITIES = ("Quest", "Npc", "Object", "Item")

# raw tables, then corrections in the addon's order (docs/forever.md): legacy -> generated -> traces -> fixes
RAW = {e: f"data/Forever/forever{e}DB.lua" for e in ENTITIES}
CORRECTIONS = {
    "Quest": ["src/corrections/Forever/legacy/classicQuestFixes.lua",
              "src/corrections/Forever/generated/foreverBaseQuest.lua",
              "src/corrections/Forever/traces/foreverQuestTraces.lua",
              "src/corrections/Forever/foreverQuestFixes.lua"],
    "Npc": ["src/corrections/Forever/legacy/classicNPCFixes.lua",
            "src/corrections/Forever/generated/foreverBaseNpc.lua",
            "src/corrections/Forever/traces/foreverNpcTraces.lua",
            "src/corrections/Forever/foreverNPCFixes.lua"],
    "Object": ["src/corrections/Forever/legacy/classicObjectFixes.lua",
               "src/corrections/Forever/generated/foreverBaseObject.lua",
               "src/corrections/Forever/traces/foreverObjectTraces.lua",
               "src/corrections/Forever/foreverObjectFixes.lua"],
    "Item": ["src/corrections/Forever/legacy/classicItemFixes.lua",
             "src/corrections/Forever/legacy/itemStartFixes.lua",
             "src/corrections/Forever/generated/foreverBaseItem.lua",
             "src/corrections/Forever/traces/foreverItemTraces.lua",
             "src/corrections/Forever/foreverItemFixes.lua"],
}
ENUMS = ["src/corrections/enum/expansions.lua", "src/corrections/enum/zones.lua",
         "src/corrections/enum/constants.lua", "src/corrections/enum/quests.lua",
         "src/corrections/enum/professions.lua", "src/corrections/enum/factions.lua",
         "src/corrections/enum/items.lua", "src/corrections/enum/icons.lua"]
SUPPORT = ["support/Forever/Zones/areaIdToUiMapId.lua", "support/Forever/Zones/uiMapIdToAreaId.lua",
           "support/Forever/Zones/dungeons.lua", "support/Forever/Zones/subZoneToParentZone.lua",
           "support/Forever/Zones/instanceIdToAreaId.lua", "support/Forever/QuestXP/xpDB-classic.lua",
           "support/Forever/QuestXP/xpDB-traces.lua"]
DOCS = ["LICENSE.md", "PROVENANCE.md", "docs/forever.md"]


def all_files() -> list[str]:
    files = list(RAW.values()) + ENUMS + SUPPORT + DOCS
    for v in CORRECTIONS.values():
        files += v
    return files


def fetch(client: PoliteClient, raw_dir: Path = RAW_DIR, ref: str = "master") -> Path:
    """Download the files of one QuestieDB commit into raw/questiedb/<sha12>/ (skips files already there)."""
    sha = client.get(f"https://api.github.com/repos/{REPO}/commits/{ref}").json()["sha"]
    dest = raw_dir / "questiedb" / sha[:12]
    for rel in all_files():
        out = dest / rel
        if out.exists() and out.stat().st_size > 0:
            continue
        r = client.get(f"https://raw.githubusercontent.com/{REPO}/{sha}/{rel}")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(r.content)
        log.info("QuestieDB %s: %s (%d KB)", sha[:8], rel, len(r.content) // 1024)
    (dest / "COMMIT").write_text(sha, encoding="ascii")
    return dest


def latest_local(raw_dir: Path = RAW_DIR, name: str = "questiedb") -> Path | None:
    base = raw_dir / name
    if not base.exists():
        return None
    cands = [p for p in base.iterdir() if (p / "COMMIT").exists()]
    return max(cands, key=lambda p: (p / "COMMIT").stat().st_mtime) if cands else None


# the addon (Questie/Questie) keeps two lists the data repository does not: quests that are not in the game
# (blacklist) and holiday quests - both are left out of "where should I go" suggestions
ADDON_REPO = "Questie/Questie"
HOLIDAYS = ["Brewfest", "ChildrensWeek", "DarkmoonFaire", "DayOfTheDead", "HallowsEnd", "HarvestFestival",
            "LoveIsInTheAir", "LunarFestival", "Midsummer", "Noblegarden", "PilgrimsBounty", "WinterVeil"]
ADDON_FILES = ["Database/Corrections/QuestieQuestBlacklist.lua"] + [
    f"Database/Corrections/Holidays/quests/{h}.lua" for h in HOLIDAYS]


def fetch_addon_lists(client: PoliteClient, raw_dir: Path = RAW_DIR, ref: str = "master") -> Path:
    sha = client.get(f"https://api.github.com/repos/{ADDON_REPO}/commits/{ref}").json()["sha"]
    dest = raw_dir / "questie" / sha[:12]
    for rel in ADDON_FILES:
        out = dest / rel
        if out.exists() and out.stat().st_size > 0:
            continue
        r = client.get(f"https://raw.githubusercontent.com/{ADDON_REPO}/{sha}/{rel}")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(r.content)
    (dest / "COMMIT").write_text(sha, encoding="ascii")
    return dest
