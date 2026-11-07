"""Race and sex vocabulary of the voice pools.

The addon sends the ``englishRace`` token of GetPlayerInfoByGUID (e.g. "NightElf",
"Scourge" for Undead) and WoW's sex code (2 male, 3 female, 1/0 unknown).  Voice pools
exist for the eight original races plus an "Unknown" pool; the playable races of WoW
Forever could not be confirmed from the client files, so other races borrow the closest
pool and anything unrecognised uses "Unknown" (configurable with ``voice.race_aliases``).
"""

from __future__ import annotations

UNKNOWN = "Unknown"
MALE, FEMALE = "Male", "Female"
GENDERS = (MALE, FEMALE)

CORE_RACES = ("Human", "Dwarf", "NightElf", "Gnome", "Orc", "Undead", "Tauren", "Troll")
POOL_RACES = CORE_RACES + (UNKNOWN,)

# englishRace token -> pool race (tokens of later expansions map to the nearest original race)
RACE_ALIASES = {
    "Scourge": "Undead",
    "Forsaken": "Undead",
    "Night Elf": "NightElf",
    "BloodElf": "NightElf",
    "VoidElf": "NightElf",
    "Nightborne": "NightElf",
    "HighElf": "NightElf",
    "KulTiran": "Human",
    "Worgen": "Human",
    "DarkIronDwarf": "Dwarf",
    "EarthenDwarf": "Dwarf",
    "Mechagnome": "Gnome",
    "MagharOrc": "Orc",
    "HighmountainTauren": "Tauren",
    "ZandalariTroll": "Troll",
}


def pool_race(english_race: str, extra_aliases: dict | None = None) -> str:
    token = (english_race or "").strip()
    if not token:
        return UNKNOWN
    if extra_aliases and token in extra_aliases:
        return extra_aliases[token]
    if token in CORE_RACES:
        return token
    return RACE_ALIASES.get(token, UNKNOWN)


def gender_of(sex: int) -> str:
    return {2: MALE, 3: FEMALE}.get(int(sex or 0), UNKNOWN)
