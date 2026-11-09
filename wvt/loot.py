"""Need or greed: the item of a group loot roll against the player's class, level and worn gear.

The addon sends the rolled item (type, slot, armor/weapon kind, required level, stats) and what the
player wears in the slot(s) it would take (TYPE_LOOT).  The verdict follows the usual group rule
"need only for an upgrade for your class":

* the class cannot use it (weapon skill it cannot learn, armor heavier than it can wear, a relic of
  another class) - greed;
* the class can use it: compare a stat score (class weights tuned for levelling: a rogue values
  agility, a mage intellect and spell power, a warrior strength and weapon DPS...) with the worn
  item - clearly better: need; about the same or worse: greed;
* a lighter armor type than the class's best (a leather piece for a level-45 warrior) counts less;
* not equipment (recipes, bags, materials) - greed, with a hint.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .protocol import LootItem, WornItem

CLASS_PL = {1: "wojownika", 2: "paladyna", 3: "łowcy", 4: "łotrzyka", 5: "kapłana", 7: "szamana", 8: "maga",
            9: "czarnoksiężnika", 11: "druida"}
CLASS_DAT = {1: "wojownikowi", 2: "paladynowi", 3: "łowcy", 4: "łotrzykowi", 5: "kapłanowi", 7: "szamanowi",
             8: "magowi", 9: "czarnoksiężnikowi", 11: "druidowi"}
# weapon subclasses each class can learn (classic rules); 0/1 axes, 2 bow, 3 gun, 4/5 maces, 6 polearm,
# 7/8 swords, 10 staff, 13 fist, 15 dagger, 16 thrown, 18 crossbow, 19 wand
WEAPONS = {
    1: {0, 1, 2, 3, 4, 5, 6, 7, 8, 10, 13, 15, 16, 18}, 2: {0, 1, 4, 5, 6, 7, 8}, 3: {0, 1, 2, 3, 6, 7, 8, 10, 13, 15, 16, 18},
    4: {2, 3, 4, 7, 13, 15, 16, 18}, 5: {4, 10, 15, 19}, 7: {0, 1, 4, 5, 10, 13, 15}, 8: {7, 10, 15, 19},
    9: {7, 10, 15, 19}, 11: {4, 5, 10, 13, 15},
}
SHIELD_CLASSES = {1, 2, 7}
RELIC_CLASS = {7: 2, 8: 11, 9: 7}  # libram -> paladin, idol -> druid, totem -> shaman
CLOTH, LEATHER, MAIL, PLATE = 1, 2, 3, 4
# levelling stat weights (relative, per point; crit/hit are in percent)
WEIGHTS = {
    1: {"str": 1.0, "agi": 0.6, "sta": 0.6, "ap": 0.45, "crit": 8, "hit": 8, "dps": 3.0, "armor": 0.01, "def": 0.3,
        "dodge": 3, "parry": 3, "block": 0.1},
    2: {"str": 1.0, "int": 0.6, "sta": 0.6, "agi": 0.3, "spi": 0.2, "ap": 0.4, "crit": 6, "hit": 6, "sp": 0.5,
        "heal": 0.3, "mp5": 1.0, "dps": 2.5, "armor": 0.01, "def": 0.2, "block": 0.1},
    3: {"agi": 1.0, "int": 0.35, "sta": 0.5, "rap": 0.45, "ap": 0.3, "crit": 8, "hit": 8, "dps": 0.5, "str": 0.1,
        "spi": 0.1, "armor": 0.01},
    4: {"agi": 1.0, "str": 0.5, "sta": 0.5, "ap": 0.45, "crit": 8, "hit": 8, "dps": 3.0, "armor": 0.01, "dodge": 2},
    5: {"int": 1.0, "spi": 0.8, "sta": 0.5, "sp": 0.9, "heal": 0.5, "mp5": 1.5, "spell_crit": 6, "spell_hit": 4,
        "dps": 0.1, "armor": 0.005},
    7: {"int": 0.8, "str": 0.6, "agi": 0.5, "sta": 0.5, "spi": 0.3, "ap": 0.3, "sp": 0.6, "heal": 0.4, "mp5": 1.2,
        "crit": 5, "spell_crit": 5, "dps": 2.0, "armor": 0.01},
    8: {"int": 1.0, "spi": 0.5, "sta": 0.5, "sp": 1.0, "spell_crit": 8, "spell_hit": 8, "mp5": 1.0, "dps": 0.1,
        "armor": 0.005},
    9: {"int": 0.8, "sta": 0.8, "spi": 0.5, "sp": 1.0, "spell_crit": 6, "spell_hit": 8, "mp5": 0.8, "dps": 0.1,
        "armor": 0.005},
    11: {"int": 0.7, "agi": 0.6, "str": 0.6, "sta": 0.6, "spi": 0.5, "ap": 0.3, "sp": 0.6, "heal": 0.4, "mp5": 1.0,
         "crit": 4, "dps": 0.5, "armor": 0.01},
}
RANGED_LOCS = {"INVTYPE_RANGED", "INVTYPE_RANGEDRIGHT", "INVTYPE_THROWN"}
NOT_GEAR = {"", "INVTYPE_BAG", "INVTYPE_AMMO", "INVTYPE_QUIVER", "INVTYPE_TABARD", "INVTYPE_BODY"}
SLOT_PL = {"INVTYPE_HEAD": "hełm", "INVTYPE_NECK": "naszyjnik", "INVTYPE_SHOULDER": "naramienniki",
           "INVTYPE_CHEST": "napierśnik", "INVTYPE_ROBE": "szata", "INVTYPE_WAIST": "pas", "INVTYPE_LEGS": "spodnie",
           "INVTYPE_FEET": "buty", "INVTYPE_WRIST": "karwasze", "INVTYPE_HAND": "rękawice", "INVTYPE_FINGER": "pierścień",
           "INVTYPE_TRINKET": "błyskotka", "INVTYPE_CLOAK": "płaszcz", "INVTYPE_SHIELD": "tarcza",
           "INVTYPE_HOLDABLE": "przedmiot do drugiej ręki", "INVTYPE_RELIC": "relikwia", "INVTYPE_BAG": "torba",
           "INVTYPE_TABARD": "tabard", "INVTYPE_BODY": "koszula"}
ARMOR_NOUN = {CLOTH: "materiał", LEATHER: "skóra", MAIL: "kolczuga", PLATE: "płyta"}
WEAPON_PL = {0: "topór jednoręczny", 1: "topór dwuręczny", 2: "łuk", 3: "strzelba", 4: "buzdygan jednoręczny",
             5: "buzdygan dwuręczny", 6: "broń drzewcowa", 7: "miecz jednoręczny", 8: "miecz dwuręczny", 10: "kostur",
             13: "broń pięściowa", 14: "broń", 15: "sztylet", 16: "broń miotana", 18: "kusza", 19: "różdżka",
             20: "wędka"}
STAT_PL = {"str": "siły", "agi": "zręczności", "sta": "wytrzymałości", "int": "intelektu", "spi": "ducha",
           "armor": "pancerza", "dps": "DPS", "ap": "mocy ataku", "rap": "mocy ataku dystansowego",
           "sp": "mocy zaklęć", "heal": "leczenia", "crit": "procent krytyka", "hit": "procent trafienia",
           "spell_crit": "procent krytyka zaklęć", "spell_hit": "procent trafienia zaklęć", "def": "obrony",
           "mp5": "many co 5 sekund", "block": "bloku", "dodge": "procent uniku", "parry": "procent parowania"}
WEAPON_CLASS, ARMOR_CLASS, RECIPE_CLASS = 2, 4, 9


def best_armor(class_id: int, level: int) -> int:
    if class_id in (5, 8, 9):
        return CLOTH
    if class_id in (4, 11):
        return LEATHER
    if class_id in (3, 7):
        return MAIL if level >= 40 else LEATHER
    if class_id in (1, 2):
        return PLATE if level >= 40 else MAIL
    return CLOTH


@dataclass
class LootAdvice:
    verdict: str  # "need" / "greed" / "pass"
    text: str  # Polish, to be spoken
    item: str
    gain: float = 0.0  # score difference (positive = upgrade)


def _num(v: float) -> str:
    return (f"{v:.1f}".replace(".", ",") if abs(v - round(v)) > 0.05 else str(int(round(v))))


def kind_text(it: LootItem) -> str:
    if it.class_id == WEAPON_CLASS:
        return WEAPON_PL.get(it.subclass_id, "broń")
    if it.class_id == RECIPE_CLASS:
        return ""
    slot = SLOT_PL.get(it.equip_loc, "")
    if it.class_id == ARMOR_CLASS and it.subclass_id in ARMOR_NOUN and it.equip_loc not in (
            "INVTYPE_CLOAK", "INVTYPE_FINGER", "INVTYPE_NECK", "INVTYPE_TRINKET"):
        return f"{slot}, {ARMOR_NOUN[it.subclass_id]}" if slot else ARMOR_NOUN[it.subclass_id]
    return slot or "przedmiot"


def usable(it: LootItem, class_id: int, level: int) -> tuple[bool, str]:
    """(can the class use it, why not in Polish)."""
    if it.class_id == WEAPON_CLASS:
        if it.subclass_id in (14, 20):  # miscellaneous, fishing pole
            return True, ""
        ok = it.subclass_id in WEAPONS.get(class_id, set())
        return ok, "" if ok else f"{CLASS_PL.get(class_id, 'twoja klasa')} nie używa takiej broni"
    if it.class_id == ARMOR_CLASS:
        if it.equip_loc == "INVTYPE_SHIELD" or it.subclass_id == 6:
            ok = class_id in SHIELD_CLASSES
            return ok, "" if ok else "tarczy nie założysz"
        if it.subclass_id in RELIC_CLASS:
            ok = RELIC_CLASS[it.subclass_id] == class_id
            return ok, "" if ok else "to relikwia innej klasy"
        if it.subclass_id in (CLOTH, LEATHER, MAIL, PLATE):
            limit = best_armor(class_id, level)
            if it.subclass_id > limit:
                need_lvl = 40 if (class_id in (1, 2) and it.subclass_id == PLATE) or (
                    class_id in (3, 7) and it.subclass_id == MAIL) else 0
                why = f"{ARMOR_NOUN[it.subclass_id]} jest dla ciebie za ciężka"
                return False, why + (f" do {need_lvl} poziomu" if need_lvl and level < need_lvl else "")
    return True, ""


def score(stats: dict[str, float], class_id: int, ranged_slot: bool = False) -> float:
    w = dict(WEIGHTS.get(class_id, WEIGHTS[1]))
    if ranged_slot:
        if class_id == 3:
            w["dps"] = 3.0  # a hunter's gun or bow is his main weapon
        elif class_id in (5, 8, 9):
            w["dps"] = 2.0  # wand while levelling
        else:
            w["dps"] = 0.4
    return sum(w.get(k, 0.0) * v for k, v in stats.items())


def _compare_base(it: LootItem, class_id: int, ranged: bool) -> tuple[Optional[float], Optional[WornItem]]:
    """Score of what the new item replaces and that item (None = an empty slot)."""
    worn = it.equipped
    if not worn:
        return None, None
    scored = [(score(w.stats, class_id, ranged), w) for w in worn]
    if it.equip_loc == "INVTYPE_2HWEAPON":  # replaces main hand + off hand
        return sum(s for s, _ in scored), scored[0][1]
    return min(scored, key=lambda sw: sw[0])  # rings, trinkets, one-handers: the weaker one goes


def _deltas(new: dict[str, float], old: dict[str, float], class_id: int, ranged: bool) -> list[tuple[str, float]]:
    w = WEIGHTS.get(class_id, WEIGHTS[1])
    keys = set(new) | set(old)
    d = [(k, new.get(k, 0.0) - old.get(k, 0.0)) for k in keys if k != "armor" or class_id in (1, 2)]
    weight = (lambda k: (3.0 if ranged and class_id == 3 else w.get(k, 0)) if k == "dps" else w.get(k, 0))
    d = [(k, v) for k, v in d if abs(v) > 0.05 and weight(k) > 0]
    d.sort(key=lambda kv: -abs(kv[1] * weight(kv[0])))
    return d


def _stat_phrase(items: list[tuple[str, float]]) -> str:
    parts = []
    for k, v in items[:2]:
        if k == "dps":
            parts.append(f"{_num(abs(v))} DPS {'więcej' if v > 0 else 'mniej'}")
        else:
            parts.append(f"{'plus' if v > 0 else 'minus'} {_num(abs(v))} {STAT_PL.get(k, k)}")
    return " i ".join(parts)


def advise(it: LootItem, class_id: int, level: int) -> LootAdvice:
    name = it.name or "przedmiot"
    kind = kind_text(it)
    who = CLASS_PL.get(class_id, "")
    head = f"{name}, {kind}." if kind else f"{name}."

    if not it.can_need and not it.can_greed:
        return LootAdvice("pass", f"{head} Gra nie pozwala ci na to rzucać.", name)
    if it.class_id == RECIPE_CLASS:
        return LootAdvice("greed", f"{head} To przepis: need tylko, jeśli masz ten zawód, inaczej greed.", name)
    if it.equip_loc in NOT_GEAR or it.class_id not in (WEAPON_CLASS, ARMOR_CLASS):
        if it.equip_loc == "INVTYPE_BAG":
            return LootAdvice("greed", f"{head} Torba: jeśli masz małe torby, weź need, inaczej greed.", name)
        return LootAdvice("greed", f"{head} Nic do założenia, daj greed.", name)
    if not class_id:
        return LootAdvice("greed", f"{head} Nie wiem jeszcze, jaką masz klasę, więc nie doradzę. Po /reload już będę wiedziała.", name)

    ok, why = usable(it, class_id, level)
    if not ok:
        return LootAdvice("greed", f"{head} Dla {who} to nic: {why}. Daj greed.", name)

    ranged = it.equip_loc in RANGED_LOCS
    new = score(it.stats, class_id, ranged)
    lighter = it.class_id == ARMOR_CLASS and it.subclass_id in (CLOTH, LEATHER, MAIL) and \
        it.subclass_id < best_armor(class_id, level) and it.equip_loc not in ("INVTYPE_CLOAK",)
    if lighter:
        new *= 0.8
    later = f" Założysz od {it.min_level} poziomu." if it.min_level > level else ""
    if new <= 0.5:
        return LootAdvice("greed", f"{head} Nie ma statystyk, które przydają się {CLASS_DAT.get(class_id, 'tobie')}. "
                          f"Greed.{later}", name, new)
    base, worn = _compare_base(it, class_id, ranged)
    if base is None:
        return LootAdvice("need", f"{head} Nic tam nie nosisz, więc dla {who} to czysty zysk. Bierz need.{later}", name, new)
    gain = new - base
    deltas = _deltas(it.stats, worn.stats if worn else {}, class_id, ranged)
    ups = [d for d in deltas if d[1] > 0]
    if gain >= max(1.0, 0.05 * abs(base)):
        what = _stat_phrase(ups) if ups else ""
        note = " Lżejszy pancerz niż twój najlepszy, ale i tak lepszy." if lighter else ""
        return LootAdvice("need", f"{head} Dla {who} to ulepszenie"
                          + (f": {what} względem tego, co nosisz." if what else ".") + f"{note} Bierz need.{later}",
                          name, gain)
    if gain > -max(1.0, 0.05 * abs(base)):
        return LootAdvice("greed", f"{head} Prawie to samo, co nosisz. Greed, chyba że nikt inny tego nie chce.", name, gain)
    downs = [d for d in deltas if d[1] < 0]
    what = _stat_phrase(downs)
    return LootAdvice("greed", f"{head} Słabsze od twojego" + (f" ({what})" if what else "") + ". Daj greed.", name, gain)
