"""Knowledge base: the safe Lua reader, QuestieDB merging, wiki HTML, chunks, hybrid search, world data."""

import numpy as np
import pytest

from wvt import protocol as P
from wvt.kb.docs import chunk_sections, extract_instances, quest_text, wiki_docs
from wvt.kb.luadata import LuaError, Parser, Unresolved, tokenize
from wvt.kb.questie_tables import _apply, _corrections, _Resolver, _table_after, load_addon_lists
from wvt.kb.store import KnowledgeBase, write_kb
from wvt.kb.wiki import html_to_sections
from wvt.kb.world import World, Player, build_world, faction_of_mask, pretty_zone


# --------------------------------------------------------------------------- Lua literals, never executed

def parse(src, resolve=None):
    toks = tokenize(src)
    p = Parser(toks, resolve)
    return p.expr()


def test_lua_literals_strings_numbers_and_tables():
    assert parse('{1, 2.5, -3, 0x10, "a\\tb", [[long\nstring]], nil, true}') == \
        {1: 1, 2: 2.5, 3: -3, 4: 16, 5: "a\tb", 6: "long\nstring", 8: True}
    assert parse("{nil, {424005}}") == {2: {1: 424005}}  # nil holes keep positions
    assert parse("{name = 'x', ['k'] = 1, [5] = {x = 1}}") == {"name": "x", "k": 1, 5: {"x": 1}}
    # decimal escapes are bytes of UTF-8 text; raw UTF-8 works too (files are read as latin-1)
    assert parse('"Zo\\195\\171"') == "Zoë"
    assert parse("'ZoÃ«'") == "Zoë"
    assert parse("--[[ comment ]] 5 -- trailing") == 5


def test_lua_names_go_through_the_resolver_and_calls_are_refused():
    consts = {"raceIDs": {"HUMAN": 1, "GNOME": 64}}

    def resolve(parts):
        try:
            return consts[parts[0]][parts[1]]
        except KeyError:
            raise Unresolved(".".join(parts))

    assert parse("raceIDs.HUMAN + raceIDs.GNOME", resolve) == 65
    with pytest.raises(Unresolved):
        parse("raceIDs.TROLL", resolve)
    with pytest.raises(LuaError):
        parse("os.execute('rm -rf /')")  # a call: never evaluated
    with pytest.raises(LuaError):
        parse("function() end")


def test_bad_entries_and_fields_are_skipped_not_fatal():
    src = "{ [1] = {'ok'}, [2] = {'bad', f(x)}, [3] = function() local a, b = 1, 2 end, [4] = {'fine'} }"
    toks = tokenize(src)
    p = Parser(toks, None, 1)
    out = p.entities(records=True)
    assert out[1] == {1: "ok"} and out[4] == {1: "fine"}
    assert out[2] == {1: "bad"}  # only the broken field is lost
    assert 3 not in out and p.skipped == 2


# --------------------------------------------------------------------------- QuestieDB corrections

KEYS = {"Quest": {"name": 1, "startedBy": 2, "finishedBy": 3, "requiredLevel": 4, "questLevel": 5,
                  "requiredRaces": 6},
        "Npc": {"name": 1, "spawns": 7, "questStarts": 10}, "Object": {"name": 1}, "Item": {"name": 1}}


def test_corrections_set_add_and_remove_like_the_addon(tmp_path):
    corr = tmp_path / "fix.lua"
    corr.write_text("""
local X = QuestieLoader:CreateModule("X")
function X:Load()
    local questKeys = QuestieDB.questKeys
    local raceIDs = QuestieDB.raceKeys
    return {
        [10] = { -- comment
            [questKeys.requiredLevel] = 12,
            [questKeys.requiredRaces] = raceIDs.HUMAN + raceIDs.SKYBORNE_ALLIANCE,
            [questKeys.startedBy_add] = {nil, {424005}},
            [questKeys.finishedBy_remove] = {{2}},
            [questKeys.name] = Questie.ICON_TYPE_EVENT,
        },
        [11] = { [questKeys.name] = "New Forever quest", [questKeys.questLevel] = 7 },
    }
end
""", encoding="utf-8")
    data = {10: {1: "Old", 2: {1: {1: 7}}, 3: {1: {1: 1, 2: 2}}, 4: 5}}
    resolver = _Resolver(KEYS, {})
    table, skipped, _ = _corrections(corr, resolver)
    assert skipped == 1  # the unknown icon constant
    _apply(data, table, KEYS["Quest"], "Quest")
    q = data[10]
    assert q[1] == "Old" and q[4] == 12 and q[6] == 1 + (1 << 32)
    assert q[2] == {1: {1: 7}, 2: {1: 424005}}  # a starter object added next to the NPC
    assert q[3] == {1: {1: 1}}  # finisher 2 removed
    assert data[11] == {1: "New Forever quest", 5: 7}


def test_spawn_maps_merge_per_zone():
    data = {5: {7: {12: {1: {1: 10.0, 2: 20.0}}}}}
    _apply(data, {5: {(7, "add"): {12: {1: {1: 30.0, 2: 40.0}}, 40: {1: {1: 1.0, 2: 2.0}}}}}, KEYS["Npc"], "Npc")
    assert data[5][7] == {12: {1: {1: 10.0, 2: 20.0}, 2: {1: 30.0, 2: 40.0}}, 40: {1: {1: 1.0, 2: 2.0}}}


def test_long_string_tables_and_the_addon_blacklist(tmp_path):
    toks = tokenize("QuestieDB.questData = [[return {\n[2] = {'A', nil, nil, 1, 2},\n}]]")
    from wvt.kb.luadata import NAME, OP
    assert _table_after(toks, [(NAME, "QuestieDB"), (OP, "."), (NAME, "questData"), (OP, "=")]) == \
        {2: {1: "A", 4: 1, 5: 2}}
    root = tmp_path / "questie"
    (root / "Database/Corrections/Holidays/quests").mkdir(parents=True)
    (root / "Database/Corrections/QuestieQuestBlacklist.lua").write_text("""
local HIDE_ON_MAP = "HIDE_ON_MAP"
function QuestieQuestBlacklist:Load()
    local questsToBlacklist = {
        [7462] = true, [2358] = Expansions.Current >= Expansions.Wotlk, [78124] = HIDE_ON_MAP, [13212] = false,
    }
    return questsToBlacklist
end""", encoding="utf-8")
    (root / "Database/Corrections/Holidays/quests/LunarFestival.lua").write_text(
        'tinsert(eventQuests, {"Lunar Festival", 8619}) -- Morndeep\ntinsert(eventQuests, {"Lunar Festival", 8635})',
        encoding="utf-8")
    black, events = load_addon_lists(root)
    assert black == {7462, 78124} and events == {8619, 8635}


# --------------------------------------------------------------------------- wiki HTML -> text

HTML = """<div class="mw-parser-output"><div class="hatnote">For the retail dungeon, see X.</div>
<table class="infobox"><tr><th>Type</th><td>Dungeon</td></tr>
<tr><th>Classic lvl</th><td>15-23</td></tr><tr><th>Location</th><td>Moonbrook, Westfall</td></tr></table>
<p>The <b>Deadmines</b> lie under Westfall.<sup class="reference">[1]</sup></p>
<div class="mw-heading mw-heading2"><h2 id="Bosses">Bosses</h2></div>
<ul><li>Rhahk'Zor</li><li>Edwin VanCleef</li></ul>
<div class="mw-heading mw-heading3"><h3>Edwin</h3></div><p>He hides on the ship.</p>
<table class="navbox"><tr><td>Navigation junk</td></tr></table>
<div class="mw-heading mw-heading2"><h2>References</h2></div><p>ignored</p>
<div class="mw-heading mw-heading2"><h2>Progress</h2></div><p>Hello, how are you, traveller?</p></div>"""


def test_wiki_html_becomes_sections_with_tables_as_rows():
    secs = html_to_sections(HTML)
    assert secs[0] == ("", "Type | Dungeon\nClassic lvl | 15-23\nLocation | Moonbrook, Westfall\n"
                           "The Deadmines lie under Westfall.")
    assert ("Bosses", "- Rhahk'Zor\n- Edwin VanCleef") in secs
    assert ("Bosses > Edwin", "He hides on the ship.") in secs
    text = "\n".join(t for _, t in secs)
    assert "Navigation" not in text and "ignored" not in text and "retail" not in text and "[1]" not in text
    page = {"title": "Deadmines (Classic)", "html": HTML, "url": "u", "categories": ["Classic"]}
    doc = next(wiki_docs([page]))
    assert all(not p.startswith("Progress") for p, _ in doc["sections"])  # quest dialogue is dropped
    assert extract_instances([page]) == [{"name": "Deadmines", "type": "Dungeon", "lo": 15, "hi": 23,
                                          "location": "Moonbrook, Westfall", "page": "Deadmines (Classic)"}]


def test_chunks_stay_within_sections_and_size():
    long = "\n".join(f"Line {i} " + "word " * 30 for i in range(40))
    chunks = list(chunk_sections([("A", long), ("B", "short but long enough to be kept as text"), ("C", "tiny")]))
    assert {p for p, _ in chunks} == {"A", "B"}
    assert all(len(t) <= 1000 for _, t in chunks) and len([c for c in chunks if c[0] == "A"]) > 3


# --------------------------------------------------------------------------- hybrid search

class KeywordEmbedder:
    """Deterministic stand-in for e5: one dimension per keyword, Polish and English share them."""
    VOCAB = [("hogger", "hoggera"), ("deadmines", "kopalnie"), ("mage", "maga", "mag"), ("frostbolt",),
             ("skyborne",), ("dragon", "smok")]

    def embed(self, texts, prefix="passage: ", batch=32, progress=None):
        out = np.zeros((len(texts), len(self.VOCAB) + 1), dtype=np.float32)
        for i, t in enumerate(texts):
            low = t.lower()
            for j, words in enumerate(self.VOCAB):
                out[i, j] = sum(low.count(w) for w in words)
            out[i, -1] = 0.3
            out[i] /= np.linalg.norm(out[i])
        return out


def test_hybrid_search_finds_by_meaning_and_by_rare_words(tmp_path):
    docs = [
        {"source": "wiki", "title": "Hogger", "kind": "wiki", "sections": [("", "Hogger is an elite gnoll in Elwynn.")]},
        {"source": "wiki", "title": "Deadmines (Classic)", "kind": "wiki",
         "sections": [("Bosses", "Edwin VanCleef waits on the ship in the Deadmines.")]},
        {"source": "wiki", "title": "Mage abilities (Forever)", "kind": "forever",
         "sections": [("Abilities", "Frostbolt | 4 | Trainer\nPolymorph | 8 | Trainer")]},
        {"source": "guide", "title": "moje notatki", "kind": "guide", "sections": [("", "Mage: Frostbolt first.")]},
    ]
    db, vec = tmp_path / "kb.sqlite", tmp_path / "vectors.npy"
    info = write_kb(docs, KeywordEmbedder(), db, vec)
    assert info["chunks"] == 4 and info["docs"] == 4
    kb = KnowledgeBase(db, vec, embedder=KeywordEmbedder())
    assert kb.search("gdzie jest Hoggera?", "where is Hogger?")[0].title == "Hogger"
    hits = kb.search("czego uczy trener maga", "what does the mage trainer teach Frostbolt")
    assert hits[0].kind == "guide" and hits[1].title == "Mage abilities (Forever)"  # own notes first, then Forever
    # BM25 alone finds a rare word the vectors do not know
    assert kb.search("kim jest VanCleef", "who is VanCleef")[0].title == "Deadmines (Classic)"
    assert [h.kind for h in kb.search("mag", "mage", kinds={"forever"})] == ["forever"]


# --------------------------------------------------------------------------- world data

def tables():
    qk = {"name": 1, "startedBy": 2, "finishedBy": 3, "requiredLevel": 4, "questLevel": 5, "requiredRaces": 6,
          "requiredClasses": 7, "objectivesText": 8, "objectives": 10, "preQuestGroup": 12, "preQuestSingle": 13,
          "exclusiveTo": 16, "zoneOrSort": 17, "nextQuestInChain": 22, "specialFlags": 24}
    nk = {"name": 1, "minLevel": 4, "maxLevel": 5, "spawns": 7, "zoneID": 9, "friendlyToFaction": 13, "subName": 14,
          "npcFlags": 15}
    quests = {
        1: {1: "Report to Gryan", 2: {1: {1: 100}}, 3: {1: {1: 101}}, 4: 10, 5: 12, 6: 1 + 4, 17: 40},
        2: {1: "The Defias Brotherhood", 2: {1: {1: 101}}, 3: {1: {1: 101}}, 4: 12, 5: 14, 13: {1: 1}, 17: 40,
            10: {1: {1: {1: 102}}}, 8: {1: "Kill the Defias."}},
        3: {1: "Horde Errand", 2: {1: {1: 103}}, 4: 10, 5: 12, 6: 2, 17: 40},
        4: {1: "Mage Lesson", 2: {1: {1: 100}}, 4: 10, 5: 12, 7: 128, 17: 40},
        5: {1: "<NYI> Old", 2: {1: {1: 100}}, 4: 1, 5: 12, 17: 40},
        6: {1: "Daily Fish", 2: {1: {1: 100}}, 4: 10, 5: 12, 24: 1, 17: 40},
    }
    npcs = {
        100: {1: "Gryan's Aide", 4: 20, 5: 20, 7: {40: {1: {1: 56.0, 2: 47.0}}}, 13: "A", 9: 40},
        101: {1: "Gryan Stoutmantle", 4: 25, 5: 25, 7: {40: {1: {1: 56.3, 2: 47.5}}}, 13: "A", 9: 40},
        102: {1: "Defias Pillager", 4: 14, 5: 15, 7: {40: {1: {1: 45.0, 2: 70.0}}}, 9: 40},
        103: {1: "Grunt", 7: {40: {1: {1: 30.0, 2: 30.0}}}, 13: "H"},
        104: {1: "Thor", 7: {40: {1: {1: 56.5, 2: 52.6}}}, 13: "A", 14: "Gryphon Master", 15: 8},
    }
    return {"keys": {"Quest": qk, "Npc": nk, "Object": {"name": 1}, "Item": {"name": 1}},
            "data": {"Quest": quests, "Npc": npcs, "Object": {}, "Item": {}},
            "enums": {"zoneIDs": {"WESTFALL": 40, "UN_GORO_CRATER": 490}}}


def world():
    return World(build_world(tables(), {"area_to_map": {40: 1436}, "dungeons": {}, "xp": {2: (14, 1200)}}))


def test_world_keeps_real_quests_and_the_npcs_they_need():
    w = world()
    assert sorted(w.quests) == [1, 2, 3, 4, 6]  # "<NYI>" quests dropped
    assert w.quests[2]["kill"] == [[102, None]] and w.quests[2]["xp"] == 1200
    assert 104 in w.npcs  # a flight master is kept as a service
    assert w.area_name(40) == "Westfall" and pretty_zone("UN_GORO_CRATER") == "Un'Goro Crater"
    assert faction_of_mask(1 + 4) == "A" and faction_of_mask(2) == "H" and faction_of_mask(0) == ""
    assert "Starts at: Gryan Stoutmantle - Westfall (56, 48)" in quest_text(w, 2)


def test_quest_availability_follows_questie_rules():
    w = world()
    human_mage = Player(level=12, race_id=1, class_id=8, faction="A", done=frozenset(), done_known=True)
    avail = {qid for qid, q in w.quests.items() if w.available(q, qid, human_mage, set())}
    assert avail == {1, 4}  # 2 needs quest 1 first, 3 is Horde, 6 is repeatable
    done1 = Player(level=12, race_id=1, class_id=8, faction="A", done=frozenset({1}), done_known=True)
    assert {qid for qid, q in w.quests.items() if w.available(q, qid, done1, set())} == {2, 4}
    warrior = Player(level=12, race_id=1, class_id=1, faction="A", done=frozenset(), done_known=True)
    assert not w.available(w.quests[4], 4, warrior, set())  # the mage-only quest
    assert not w.available(w.quests[1], 1, human_mage, {1})  # already in the log


def test_spots_measure_yards_on_the_players_map():
    w = world()
    pos = P.PlayerPos(map_id=1436, x=0.56, y=0.47, width=3500, height=2333)
    s = w.nearest("npc", 101, pos)
    assert s.ui_map == 1436 and s.waypoint == (1436, 56.3, 47.5) and abs(s.distance - 15.7) < 0.1
    assert w.nearest("npc", 101, P.PlayerPos(map_id=1429, x=0.5, y=0.5, width=1, height=1)).distance is None
    assert w.find_npcs("gryan stoutmantle") == [101] and w.find_npcs("Stoutmantle") == [101]


# --------------------------------------------------------------------------- Polish quest texts

class FakeTranslatingLLM:
    """Translates by a table; drops a guarded name once to test the retry."""

    def __init__(self):
        self.calls = 0

    def chat(self, messages, max_tokens=200, temperature=0.0, stop=None, timeout=None):
        self.calls += 1
        table = {"Report to Gryan": "Zamelduj się u Gryana",
                 "Kill the Defias.": "Zabij Defiasów.",
                 "Bring the «Huge Claw» to «Marshal Dughan» at the «Fargodeep Mine».":
                     "Przynieś «Huge Claw» do «Marshal Dughan» przy «Fargodeep Mine»."}
        lines = messages[-1]["content"].splitlines()
        out = []
        for ln in lines:
            num, text = ln.split(". ", 1)
            pl = table.get(text, text)
            if "Huge Claw" in text and len(lines) > 1:
                pl = "Przynieś Wielki Pazur do «Marshal Dughan» przy «Fargodeep Mine»."  # a lost name
            out.append(f"{num}. {pl}")
        return "\n".join(out), {}


def test_quest_translation_keeps_wow_names_and_is_cached(tmp_path):
    from wvt.kb.translate_pl import NameGuard, QuestTranslator, guards_kept, unguard

    g = NameGuard(["Marshal Dughan", "Huge Claw"])
    src = g.protect_caps(g.protect("Bring the Huge Claw to Marshal Dughan at the Fargodeep Mine."))
    assert src == "Bring the «Huge Claw» to «Marshal Dughan» at the «Fargodeep Mine»."
    assert g.protect_caps("Kill 10 Defias Bandits. The Brotherhood must fall.") == \
        "Kill 10 «Defias Bandits». The «Brotherhood» must fall."
    assert not guards_kept(src, "Przynieś Wielki Pazur do «Marshal Dughan».") and unguard("«A» b") == "A b"
    llm = FakeTranslatingLLM()
    tr = QuestTranslator(llm, tmp_path / "pl.json", workers=1, batch=8)
    out = tr.translate({"t:1": "Report to Gryan", "o:1": "Kill the Defias.", "o:2": src})
    assert out == {"t:1": "Zamelduj się u Gryana", "o:1": "Zabij Defiasów.",
                   "o:2": "Przynieś Huge Claw do Marshal Dughan przy Fargodeep Mine."}  # retried alone
    calls = llm.calls
    again = QuestTranslator(llm, tmp_path / "pl.json").translate({"t:1": "Report to Gryan"})
    assert again == {"t:1": "Zamelduj się u Gryana"} and llm.calls == calls  # from the cache


def test_polish_quest_text_has_a_gloss_and_english_names():
    from wvt.kb.docs import quest_docs, quest_text_pl
    from wvt.kb.translate_pl import sha

    w = world()
    pl = {"t:" + sha("The Defias Brotherhood"): "Bractwo Defiasów", "o:" + sha("Kill the Defias."): "Zabij Defiasów."}
    text = quest_text_pl(w, 2, pl)
    assert text.startswith("Quest „The Defias Brotherhood” (po polsku: Bractwo Defiasów) – poziom 14, dostępny od 12;")
    assert "Zaczyna się u: Gryan Stoutmantle – Westfall (56, 48)." in text and "Cel: Zabij Defiasów." in text
    assert "Najpierw zrób: Report to Gryan." in text and "Doświadczenie: 1200 XP." in text
    doc = next(d for d in quest_docs(w, pl) if d["ref"] == 2)
    assert [s for s, _ in doc["sections"]] == ["", "po polsku"]
