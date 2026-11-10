"""What to do now (wvt/planner.py): the route through your quests, what each is worth, what to let go."""

from wvt import protocol as P
from wvt.kb.world import Player, World
from wvt.planner import make_plan
from wvt.quests import QuestBook


def plan_world():
    return World({
        "quests": {
            "384": {"n": "Beer Basted Boar Ribs", "lvl": 7, "xp": 630, "sn": [1267], "en": [1267],
                    "txt": ["Ragnar Thunderbrew in Kharanos wants 6 Crag Boar Ribs and a mug of Rhapsody Malt."]},
            "183": {"n": "The Boar Hunter", "lvl": 8, "xp": 450, "next": 184},
            "184": {"n": "The Boar Hunter II", "lvl": 9, "xp": 600, "rw": [5000]},
            "50": {"n": "Old Errand", "lvl": 2, "xp": 40},
            "60": {"n": "Troll Elites", "lvl": 9, "xp": 700},
            "70": {"n": "Far Away", "lvl": 8, "xp": 500},
        },
        "npcs": {"1267": {"n": "Ragnar Thunderbrew", "f": "A", "sp": {"1": [[52.0, 50.0]]}}},
        "items": {"5000": {"n": "Boarhide Belt", "c": 4, "sc": 2, "il": 12}},
        "zones": {"names": {"1": "Dun Morogh"}, "map": {"1": 1426}},
    })


def quest(qid, title, level, flags=P.QFLAG_POI, x=0.0, y=0.0, objectives=(), group=0, poi_map=1426, index=1):
    return P.Packet(type=P.TYPE_QUEST, seq=qid, quest=P.QuestEntry(
        sync=1, index=index, total=5, quest_id=qid, level=level, flags=flags, group=group, poi_map=poi_map,
        poi_x=x, poi_y=y, title=title, zone="Dun Morogh", objectives=list(objectives)))


def book():
    b = QuestBook()
    b.on_packet(P.Packet(type=P.TYPE_POS, seq=1, pos=P.PlayerPos(1426, 0.50, 0.50, 4000, 3000)))
    b.on_packet(quest(384, "Beer Basted Boar Ribs", 7, P.QFLAG_POI | P.QFLAG_COMPLETE, 0.52, 0.50, index=1))
    b.on_packet(quest(183, "The Boar Hunter", 8, x=0.60, y=0.50, objectives=[(False, "4/12 Small Crag Boar")],
                      index=2))
    b.on_packet(quest(50, "Old Errand", 2, x=0.51, y=0.51, index=3))
    b.on_packet(quest(60, "Troll Elites", 9, x=0.55, y=0.40, group=3, index=4))
    b.on_packet(quest(70, "Far Away", 8, P.QFLAG_POI | P.QFLAG_POI_OTHER_MAP, 0.5, 0.5, poi_map=1432, index=5))
    return b


def test_the_route_turns_in_first_and_lets_go_of_what_is_not_worth_it():
    plan = make_plan(plan_world(), book(), Player(level=10, class_id=1, faction="A"), book().pos, xp_max=7000)
    assert [s.title for s in plan.route] == ["Beer Basted Boar Ribs", "The Boar Hunter"]
    first, second = plan.route
    assert first.kind == "turnin" and first.who == "Ragnar Thunderbrew" and first.line == "/wvt track 384"
    assert first.where == "około 80 jardów na wschód" and second.hop == "około 320 jardów na wschód"
    assert second.left == "Small Crag Boar 4 z 12" and "1. z 2 questów w łańcuchu" in " ".join(second.facts)
    skipped = {s.title: why for s, why in plan.skip}
    assert skipped["Old Errand"].startswith("jest już szary")
    assert skipped["Troll Elites"] == "to quest na grupę 3 osób, a grasz solo"
    assert [s.title for s in plan.later] == ["Far Away"]
    assert plan.text() == ("Najpierw oddaj Beer Basted Boar Ribs u Ragnar Thunderbrew, około 80 jardów na wschód, "
                           "na koniec The Boar Hunter (Small Crag Boar 4 z 12), około 320 jardów na wschód. Odpuść "
                           "Old Errand: jest już szary, poniżej 1% poziomu. Odpuść Troll Elites: to quest na grupę 3 "
                           "osób, a grasz solo. Na później: Far Away (Dun Morogh).")
    facts = plan.facts()
    assert facts.startswith("Gracz: poziom 10, gra solo.\nTrasa (kolejność najkrótszego chodzenia")
    assert "2. The Boar Hunter (Small Crag Boar 4 z 12); od gracza około 400 jardów na wschód, od poprzedniego " \
           "punktu około 320 jardów na wschód. Ocena:" in facts
    assert "Odpuść: Troll Elites – to quest na grupę 3 osób, a grasz solo." in facts


def test_in_a_group_the_group_quest_is_on_the_route():
    plan = make_plan(plan_world(), book(), Player(level=10, class_id=1, faction="A"), book().pos, xp_max=7000,
                     in_group=True)
    assert "Troll Elites" in [s.title for s in plan.route]


def test_without_world_data_the_markers_still_make_a_route():
    plan = make_plan(None, book(), Player(level=10), book().pos)
    assert [s.title for s in plan.route][:2] == ["Beer Basted Boar Ribs", "Old Errand"]
    assert plan.route[0].verdict == "" and not plan.empty
    assert make_plan(None, QuestBook(), Player(level=10), None).empty
