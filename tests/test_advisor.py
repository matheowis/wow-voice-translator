"""Where to go, places and dungeons (advisor), need or greed (loot), reminders and session statistics."""

import time
from datetime import datetime

import pytest

from tests.test_kb import world
from wvt import protocol as P
from wvt.advisor import dungeons_for_level, find_place, waypoint_line, where_next
from wvt.kb.world import Player, Spot
from wvt.loot import advise, best_armor
from wvt.quests import QuestBook
from wvt.reminders import Reminders, parse_reminder, say_reminder
from wvt.session_stats import SessionStats

POS = P.PlayerPos(map_id=1436, x=0.50, y=0.50, width=3500, height=2333)


def book_with(*quests):
    b = QuestBook()
    b.on_packet(P.Packet(type=P.TYPE_POS, seq=1, pos=POS))
    for i, q in enumerate(quests, 1):
        b.on_packet(P.Packet(type=P.TYPE_QUEST, seq=i, quest=P.QuestEntry(sync=1, index=i, total=len(quests), **q)))
    if not quests:
        b.on_packet(P.Packet(type=P.TYPE_QUEST, seq=1, quest=P.QuestEntry(sync=1, index=0, total=0)))
    return b


def mage(**kw):
    return Player(**{"level": 12, "race_id": 1, "class_id": 8, "faction": "A", "done": frozenset(), "done_known": True,
                     **kw})


# --------------------------------------------------------------------------- where next

def test_turn_ins_come_first_then_the_nearest_open_quest():
    w = world()
    poi = P.QFLAG_POI
    b = book_with(dict(quest_id=50, title="Ready One", flags=P.QFLAG_COMPLETE | poi, poi_map=1436, poi_x=0.52, poi_y=0.5),
                  dict(quest_id=51, title="Open One", level=12, flags=poi, poi_map=1436, poi_x=0.5, poi_y=0.45,
                       objectives=[(False, "2/5 Goretusk Liver")]))
    adv = where_next(w, b, mage(), POS)
    assert adv.text.startswith("Najpierw oddaj Ready One: około 70 jardów na wschód") and adv.line == "/wvt track 50"
    b = book_with(dict(quest_id=51, title="Open One", level=12, flags=poi, poi_map=1436, poi_x=0.5, poi_y=0.45,
                       objectives=[(False, "2/5 Goretusk Liver")]))
    adv = where_next(w, b, mage(), POS)
    assert adv.text == "Najbliżej masz Open One: około 120 jardów na północ. Zostało: Goretusk Liver 2 z 5."
    assert adv.line == "/wvt track 51"


def test_new_quests_nearby_with_a_waypoint_then_the_zone_for_the_level():
    w = world()
    adv = where_next(w, book_with(), mage(), P.PlayerPos(map_id=1436, x=0.56, y=0.47, width=3500, height=2333))
    # Gryan's Aide gives two quests (Report to Gryan, Mage Lesson) a few yards away
    assert adv.text.startswith("U Gryan's Aide weźmiesz 2 questy: Report to Gryan i Mage Lesson.")
    assert adv.line == "/wvt way 1436 56.0 47.0 Gryan's Aide"
    far = where_next(w, book_with(), mage(), P.PlayerPos(map_id=1429, x=0.5, y=0.5, width=3000, height=2000))
    assert far.text.startswith("Na twój poziom najlepiej pasuje Westfall") and far.line.startswith("/wvt way 1436")
    unknown = where_next(w, book_with(), mage(done_known=False), POS)
    assert "Nie znam jeszcze twoich ukończonych questów" in unknown.text


def test_places_services_and_npcs():
    w = world()
    me = mage()
    pos = P.PlayerPos(map_id=1436, x=0.56, y=0.47, width=3500, height=2333)
    adv = find_place(w, "gdzie jest najbliższy mistrz lotów", me, pos)
    assert adv.text.startswith("Najbliżej masz Thor: około") and adv.line == "/wvt way 1436 56.5 52.6 Thor"
    adv = find_place(w, "zaprowadź mnie do Gryana Stoutmantle", me, pos)
    assert adv.text.startswith("Gryan Stoutmantle: ") and adv.line.endswith("Gryan Stoutmantle")
    assert find_place(w, "gdzie jest Pillagera Defiasów", me, pos, english="where is the Defias Pillager") is not None
    assert find_place(w, "gdzie jest coś zupełnie innego", me, pos) is None
    assert waypoint_line(Spot("npc", 1, "A|B\nC", 40, 1.25, 2.0, 1436)) == "/wvt way 1436 1.2 2.0 A B C"


def test_dungeons_for_the_level_use_the_wiki_table_and_classic_ranges():
    inst = [{"name": "Hall of Thanes", "type": "Dungeon", "lo": 13, "hi": 18, "location": "Inside Ironforge"},
            {"name": "Deadmines", "type": "Dungeon", "lo": 15, "hi": 23, "location": "Moonbrook, Westfall"},
            {"name": "Barrow Deeps", "type": "Raid", "lo": 60, "hi": 60, "location": "Mount Hyjal"}]
    assert dungeons_for_level(inst, 16).text == (
        "Na 16 poziom pasują: Hall of Thanes (13–18, Inside Ironforge), Ragefire Chasm (13–18) i "
        "Deadmines (15–23, Westfall).")
    assert "Scarlet Monastery Graveyard (26–36)" in dungeons_for_level(inst, 30).text
    assert dungeons_for_level(inst, 5).text.startswith("Na 5 poziom jeszcze nic nie pasuje")


# --------------------------------------------------------------------------- need or greed

NG = P.LFLAG_NEED | P.LFLAG_GREED


def item(**kw):
    base = dict(roll_id=1, flags=NG, quality=2, item_level=24, min_level=19, class_id=4, subclass_id=2,
                equip_loc="INVTYPE_HAND", name="Gloves of the Monkey", stats={"agi": 6, "sta": 4, "armor": 62})
    base.update(kw)
    return P.LootItem(**base)


def test_need_for_an_upgrade_of_your_class():
    worn = [P.WornItem(15, 2, "Rough Leather Gloves", {"armor": 40, "agi": 1})]
    adv = advise(item(equipped=worn), class_id=4, level=22)
    assert adv.verdict == "need"
    assert adv.text == ("Gloves of the Monkey, rękawice, skóra. Dla łotrzyka to ulepszenie: plus 5 zręczności i plus 4 "
                        "wytrzymałości względem tego, co nosisz. Bierz need.")
    assert advise(item(), 4, 22).verdict == "need"  # an empty slot


def test_greed_when_the_class_cannot_use_it_or_gains_nothing():
    assert advise(item(subclass_id=3, name="Scaled Hauberk", equip_loc="INVTYPE_CHEST", stats={"str": 5}),
                  class_id=8, level=20).text.endswith("kolczuga jest dla ciebie za ciężka. Daj greed.")
    robe = item(subclass_id=1, equip_loc="INVTYPE_ROBE", name="Robe of Power", stats={"int": 8, "spi": 5})
    assert advise(robe, class_id=1, level=22).verdict == "greed"  # nothing a warrior uses
    dagger = item(class_id=2, subclass_id=15, equip_loc="INVTYPE_WEAPON", name="Thief Dagger", stats={"dps": 9.0, "agi": 3},
                  equipped=[P.WornItem(22, 10, "Staff", {"int": 5, "spi": 4, "dps": 10})])
    assert advise(dagger, class_id=5, level=21).text == (
        "Thief Dagger, sztylet. Słabsze od twojego (minus 5 intelektu i minus 4 ducha). Daj greed.")
    assert advise(item(class_id=2, subclass_id=1, equip_loc="INVTYPE_2HWEAPON"), class_id=8, level=20).verdict == "greed"
    assert advise(item(class_id=9, equip_loc="", name="Pattern: Boots"), 4, 20).text.startswith("Pattern: Boots. To przepis")


def test_armor_and_ranged_rules():
    assert (best_armor(1, 39), best_armor(1, 40), best_armor(3, 40), best_armor(8, 60)) == (3, 4, 3, 1)
    bow = item(class_id=2, subclass_id=2, equip_loc="INVTYPE_RANGED", name="Venomstrike", stats={"dps": 18.5},
               equipped=[P.WornItem(25, 3, "Old Gun", {"dps": 12.1})])
    assert advise(bow, class_id=3, level=30).text == (
        "Venomstrike, łuk. Dla łowcy to ulepszenie: 6,4 DPS więcej względem tego, co nosisz. Bierz need.")


# --------------------------------------------------------------------------- reminders and session

EVENING = datetime(2026, 11, 9, 20, 0, 0).timestamp()


@pytest.mark.parametrize("text,when,message", [
    ("przypomnij mi za 10 minut, że mam wyjąć pranie", "za 10 min", "Przypominam, że masz wyjąć pranie."),
    ("Przypomnij mi za pół godziny żebym kupił strzały", "za 30 min", "Przypominam, żebyś kupił strzały."),
    ("przypomnij mi za godzinę i 15 minut że muszę iść spać", "za 1 godz. 15 min", "Przypominam, że musisz iść spać."),
    ("za kwadrans przypomnij mi żeby zjeść", "za 15 min", "Przypominam, żeby zjeść."),
    ("przypomnij mi za dwadzieścia pięć minut o pizzy", "za 25 min", "Przypominam o pizzy."),
    ("przypomnij mi o 21:30 o rajdzie", "o 21:30", "Przypominam o rajdzie."),
    ("przypomnij mi o dwudziestej pierwszej trzydzieści że mam raid", "o 21:30", "Przypominam, że masz raid."),
    ("przypomnij mi o 9 że mam ogarnąć bank", "o 21:00", "Przypominam, że masz ogarnąć bank."),
    ("przypomnij mi na 20 poziomie, żeby kupić nowe skille", "na 20 poziomie", "Przypominam, żeby kupić nowe skille."),
    ("przypomnij mi jak wbiję 30 level że mogę kupić mounta", "na 30 poziomie", "Przypominam, że możesz kupić mounta."),
])
def test_reminders_understand_spoken_polish(text, when, message):
    r = parse_reminder(text, EVENING)
    assert r.when_text == when and say_reminder(r.message) == message


def test_reminders_fire_once_and_survive_a_restart(tmp_path):
    fired = []
    clock = [EVENING]
    path = tmp_path / "reminders.json"
    rem = Reminders(path, fired.append, clock=lambda: clock[0])
    rem.add(parse_reminder("przypomnij mi za 10 minut że mam pranie", EVENING))
    rem.add(parse_reminder("przypomnij mi na 20 poziomie o trenerze", EVENING))
    assert parse_reminder("przypomnij mi coś", EVENING) is None
    again = Reminders(path, fired.append, clock=lambda: clock[0])  # restart: loaded from disk
    assert [r.level for r in again.pending()] == [0, 20]
    assert again.tick(EVENING + 599) == [] and len(again.tick(EVENING + 601)) == 1
    assert again.tick(EVENING + 700) == [] and fired[0].message == "że masz pranie"
    assert again.on_level(19) == [] and len(again.on_level(20)) == 1
    assert again.pending() == [] and Reminders(path, fired.append).pending() == []
    again.add(parse_reminder("przypomnij mi za godzinę o czymś", EVENING))
    assert len(again.cancel("all")) == 1 and again.pending() == []


def st(level, xp, xp_max=10000, gold=0, sc=0):
    return P.GameState(level=level, xp=xp, xp_max=xp_max, gold=gold, silver_copper=sc)


def test_session_counts_xp_across_level_ups_and_skips_breaks():
    s = SessionStats()
    s.on_state(st(10, 8000, gold=1), now=0)
    s.on_state(st(10, 9000, gold=1, sc=5000), now=200)
    s.on_state(st(11, 500, 12000, gold=2), now=400)  # +1000 to the end of the bar, +500 into the next one
    s.on_state(st(11, 500, 12000, gold=2), now=400 + 3600)  # a long break: not active time
    s.on_state(st(11, 2300, 12000, gold=2), now=400 + 3600 + 200)
    summary = s.summary(st(11, 2300, 12000))
    assert summary.xp_gained == 1000 + 1000 + 500 + 1800 and summary.levels == 1
    assert summary.active_s == 600 and summary.money_delta == 10000
    assert round(summary.xp_per_h) == 25800 and round(summary.eta_s / 60) == 23
    text = s.polish(st(11, 2300, 12000))
    assert text.startswith("Gramy 10 min. Zdobyłeś 4 300 doświadczenia, około 25 800 na godzinę, i wbiłeś 1 poziom.")
    assert "Na plusie 1 złota, czyli 6 złota na godzinę." in text and "następny poziom za 23 min" in text
    s.reset()
    assert s.polish().startswith("Sesja dopiero się zaczęła")


# --------------------------------------------------------------------------- quest chains, places, notes

def test_chains_follow_next_and_single_prerequisites():
    from wvt.quest_tips import Chains, assess, effective_xp

    w = world()
    ch = Chains(w)
    assert ch.chain(1).ids == [1, 2] and ch.chain(2).pos == 1  # quest 2 needs quest 1 (its only prerequisite)
    a = assess(w, ch, 1, level=12, xp_max=10000, class_id=8)
    assert a.chain.length == 2 and "To 1. z 2 questów w łańcuchu" in " ".join(a.facts)
    assert effective_xp(1000, 10, 15) == 1000 and effective_xp(1000, 10, 17) == 600 and effective_xp(1000, 10, 40) == 100


def test_place_reminders_and_notes_match_cities_and_auction_houses(tmp_path):
    from wvt.reminders import parse_note, place_matches

    assert parse_reminder("przypomnij mi w Ironforge żeby wystawić miecz na AH").place == "Ironforge"
    assert parse_reminder("przypomnij mi jak będę w Orgrimmarze o banku").place == "Orgrimmar"
    assert parse_reminder("przypomnij mi przy aukcji żeby sprzedać skóry").place == "ah"
    assert parse_reminder("przypomnij mi w mieście żeby naprawić zbroję").place == "city"
    assert parse_reminder("przypomnij mi w Westfall o Hoggerze", places=["Westfall"]).place == "Westfall"
    note = parse_note("zanotuj, że chcę wystawić miecz na AH")
    assert (note.place, note.keep, note.message) == ("ah", True, "chcesz wystawić miecz na AH")
    assert parse_note("zapisz sobie, że muszę kupić strzały").place == ""
    assert place_matches("ah", "Stranglethorn Vale", "Booty Bay") and not place_matches("city", "Westfall")
    fired = []
    clock = [1000.0]
    rem = Reminders(tmp_path / "r.json", fired.append, clock=lambda: clock[0])
    rem.add(note)
    rem.add(parse_reminder("przypomnij mi w Westfall o Hoggerze", places=["Westfall"]))
    assert rem.on_place("Ironforge") and rem.on_place("Ironforge") == []  # a note: not again at once
    clock[0] += 1801
    assert rem.on_place("Orgrimmar") and rem.on_place("Westfall")[0].message == "o Hoggerze"
    assert [r.keep for r in rem.pending(notes=None)] == [True]  # the place reminder is gone, the note stays
    assert rem.cancel("all") == [] and len(rem.done(["miecz"])) == 1
