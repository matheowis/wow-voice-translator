"""Where to go and where things are, for the assistant.

Built from what the addon reports (quest log, position, level, class/race/faction, completed quests)
and the QuestieDB world data (:mod:`wvt.kb.world`):

* :func:`where_next` - "gdzie mam się teraz udać?": first quests to turn in, then the nearest open
  quest of the log, then new quests to pick up near you (available for your level, faction, class and
  completed quests), otherwise the zone that fits your level best and its main quest giver;
* :func:`find_place` - "gdzie jest trener magów / karczmarz / Hogger?": the nearest NPC of that kind
  or name on your map;
* :func:`dungeons_for_level` - "jaki dungeon na mój poziom?" from the wiki infobox levels;

Each answer can carry a chat line that puts a map waypoint (``/wvt way <map> <x> <y> <label>``) or
tracks a quest of the log (``/wvt track <id>``); like every command it runs only on Enter.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .kb.world import NPC_FLAGS, Player, Spot, World, fold
from .quests import QuestBook, Where, compass, spoken_objective


@dataclass
class Advice:
    text: str
    line: str = ""
    spot: Optional[Spot] = None
    quest_id: int = 0


def waypoint_line(spot: Spot, label: str = "") -> str:
    if not spot or not spot.ui_map:
        return ""
    label = re.sub(r"[|\n]", " ", label or spot.name)[:60]
    return f"/wvt way {spot.ui_map} {spot.x:.1f} {spot.y:.1f} {label}".strip()


def spot_where(world: World, spot: Spot, pos) -> str:
    if spot.distance is not None and pos is not None and pos.width:
        dx = (spot.x / 100 - pos.x) * pos.width
        dy = (spot.y / 100 - pos.y) * pos.height
        return Where(spot.distance, compass(dx, dy)).polish()
    zone = world.area_name(spot.area)
    return f"strefa {zone}" if zone else "na innej mapie"


def cap(s: str) -> str:
    return s[:1].upper() + s[1:] if s else s


# capitals (AreaTable ids) first when nothing is on the player's map (Stormwind, Ironforge, Darnassus /
# Orgrimmar, Undercity, Thunder Bluff), then the rest
CAPITALS = {"A": (1519, 1537, 1657), "H": (1637, 1497, 1638)}


def plural_quests(n: int) -> str:
    if n == 1:
        return "quest"
    return "questy" if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14 else "questów"


def _join(items: list[str]) -> str:
    items = [i for i in items if i]
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " i " + items[-1] if items else ""


# --------------------------------------------------------------------------- where next
def where_next(world: Optional[World], book: QuestBook, player: Player, pos=None) -> Advice:
    level = player.level
    quests = book.quests() if book.known else []
    ready = [q for q in quests if q.complete]
    if ready:
        near = book.nearest(ready)
        if near:
            q, w = near
            more = f" Do oddania masz jeszcze {len(ready) - 1}." if len(ready) > 1 else ""
            return Advice(f"Najpierw oddaj {q.title}: {w.polish()}.{more}", f"/wvt track {q.quest_id}",
                          quest_id=q.quest_id)
        if world:
            for q in ready:
                spot = world.finisher_spot(q.quest_id, pos)
                if spot:
                    return Advice(f"Najpierw oddaj {q.title}, u {spot.name}: {spot_where(world, spot, pos)}.",
                                  waypoint_line(spot, spot.name), spot, q.quest_id)
        return Advice(f"Masz do oddania: {_join([q.title for q in ready[:3]])}.")

    open_q = [q for q in quests if not q.complete and not q.failed and (not q.level or q.level <= level + 3)]
    near = book.nearest(open_q)
    if near and near[1].distance_yd is not None and near[1].distance_yd <= 800:
        q, w = near
        left = [spoken_objective(t) for done, t in q.objectives if not done]
        tail = f" Zostało: {_join(left[:2])}." if left else ""
        return Advice(f"Najbliżej masz {q.title}: {w.polish()}.{tail}", f"/wvt track {q.quest_id}",
                      quest_id=q.quest_id)

    if world is None:
        return Advice("Nie mam jeszcze bazy questów. Zbuduj ją: python -m wvt.kb.build.")
    active = {q.quest_id for q in quests}
    cands: list[tuple[int, dict, Spot]] = []
    if player.done_known and level:
        for qid, q in world.quests.items():
            lvl = q.get("lvl") or q.get("min") or 0
            if lvl and not (level - 4 <= lvl <= level + 2):
                continue
            if not world.available(q, qid, player, active):
                continue
            spot = world.starter_spot(qid, pos, player.faction)
            if spot is not None:
                cands.append((qid, q, spot))
    here = [c for c in cands if c[2].distance is not None and c[2].distance <= 1500]
    if here:
        groups: dict[tuple[str, int], list] = {}
        for c in here:
            groups.setdefault((c[2].kind, c[2].eid), []).append(c)
        best = min(groups.values(), key=lambda g: g[0][2].distance / (1 + 0.4 * (len(g) - 1)))
        spot = best[0][2]
        titles = [q["n"] for _, q, _ in best][:3]
        n = len(best)
        giver = f"u {spot.name}" if spot.kind == "npc" else f"przy {spot.name}"
        text = f"{cap(giver)} weźmiesz {n} {plural_quests(n)}: {_join(titles)}. {cap(spot_where(world, spot, pos))}."
        if open_q and not near:
            text += f" Masz też {len(open_q)} {plural_quests(len(open_q))} w dzienniku."
        return Advice(text, waypoint_line(spot, spot.name), spot)

    zone_tip = best_zone(world, player, cands, pos)
    if zone_tip:
        if not player.done_known:
            zone_tip.text += " Nie znam jeszcze twoich ukończonych questów, więc to ogólna rada."
        return zone_tip
    if open_q:
        q = open_q[0]
        w = book.where(q)
        return Advice(f"Masz w dzienniku {q.title}" + (f": {w.polish()}." if w else ".") + " Zacznij od tego.",
                      f"/wvt track {q.quest_id}", quest_id=q.quest_id)
    return Advice("Nie widzę nic konkretnego na twój poziom. Może jakiś dungeon? Zapytaj, jaki pasuje.")


def best_zone(world: World, player: Player, cands: list, pos=None) -> Optional[Advice]:
    level = player.level
    if not level:
        return None
    counts: dict[int, int] = {}
    for _, q, spot in cands:
        counts[spot.area] = counts.get(spot.area, 0) + 1
    scored = []
    for area, (lo, hi, n) in world.levels.items():
        if not (lo - 2 <= level <= hi + 1) or area in world.dungeons:
            continue
        hub = world.hubs.get(area, {}).get(player.faction) if player.faction else None
        if player.faction and not hub:
            continue  # no quest giver of your faction there
        avail = counts.get(area, 0)
        fit = -abs((lo + hi) / 2 - level)
        scored.append((avail if player.done_known else n, fit, area, hub))
    if not scored:
        return None
    scored.sort(key=lambda s: (s[0], s[1]), reverse=True)
    avail, _, area, hub = scored[0]
    lo, hi, _ = world.levels[area]
    zone = world.area_name(area)
    text = f"Na twój poziom najlepiej pasuje {zone}, questy na poziomy {lo}–{hi}."
    if player.done_known and avail:
        text += f" Czeka tam {avail} {plural_quests(avail)} dla ciebie."
    line, spot = "", None
    if hub:
        spot = world.nearest("npc", hub[0], pos)
        if spot:
            text += f" Zacznij od {spot.name}."
            line = waypoint_line(spot, f"{spot.name} ({zone})")
    return Advice(text, line, spot)


# --------------------------------------------------------------------------- services and NPCs by name
SERVICES = (
    (r"\b(mistrz\w* lot\w*|lot\w*|lotn\w*|gryf\w*|wiwern\w*|hipogryf\w*|flight\w*|taxi|przelot\w*|nietoperz\w*)\b",
     "FLIGHT_MASTER", "mistrza lotów"),
    (r"\b(karczm\w*|karczmarz\w*|gospod\w*|inn|innkeeper\w*|zajazd\w*|kamien\w* powrotu|hearthstone\w*)\b",
     "INNKEEPER", "karczmarza"),
    (r"\b(bank\w*|bankier\w*)\b", "BANKER", "bankiera"),
    (r"\b(aukcj\w*|aukcyjn\w*|auction\w*|ah)\b", "AUCTIONEER", "domu aukcyjnego"),
    (r"\b(stajn\w*|stajenn\w*|stable\w*)\b", "STABLEMASTER", "stajennego"),
    (r"\b(napraw\w*|repair\w*)\b", "REPAIR", "naprawy"),
    (r"\b(duch\w* uzdrowic\w*|aniol\w*|spirit healer)\b", "SPIRIT_HEALER", "ducha uzdrowiciela"),
    (r"\b(trener\w*|trainer\w*|nauczyciel\w*)\b", "TRAINER", "trenera"),
)
CLASS_TRAINER = (("mag", "Mage"), ("wojown", "Warrior"), ("lowc", "Hunter"), ("hunter", "Hunter"), ("lotrzyk", "Rogue"),
                 ("rogue", "Rogue"), ("kaplan", "Priest"), ("priest", "Priest"), ("paladyn", "Paladin"),
                 ("szaman", "Shaman"), ("czarnoksiez", "Warlock"), ("warlock", "Warlock"), ("druid", "Druid"))
PROF_TRAINER = (("alchem", "Alchem"), ("kowal", "Blacksmith"), ("zaklin", "Enchant"), ("enchant", "Enchant"),
                ("inzynier", "Engineer"), ("ziel", "Herbalis"), ("skorni", "Leatherwork"), ("kaletn", "Leatherwork"),
                ("leather", "Leatherwork"), ("gornic", "Min"), ("skorow", "Skinn"), ("oprawi", "Skinn"),
                ("krawie", "Tailor"), ("gotow", "Cook"), ("kucharz", "Cook"), ("pierwszej pomocy", "First Aid"),
                ("bandaz", "First Aid"), ("wedkar", "Fish"), ("ryb", "Fish"), ("jazd", "Riding"),
                ("broni", "Weapon Master"))
CLASS_EN = {1: "Warrior", 2: "Paladin", 3: "Hunter", 4: "Rogue", 5: "Priest", 7: "Shaman", 8: "Mage", 9: "Warlock",
            11: "Druid"}
TRAINER_PL = {"Mage": "magów", "Warrior": "wojowników", "Hunter": "łowców", "Rogue": "łotrzyków", "Priest": "kapłanów",
              "Paladin": "paladynów", "Shaman": "szamanów", "Warlock": "czarnoksiężników", "Druid": "druidów",
              "Alchem": "alchemii", "Blacksmith": "kowalstwa", "Enchant": "zaklinania", "Engineer": "inżynierii",
              "Herbalis": "zielarstwa", "Leatherwork": "kaletnictwa", "Min": "górnictwa", "Skinn": "oprawiania skór",
              "Tailor": "krawiectwa", "Cook": "gotowania", "First Aid": "pierwszej pomocy", "Fish": "wędkarstwa",
              "Riding": "jazdy", "Weapon Master": "broni"}
_ENDINGS = ("owi", "ami", "em", "ie", "om", "a", "e", "u", "y", "i", "o")
_NOISE = {"gdzie", "jest", "sa", "znajde", "znalezc", "szukac", "kupie", "najblizszy", "najblizsza", "najblizsze",
          "najblizej", "do", "mnie", "zaprowadz", "prowadz", "doprowadz", "pokaz", "na", "mapie", "zaznacz", "postaw",
          "znacznik", "ten", "ta", "to", "tego", "tej", "jakis", "jakas", "jakies", "tu", "tutaj", "w", "okolicy",
          "iskra", "ej", "hej", "prosze", "mi", "mam", "isc", "ide", "moge", "z", "czy", "jak", "dojsc", "trafic", "npc",
          "potrzebuje", "musze", "chce", "nawiguj", "wyznacz", "droge", "trase", "i"}


def _service(folded: str, player: Player) -> Optional[tuple[str, str, str]]:
    """(npc flag, subName fragment or "", Polish name in genitive) asked for, or None."""
    for rx, flag, pl in SERVICES:
        if re.search(rx, folded):
            sub = ""
            if flag == "TRAINER":
                sub = next((en for pre, en in PROF_TRAINER if pre in folded), "")
                if not sub:
                    sub = next((en for pre, en in CLASS_TRAINER if re.search(rf"\b{pre}", folded)), "")
                if not sub and player.class_id in CLASS_EN:
                    sub = CLASS_EN[player.class_id]
                pl = f"trenera {TRAINER_PL.get(sub, sub)}" if sub else pl
            return flag, sub, pl
    return None


def _friendly(npc: dict, faction: str) -> bool:
    f = npc.get("f")
    return not faction or not f or faction in f


def nearest_service(world: World, flag: str, sub: str, player: Player, pos=None) -> Optional[Spot]:
    """The nearest such NPC on the player's map; without one, one in a capital of the player's faction."""
    bit = NPC_FLAGS[flag]
    best: Optional[Spot] = None
    elsewhere: list[Spot] = []
    for nid, n in world.npcs.items():
        if not n.get("fl", 0) & bit or not _friendly(n, player.faction):
            continue
        if sub and sub.lower() not in (n.get("sub", "") + " " + n["n"]).lower():
            continue
        s = world.nearest("npc", nid, pos)
        if s is None:
            continue
        if s.distance is None:
            elsewhere.append(s)
        elif best is None or s.distance < best.distance:
            best = s
    if best is not None or not elsewhere:
        return best
    order = {world.map_of(area): i for i, area in enumerate(CAPITALS.get(player.faction, ()))}
    elsewhere.sort(key=lambda s: (order.get(s.ui_map, len(order)), s.name))
    return elsewhere[0]


def _name_candidates(phrase: str) -> list[str]:
    words = [w for w in fold(phrase).split() if w not in _NOISE and len(w) > 1]
    if not words:
        return []
    out = [" ".join(words)]
    stripped = []
    for w in words:
        base = w
        for e in _ENDINGS:
            if w.endswith(e) and len(w) - len(e) >= 3:
                base = w[: -len(e)]
                break
        stripped.append(base)
    out.append(" ".join(stripped))
    out += words + stripped
    return list(dict.fromkeys(o for o in out if len(o) >= 3))


def find_npc(world: World, phrase: str, player: Player, pos=None, english: str = "") -> Optional[Spot]:
    import difflib

    for cand in _name_candidates(english) + _name_candidates(phrase):
        ids = world.find_npcs(cand, player.faction)
        if not ids:  # "hogera" -> "hogger": fuzzy on the whole name
            close = difflib.get_close_matches(cand, list(world._npc_names), n=3, cutoff=0.78)
            ids = [nid for name in close for nid in world._npc_names[name]]
        spots = [s for nid in ids[:20] if (s := world.nearest("npc", nid, pos))]
        if spots:
            return min(spots, key=lambda s: s.distance if s.distance is not None else 1e9)
    return None


def find_dungeon(world: World, polish: str, english: str = "") -> Optional[Spot]:
    """The entrance of a dungeon named in the question (English name, or close to it)."""
    import difflib

    text = f" {fold(polish)} {fold(english)} "
    words = [w for w in re.findall(r"[a-z0-9']+", text) if w not in _NOISE]
    grams = {" ".join(words[i:i + n]) for n in (1, 2, 3) for i in range(len(words))}
    best, best_score = None, 0.0
    for d in world.dungeons.values():
        if not d.get("at"):
            continue
        key = fold(_clean(d["n"]))
        if len(key) < 4:
            continue
        score = 1.0 if f" {key} " in text else max(
            (difflib.SequenceMatcher(None, key, g).ratio() for g in grams), default=0.0)
        if score > best_score:
            best, best_score = d, score
    if best is None or best_score < 0.82:
        return None
    return _entrance(world, best["n"])


def find_place(world: Optional[World], polish: str, player: Player, pos=None, english: str = "") -> Optional[Advice]:
    if world is None:
        return None
    folded = fold(polish)
    if re.search(r"\b(wejsci\w*|wejsc\w*|entrance|dungeon\w*|dung\w*|loch\w*|instancj\w*|raid\w*|rajd\w*)\b",
                 folded + " " + fold(english)):
        spot = find_dungeon(world, polish, english)
        if spot is not None:
            return Advice(f"Wejście do {_clean(spot.name)}: {spot_where(world, spot, pos)}.",
                          waypoint_line(spot, f"wejście: {_clean(spot.name)}"), spot)
    svc = _service(folded, player)
    if svc:
        flag, sub, pl = svc
        spot = nearest_service(world, flag, sub, player, pos)
        if spot is None:
            return Advice(f"Nie znam żadnego {pl} dla twojej frakcji. Zapytaj strażnika w mieście.")
        if spot.distance is None:
            return Advice(f"Na tej mapie nie ma {pl}. Najbliżej w mieście: {spot.name}, {spot_where(world, spot, pos)}.",
                          waypoint_line(spot, spot.name), spot)
        return Advice(f"Najbliżej masz {spot.name}: {spot_where(world, spot, pos)}.", waypoint_line(spot, spot.name), spot)
    spot = find_npc(world, polish, player, pos, english)
    if spot is None:
        return None
    lvl = world.npcs.get(spot.eid, {}).get("lv") if spot.kind == "npc" else None
    extra = ""
    if lvl and spot.kind == "npc" and not world.npcs[spot.eid].get("f"):
        extra = f" Poziom {lvl[0]}" + (f"–{lvl[1]}" if lvl[1] != lvl[0] else "") + "."
    return Advice(f"{spot.name}: {spot_where(world, spot, pos)}.{extra}", waypoint_line(spot, spot.name), spot)


# --------------------------------------------------------------------------- dungeons by level
# classic level ranges for dungeons whose wiki page has no Classic level (the wiki table wins when present)
CLASSIC_DUNGEONS = {
    "Ragefire Chasm": (13, 18), "Wailing Caverns": (17, 24), "The Deadmines": (17, 26), "Shadowfang Keep": (22, 30),
    "Blackfathom Deeps": (24, 32), "The Stockade": (24, 32), "Gnomeregan": (29, 38), "Razorfen Kraul": (29, 38),
    "Scarlet Monastery Graveyard": (26, 36), "Scarlet Monastery Library": (29, 39),
    "Scarlet Monastery Armory": (32, 42), "Scarlet Monastery Cathedral": (35, 45), "Razorfen Downs": (37, 46),
    "Uldaman": (41, 51), "Zul'Farrak": (44, 54), "Maraudon": (46, 55), "Temple of Atal'Hakkar": (50, 60),
    "Blackrock Depths": (52, 60), "Lower Blackrock Spire": (55, 60), "Upper Blackrock Spire": (55, 60),
    "Dire Maul": (55, 60), "Stratholme": (58, 60), "Scholomance": (58, 60),
}


def load_instances(path: Path) -> list[dict]:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def _clean(name: str) -> str:
    n = re.sub(r"\s*\((Classic|instance|Forever)\)$", "", name)
    return re.sub(r"^The ", "", n)


def dungeon_list(instances: list[dict]) -> list[dict]:
    seen = {fold(_clean(d["name"])) for d in instances}
    out = [d for d in instances if d.get("type") == "Dungeon"]
    for name, (lo, hi) in CLASSIC_DUNGEONS.items():
        if fold(_clean(name)) not in seen:
            out.append({"name": name, "type": "Dungeon", "lo": lo, "hi": hi, "location": ""})
    return out


def _entrance(world: Optional[World], name: str) -> Optional[Spot]:
    if world is None:
        return None
    key = fold(_clean(name))
    for area, d in world.dungeons.items():
        if fold(_clean(d["n"])) == key and d.get("at"):
            zone, x, y = d["at"]
            return Spot("dungeon", area, d["n"], int(zone), float(x), float(y), world.map_of(int(zone)))
    return None


def dungeons_for_level(instances: list[dict], level: int, world: Optional[World] = None, pos=None) -> Advice:
    if not level:
        return Advice("Nie znam twojego poziomu, daj mi chwilę po wejściu do gry.")
    fits = [d for d in dungeon_list(instances) if d["lo"] - 1 <= level <= d["hi"]]
    if not fits:
        lower = sorted((d for d in dungeon_list(instances) if d["lo"] > level), key=lambda d: d["lo"])
        if lower:
            d = lower[0]
            return Advice(f"Na {level} poziom jeszcze nic nie pasuje. Najbliższy będzie {_clean(d['name'])} od {d['lo']} poziomu.")
        return Advice("Na twój poziom nie znam żadnego dungeonu, zostają rajdy.")
    fits.sort(key=lambda d: (abs((d["lo"] + d["hi"]) / 2 - level), d["lo"]))
    parts = []
    for d in fits[:3]:
        where = f", {d['location'].split(',')[-1].strip()}" if d.get("location") else ""
        parts.append(f"{_clean(d['name'])} ({d['lo']}–{d['hi']}{where})")
    text = f"Na {level} poziom pasują: {_join(parts)}."
    spot = _entrance(world, fits[0]["name"])
    line = waypoint_line(spot, f"wejście: {_clean(fits[0]['name'])}") if spot else ""
    if spot:
        text += f" Wejście do {_clean(fits[0]['name'])}: {spot_where(world, spot, pos)}."
    return Advice(text, line, spot)
