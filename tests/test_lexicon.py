"""The names of the moment (wvt/lexicon.py): correcting misheard names, the entries taken from the game,
Whisper's prompt, the vector search and the facts for the LLM."""

import time

import numpy as np
import pytest

from tests.test_advisor import item_world
from wvt import protocol as P
from wvt.assistant import ChatLog
from wvt.bags import BagBook, parse_line
from wvt.lexicon import Lexicon, item_words, similarity, sound_key, strip_ending
from wvt.quests import QuestBook


class Cfg(dict):
    def get(self, dotted, default=None):
        return super().get(dotted, default)


TERMS = ["Beer Basted Boar Ribs", "Senir's Observations", "Tools for Steelgrill", "Frostmane Hold", "Innkeeper Belm",
         "Grelin Whitebeard", "Senir Whitebeard", "Talin Keeneye", "Pilot Bellowfiz", "Rockjaw Trogg",
         "Frostmane Seer", "Crag Boar Rib", "Gnarled Short Staff", "Kharanos", "Gnomeregan", "Vagash", "Thargas",
         "Elandra", "Morgrim"]


@pytest.fixture
def lex(tmp_path):
    f = tmp_path / "slownik.txt"
    f.write_text("# moje słowa\n" + "\n".join(TERMS) + "\nThargas = mój kumpel z gildii\n", encoding="utf-8")
    return Lexicon(Cfg(), user_file=f)


def test_polish_spellings_of_english_names_sound_the_same():
    assert sound_key(strip_ending("Targasa")) == sound_key("Thargas")
    assert sound_key("Kinaj") == sound_key("Keeneye")
    assert sound_key(strip_ending("Wagasza")) == sound_key("Vagash")
    assert sound_key("gnome") + sound_key("Regan") == sound_key("Gnomeregan")
    assert sound_key("Karanos") == sound_key("Kharanos", english=True)
    assert sound_key("Cuesta") == sound_key("questa")
    assert similarity(sound_key("Belofiz"), sound_key("Bellowfiz")) > 0.9


# what Whisper really wrote in the bench (56 Polish lines read by two ElevenLabs voices, no prompt)
@pytest.mark.parametrize("heard, fixed", [
    ("Jak zrobić Cuesta? Beer Bastard Bore Ribs.", "Jak zrobić questa? Beer Basted Boar Ribs."),
    ("Gdzie mam oddać Seniors Observations?", "Gdzie mam oddać Senir's Observations?"),
    ("Czy warto robić Tools for Steel Grill?", "Czy warto robić Tools for Steelgrill?"),
    ("Ile crackbore ribów mi jeszcze brakuje?", "Ile Crag Boar Rib mi jeszcze brakuje?"),
    ("Zaproś Targasa i Elandrę do party.", "Zaproś Thargas i Elandra do party."),
    ("Odpisz Morgrimowi, że zaraz będę.", "Odpisz Morgrim, że zaraz będę."),
    ("Gdzie jest wejście do gnome Regan?", "Gdzie jest wejście do Gnomeregan?"),
    ("Gdzie jest wejście do Gnomery Gun?", "Gdzie jest wejście do Gnomeregan?"),
    ("Gdzie są Frostmine Siri do Cuesta?", "Gdzie są Frostmane Seer do questa?"),
    ("Gdzie znajdę pilota Beloufiza?", "Gdzie znajdę Pilot Bellowfiz?"),
    ("Uważajcie na wagaża. Bije mocno.", "Uważajcie na Vagash. Bije mocno."),
    ("Czekam przy Talinie Kinaj w Haranos.", "Czekam przy Talin Keeneye w Kharanos."),
    ("Czekam przy talinie Kinaj w Karanos.", "Czekam przy Talin Keeneye w Kharanos."),
    ("Targas, weź jeszcze jednego Wendigo.", "Thargas, weź jeszcze jednego Wendigo."),
])
def test_misheard_names_are_put_right(lex, heard, fixed):
    c = lex.correct(heard)
    assert c.text == fixed and c.changed


@pytest.mark.parametrize("text", [
    "Siedzę na tarasie i piję kawę.", "Mam kwestię do omówienia.", "Targowisko jest zamknięte.",
    "Wagary były fajne.", "Ragnarok to dobry film.", "Granica jest blisko.", "Gdzie jest najbliższy trener?",
    "Było w kracie. Oboje poszli do domu.", "Belka spadła z dachu.", "Kino było super, a potem burger.",
])
def test_ordinary_polish_stays_as_it_was(lex, text):
    c = lex.correct(text)
    assert c.text == text and not c.fixes


def test_names_spelled_right_are_found_but_left_alone(lex):
    c = lex.correct("Gdzie stoi Innkeeper Belm w Kharanos?")
    assert c.text == "Gdzie stoi Innkeeper Belm w Kharanos?" and not c.changed
    assert {e.name for e in c.found} == {"Innkeeper Belm", "Kharanos"}


def test_chat_lines_need_a_closer_match_than_questions_to_iskra(lex):
    heard = "Ile Cragbor Rebo mi jeszcze brakuje?"
    assert lex.correct(heard).text == "Ile Crag Boar Rib mi jeszcze brakuje?"
    assert lex.correct(heard, strict=True).text == heard


def test_correction_can_be_switched_off(tmp_path):
    f = tmp_path / "s.txt"
    f.write_text("Thargas", encoding="utf-8")
    off = Lexicon(Cfg({"lexicon.correct": False}), user_file=f)
    assert off.correct("Zaproś Targasa").text == "Zaproś Targasa"
    assert Lexicon(Cfg({"lexicon.enabled": False}), user_file=f).whisper_prompt() == ""


# --------------------------------------------------------------------------- entries from the game

def game():
    world = item_world()
    world.quests[384]["txt"] = ["Ragnar Thunderbrew in Kharanos wants 6 Crag Boar Ribs and a mug of Rhapsody Malt."]
    book = QuestBook()
    book.on_packet(P.Packet(type=P.TYPE_QUEST, seq=1, quest=P.QuestEntry(
        sync=1, index=1, total=1, quest_id=384, level=7, title="Beer Basted Boar Ribs", zone="Dun Morogh",
        objectives=[(False, "2/6 Crag Boar Rib"), (False, "0/1 Rhapsody Malt")])))
    book.on_packet(P.Packet(type=P.TYPE_POS, seq=2, pos=P.PlayerPos(1426, 0.47, 0.52, 4000, 2700)))
    chat = ChatLog()
    chat.add_packet(P.Packet(type=P.TYPE_CHAT, seq=1, chat_kind=10, sender="Thargas-Realm", text="inv pls"))
    chat.add_packet(P.Packet(type=P.TYPE_CHAT, seq=2, chat_kind=20, sender="Ragnar Thunderbrew",
                             text="Ask Innkeeper Belm for a mug of Rhapsody Malt."))
    chat.add_packet(P.Packet(type=P.TYPE_CHAT, seq=3, chat_kind=24, flags=P.VFLAG_SELF, sender="Mati",
                             text="You receive loot: [Gnarled Short Staff]."))
    bags = BagBook()
    bags.on_packet(P.Packet(type=P.TYPE_CHAT, seq=4, chat_kind=P.KIND_BAG, race="7:1/1",
                            text="2589\t12\t1\t7\t5\t0\tLinen Cloth\n3300\t2\t0\t15\t0\t0\tBroken Tooth"))
    staff = P.LootItem(flags=P.LFLAG_PICKED, quality=1, class_id=2, subclass_id=10, equip_loc="INVTYPE_2HWEAPON",
                       name="Gnarled Short Staff", stats={"int": 2})
    state = P.GameState(level=7, zone="Dun Morogh", subzone="Kharanos", members=["Brunhilda-Realm"], name="Mati")
    lex = Lexicon(Cfg({"lexicon.user_file": ""}), chat_log=chat, state_getter=lambda: state,
                  quests_getter=lambda: book, world_getter=lambda: world, bags_getter=lambda: bags,
                  picked_getter=lambda: [(time.time() - 120, staff)])
    return lex


def test_the_entries_come_from_your_quests_people_places_and_items():
    lex = game()
    by = {e.name: e for e in lex.entries()}
    assert (by["Beer Basted Boar Ribs"].kind, by["Beer Basted Boar Ribs"].source) == ("quest", "questlog")
    assert "Ragnar Thunderbrew in Kharanos wants" in by["Beer Basted Boar Ribs"].info
    assert (by["Crag Boar Rib"].kind, by["Crag Boar Rib"].source) == ("item", "objective")
    assert (by["Rhapsody Malt"].source, by["Kharanos"].kind) == ("objective", "place")
    assert (by["Thargas"].kind, by["Brunhilda"].source) == ("player", "party")
    assert (by["Ragnar Thunderbrew"].kind, by["Ragnar Thunderbrew"].source) == ("npc", "dialog")
    assert (by["Gnarled Short Staff"].source, by["Linen Cloth"].source) == ("loot", "bag")
    assert "kostur, kij, laska" in by["Gnarled Short Staff"].info and "masz w torbach 12" in by["Linen Cloth"].info
    assert by["Innkeeper Belm"].kind == "npc"  # named by Ragnar, standing near you
    assert (by["Crag Boar"].kind, by["Crag Boar"].source) == ("mob", "nearby")
    assert by["Broken Tooth"].weight < by["Linen Cloth"].weight  # grey junk matters less


def test_whispers_prompt_has_the_most_important_names_last():
    lex = game()
    prompt = lex.whisper_prompt()
    assert prompt.startswith("Rozmowa w World of Warcraft. ") and prompt.endswith("Beer Basted Boar Ribs.")
    assert "Thargas" in prompt and "Gnarled Short Staff" in prompt and "questa" not in prompt
    assert lex.whisper_prompt(max_terms=3).count(",") == 2


def test_the_translator_and_iskra_get_the_names_and_facts():
    lex = game()
    c = lex.correct("Zaproś Targasa na Beer Bastard Bore Ribs")
    assert c.text == "Zaproś Thargas na Beer Basted Boar Ribs"
    names = lex.translation_names(c)
    assert names[:2] == ["Thargas", "Beer Basted Boar Ribs"] and "Kharanos" in names
    facts = lex.facts()
    assert "Questy w dzienniku: Beer Basted Boar Ribs (2/6 Crag Boar Rib, 0/1 Rhapsody Malt)" in facts
    assert "Ostatnie łupy: Gnarled Short Staff" in facts and "Thargas" in facts


class WordEmbedder:
    """A tiny stand-in for e5: one dimension per meaning, so the test knows what should win."""

    MEANINGS = (("kij", "kostur", "staff"), ("piw", "malt", "ale"), ("dzik", "boar"), ("materia", "cloth"))

    def __init__(self):
        self.embedded = 0

    def _vec(self, text):
        t = text.lower()
        v = np.array([sum(t.count(w) for w in m) for m in self.MEANINGS] + [0.1], dtype=np.float32)
        return v / np.linalg.norm(v)

    def embed(self, texts, prefix="passage: "):
        self.embedded += len(texts)
        return np.stack([self._vec(t) for t in texts])

    def query(self, text):
        return self._vec(text)


def test_vector_search_finds_what_a_description_means():
    lex = game()
    emb = WordEmbedder()
    lex.embedder_getter = lambda: emb
    hits = lex.search("czy ten kij, który znalazłem, jest dobry?", k=2, kinds={"item"}, sources={"loot", "bag"})
    assert hits[0][0].name == "Gnarled Short Staff"
    assert lex.search("ile mam materiału?", k=1, kinds={"item"})[0][0].name == "Linen Cloth"
    lex.search("a ten kij?", k=1)
    first = emb.embedded
    lex.search("a ten dzik?", k=1)
    assert emb.embedded == first  # every entry is embedded once, then only the questions
    lex.embedder_getter = lambda: None
    assert lex.search("kij") == []  # without the guides' model: no vector search, nothing breaks


def test_bag_lines_are_put_together_from_their_parts():
    book = BagBook()
    part2 = P.Packet(type=P.TYPE_CHAT, seq=2, chat_kind=P.KIND_BAG, race="3:2/2", text="2894\t1\t1\t0\t0\t0\tRhapsody Malt")
    part1 = P.Packet(type=P.TYPE_CHAT, seq=1, chat_kind=P.KIND_BAG, race="3:1/2",
                     text="2589\t12\t1\t7\t5\t0\tLinen Cloth\n15000\t1\t2\t4\t2\t11\tGloves of the Monkey")
    assert book.on_packet(part2) and not book.known  # half a list is not the bags yet
    assert book.on_packet(part1) and book.known
    assert [(i.name, i.count) for i in book.items] == [("Linen Cloth", 12), ("Gloves of the Monkey", 1),
                                                       ("Rhapsody Malt", 1)]
    assert book.items[1].equip_loc == "INVTYPE_HAND" and book.count("linen cloth") == 12
    newer = P.Packet(type=P.TYPE_CHAT, seq=3, chat_kind=P.KIND_BAG, race="4:1/1", text="")
    assert book.on_packet(newer) and book.items == []  # emptied bags
    assert not book.on_packet(P.Packet(type=P.TYPE_CHAT, seq=4, chat_kind=10, text="hi"))
    assert parse_line("bad line") is None and parse_line("1\t2\t3") is None
    log = ChatLog()
    log.add_packet(part1)
    assert log.recent(10, kinds=("BAG",)) == []  # never part of the chat


def test_item_words_name_the_kind_in_polish():
    assert item_words(2, 10, "INVTYPE_2HWEAPON", 2) == "kostur, kij, laska; zielony, niezwykły"
    assert item_words(4, 2, "INVTYPE_HAND") == "rękawice; skóra"
    assert item_words(0, 0) == "jedzenie, picie, mikstura, eliksir"


# --------------------------------------------------------------------------- push-to-talk and the translator

def test_push_to_talk_gives_whisper_the_names_and_fixes_them_before_translating(tmp_path):
    import types

    from wvt.pipeline import PushToTalkPipeline

    f = tmp_path / "slownik.txt"
    f.write_text("Thargas\nGnomeregan", encoding="utf-8")

    class Stt:
        prompt_ok = True

        def __init__(self):
            self.prompts = []

        def transcribe(self, clip, prompt=""):
            self.prompts.append(prompt)
            return "Zaproś Targasa do Gnome Regan"

    class Translator:
        def __init__(self):
            self.calls = []

        def translate(self, text, direction, context=None, names=None):
            self.calls.append((text, names))
            return types.SimpleNamespace(text="Inviting Thargas to Gnomeregan")

        def remember(self, *a):
            pass

    class UI:
        def __init__(self):
            self.lines = []

        def status(self, kind, title, lines=None, timeout=0.0):
            self.lines += lines or []

        def result(self, item):
            pass

    cfg = Cfg({"audio": {}, "channels.party": "/p"})
    stt, tr, ui = Stt(), Translator(), UI()
    inserter = types.SimpleNamespace(insert=lambda line, fg: "inserted")
    pipe = PushToTalkPipeline(cfg, None, stt, tr, inserter, ui, lambda: True)
    pipe.lexicon = Lexicon(cfg, user_file=f)
    audio = (np.sin(2 * np.pi * 200 * np.arange(16000) / 16000) * 0.3).astype(np.float32)
    item = pipe.process_audio(audio, "party")
    assert stt.prompts[0].startswith("Rozmowa w World of Warcraft.") and "Thargas" in stt.prompts[0]
    text, names = tr.calls[0]
    assert text == "Zaproś Thargas do Gnomeregan" and "Gnomeregan" in names
    assert item.polish == text and item.chat_line == "/p Inviting Thargas to Gnomeregan"
    assert "usłyszane: Targasa → Thargas, Gnome Regan → Gnomeregan" in ui.lines


def test_the_translator_is_told_the_names_of_the_moment():
    from wvt.translator import Translator

    class LLM:
        def __init__(self):
            self.msgs = []

        def chat(self, messages, max_tokens=0, temperature=0.0):
            self.msgs.append(messages)
            return "Inviting Thargas", {}

    llm = LLM()
    tr = Translator(llm, {})
    assert tr.translate("Zaproś Targasa", "pl-en", names=["Thargas", "Kharanos"]).text == "Inviting Thargas"
    system = llm.msgs[-1][0]["content"]
    assert "Names in the player's game right now" in system and system.endswith("Thargas, Kharanos")
    tr.translate("Zaproś Targasa", "pl-en", names=["Thargas", "Kharanos"])
    assert len(llm.msgs) == 1  # the same line with the same names: from the cache
    tr.translate("Zaproś Targasa", "pl-en")
    assert len(llm.msgs) == 2 and "Names in the player's game" not in llm.msgs[-1][0]["content"]
    assert "Names in the player's game" not in tr.build_messages("hi", "en-pl", names=["Thargas"])[0]["content"]


class RemoteEmbedder(WordEmbedder):
    """Stands in for Google's vectors: own model name, one call for the question and new entries."""

    model_name = "gemini-embedding-2"

    def __init__(self, fail=False):
        super().__init__()
        self.fail = fail
        self.calls = 0

    def available(self):
        return True

    def doc_text(self, title, text):
        return f"title: {title} | text: {text}"

    def embed_with_query(self, docs, text):
        self.calls += 1
        if self.fail:
            raise RuntimeError("no network")
        return self.embed(docs), self.query(text)

    def query(self, text):
        if self.fail:
            raise RuntimeError("no network")
        return super().query(text)


def test_google_vectors_first_and_only_their_scores_are_trusted():
    lex = game()
    local, remote = WordEmbedder(), RemoteEmbedder()
    lex.embedder_getter, lex.remote = lambda: local, remote
    hits = lex.search("czy ten kij jest lepszy?", k=1, kinds={"item"}, sources={"loot", "bag"})
    assert hits[0][0].name == "Gnarled Short Staff" and lex.last_backend == "remote" and remote.calls == 1
    assert lex.trusted(hits[0][1], "item") and not lex.trusted(0.6, "item") and not lex.trusted(0.68, "quest")
    assert local.embedded == 0  # the local model was not needed
    lex.remote = RemoteEmbedder(fail=True)  # no network: the local e5 answers, but nobody acts on its score alone
    hits = lex.search("czy ten kij jest lepszy?", k=1, kinds={"item"}, sources={"loot", "bag"})
    assert hits[0][0].name == "Gnarled Short Staff" and lex.last_backend == "local"
    assert not lex.trusted(hits[0][1], "item")
    assert {m for m, _ in lex._vec_cache} == {"gemini-embedding-2", "e5"}  # each model keeps its own vectors
