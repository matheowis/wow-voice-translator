"""Voice assistant: intents from Polish speech, invite requests in chat, group commands, answers."""

import time

import pytest

from wvt import protocol as P
from wvt.assistant import Assistant, ChatLog, READ_KINDS, clean_reply, is_invite_request, match_name, parse_intent


@pytest.mark.parametrize("text,kind", [
    ("ej weź ich wysapraszaj", "invite_requests"),  # the user's own example
    ("Zaproś ich wszystkich", "invite_requests"),
    ("zinwajtuj tych co piszą inv", "invite_requests"),
    ("zaproś Artasa", "invite_name"),
    ("weź go doproś do party", "invite_last"),  # a user's bug report
    ("możesz go zaprosić?", "invite_last"),
    ("doproś Artasa", "invite_name"),
    ("wciągnij ich do grupy", "invite_requests"),
    ("doprowadź mnie do trenera", "waypoint"),  # "dopro..." but not an invite
    ("wyrzuć Bolka", "kick"),
    ("wyproś go z grupy", "kick"),
    ("daj lidera Jainie", "lead"),
    ("zrób raid", "raid"),
    ("opuść grupę", "leave"),
    ("sprawdź gotowość", "ready"),
    ("kto chce inv?", "who_invite"),
    ("co mi napisał?", "whispers"),
    ("odpisz mu że zaraz będę", "message"),
    ("streść czat", "summary"),
    ("co oni piszą?", "summary"),
    ("przeczytaj czat", "summary"),
    ("co piszą na trade?", "summary"),
    ("co słychać na gildii", "summary"),
    ("kto szuka grupy?", "summary"),
    ("co mi wpadło?", "loot"),
    ("co podniosłem", "loot"),
    ("ile mam złota?", "gold"),
    ("ile mi brakuje do levelu", "xp"),
    ("gdzie jestem?", "where"),
    ("ile mam miejsca w torbach", "bags"),
    ("kto jest w party?", "members"),
    ("wycisz głosy", "mute"),
    ("wyłącz głosy na say", "voice_say"),
    ("przełącz na polski", "lang_pl"),
    ("cicho", "stop"),
    ("co umiesz?", "help"),
    ("jak się masz piękna?", "chat"),
])
def test_polish_requests_become_intents(text, kind):
    assert parse_intent(text).kind == kind


@pytest.mark.parametrize("text", [
    "Czy mogę solo zrobić Deadmines na 18 levelu?", "czy znasz polskich graczy?",
    "kto jest liderem?", "kup mi torbę", "jaki jest najlepszy expowisko", "idziemy do bagna",
])
def test_ordinary_questions_are_not_mistaken_for_commands(text):
    assert parse_intent(text).kind == "chat"


def test_messages_keep_the_spoken_words_and_find_the_addressee():
    it = parse_intent("napisz do Bolka że czekam w Goldshire")
    assert (it.channel, it.spoken, it.message) == ("whisper", "bolka", "czekam w Goldshire")
    it = parse_intent("napisz na party że idę po wodę")
    assert (it.channel, it.message) == ("party", "idę po wodę")
    it = parse_intent("odpisz mu że zaraz będę")
    assert (it.channel, it.spoken, it.message) == ("whisper", "", "zaraz będę")


def test_spoken_polish_names_match_characters():
    names = ["Arthas", "Jaina-Kazzak", "Thrall", "Bolek", "Zoë"]
    assert match_name("Artasa", names) == "Arthas"
    assert match_name("Jainie", names) == "Jaina-Kazzak"
    assert match_name("Tralla", names) == "Thrall"
    assert match_name("Bolkowi", names) == "Bolek"
    assert match_name("Mateusz", names) is None


@pytest.mark.parametrize("text,kind,expected", [
    ("inv me", "SAY", True), ("inv pls", "YELL", True), ("can i get an invite?", "SAY", True),
    ("inv to party pls", "SAY", True), ("+", "WHISPER", True), ("1", "WHISPER", True),
    ("+", "SAY", False), ("LFM SM need heal", "SAY", False), ("selling invite to guild bank tab", "SAY", False),
])
def test_invite_requests_in_chat(text, kind, expected):
    assert is_invite_request(text, kind) is expected


# --------------------------------------------------------------------------- the assistant with fakes

class FakeInserter:
    def __init__(self):
        self.lines = []

    def insert(self, line, wow_is_foreground):
        self.lines.append(line)
        return "inserted"


class FakeUI:
    def __init__(self):
        self.shown = []

    def status(self, kind, title, lines=None, timeout=0.0):
        self.shown.append((kind, title, lines or []))


class FakeVoice:
    def __init__(self):
        self.said = []
        self.silenced = 0

    def say(self, text, voice_id, lang="pl", gain=1.0, speaker="assistant"):
        self.said.append((text, voice_id, lang))
        return True

    def silence(self):
        self.silenced += 1


class Result:
    def __init__(self, text):
        self.text = text


class FakeTranslator:
    def translate(self, text, direction):
        table = {"zaraz będę": "I'll be right there", "Hello, need a hand?": "Cześć, potrzebujesz pomocy?"}
        return Result(table.get(text, f"<{direction}:{text}>"))


class FakeLLM:
    def __init__(self, answer="Jasne, przystojniaku. Zawsze do usług."):
        self.answer = answer
        self.calls = []

    def chat(self, messages, max_tokens=120, temperature=0.0, stop=None, timeout=None):
        self.calls.append(messages)
        return self.answer, {}


class Cfg(dict):
    def get(self, dotted, default=None):
        return super().get(dotted, default)


def make_assistant(voice_id="voice-iskra", **kw):
    cfg = Cfg({"assistant.name": "Iskra", "assistant.voice_id": voice_id, "assistant.invite_window_s": 300,
               "assistant.flavor": False,  # one fixed phrasing, no LLM for spontaneous comments
               "lexicon.user_file": ""})  # never the player's own data/slownik.txt
    ins, ui, voice, llm = FakeInserter(), FakeUI(), FakeVoice(), FakeLLM()
    a = Assistant(cfg, FakeTranslator(), llm, ins, ui, lambda: True, voice_getter=lambda: voice, **kw)
    return a, ins, ui, voice, llm


def chat_pkt(sender, text, kind=1, self_=False):
    flags = P.VFLAG_SELF if self_ else 0
    return P.Packet(type=P.TYPE_CHAT, seq=0, flags=flags, chat_kind=kind, sender=sender, text=text)


def state_pkt(**kw):
    st = P.GameState(**kw)
    return P.Packet(type=P.TYPE_STATE, seq=1, flags=st.flags, state=st)


def test_invites_everybody_who_asked_up_to_the_free_party_slots():
    a, ins, ui, voice, _ = make_assistant()
    a.on_packet(state_pkt(flags=P.SFLAG_GROUP | P.SFLAG_LEADER, group_size=2, name="Mati", members=["Arthas"]))
    for sender, text in [("Ann", "inv me"), ("Bob-Kazzak", "inv pls"), ("Arthas", "inv"), ("Cid", "lfm sm"),
                         ("Dan", "can i get an invite?"), ("Eve", "inv me"), ("Ann", "inv me pls"), ("Mati", "inv")]:
        a.on_packet(chat_pkt(sender, text, self_=sender == "Mati"))
    a.on_packet(chat_pkt("Fay", "inv", kind=10))  # a whisper
    reply, line = a.handle("ej weź ich wysapraszaj")
    assert line == "/wvt inv Ann Bob-Kazzak Dan"  # 3 free slots; Arthas is already in, Cid never asked
    assert ins.lines == [line]
    assert "Eve i Fay się nie zmieści" in reply
    assert voice.said[-1] == (reply, "voice-iskra", "pl")
    # asking again does not invite the same people twice
    _, line2 = a.handle("zaproś ich")
    assert line2 == "/wvt inv Eve Fay"


def test_old_requests_are_forgotten():
    a, ins, *_ = make_assistant()
    a.chat.add_packet(chat_pkt("Ann", "inv me"), now=time.time() - 400)
    reply, line = a.handle("zaproś chętnych")
    assert line == "" and "Nikt nie prosił" in reply


def test_him_is_who_asked_for_an_invite_or_spoke_last():
    a, *_ = make_assistant()
    reply, line = a.handle("weź go doproś do party")
    assert line == "" and "Kogo" in reply  # nobody wrote anything yet
    a.on_packet(chat_pkt("Ann", "hi there"))
    a.on_packet(chat_pkt("Bob", "nice weather", kind=10))  # a whisper
    reply, line = a.handle("weź go doproś do party")
    assert line == "/wvt inv Bob" and "Zapraszam Bob" in reply
    a.on_packet(chat_pkt("Cid", "inv me"))
    a.on_packet(chat_pkt("Dan", "lol"))
    assert a.handle("możesz go zaprosić?")[1] == "/wvt inv Cid"  # who asked beats who spoke
    assert a.handle("weź go do party")[1] == "/wvt inv Dan"  # Bob and Cid are invited already


def test_named_invite_kick_and_lead_check_the_group():
    a, ins, ui, voice, _ = make_assistant()
    a.on_packet(chat_pkt("Arthas", "anyone for SM?"))
    _, line = a.handle("zaproś Artasa")
    assert line == "/wvt inv Arthas"
    a.on_packet(state_pkt(flags=P.SFLAG_GROUP, group_size=3, members=["Arthas", "Jaina-Kazzak"]))
    reply, line = a.handle("wyrzuć Jainę")
    assert line == "" and "liderem" in reply  # not the leader
    a.on_packet(state_pkt(flags=P.SFLAG_GROUP | P.SFLAG_LEADER | P.SFLAG_RESTRICTED, group_size=3,
                          members=["Arthas", "Jaina-Kazzak"]))
    reply, line = a.handle("wyrzuć Jainę")
    assert line == "" and "walce" in reply
    a.on_packet(state_pkt(flags=P.SFLAG_GROUP | P.SFLAG_LEADER, group_size=3, members=["Arthas", "Jaina-Kazzak"]))
    assert a.handle("wyrzuć Jainę")[1] == "/wvt kick Jaina-Kazzak"
    assert a.handle("daj lidera Artasowi")[1] == "/wvt lead Arthas"
    assert a.handle("zrób raid")[1] == "/wvt raid"


def test_whispers_are_read_in_polish_and_answered_in_english():
    a, ins, ui, voice, _ = make_assistant()
    a.on_packet(chat_pkt("Bob-Kazzak", "Hello, need a hand?", kind=10))
    reply, _ = a.handle("co mi napisał?")
    assert reply == "Bob pisze: Cześć, potrzebujesz pomocy?."
    reply, line = a.handle("odpisz mu że zaraz będę")
    assert line == "/w Bob-Kazzak I'll be right there"
    assert "Bob" in reply


def test_game_facts_come_from_the_state_packet():
    a, *_ = make_assistant()
    assert "Nie widzę danych z gry" in a.handle("ile mam złota?")[0]
    a.on_packet(state_pkt(level=23, xp=30000, xp_max=40000, gold=123, silver_copper=4567, bag_free=11, bag_slots=64,
                          zone="Elwynn Forest", subzone="Goldshire"))
    assert a.handle("ile mam złota?")[0] == "Masz 123 złota, 45 srebra i 67 miedzi."
    assert "75 procent" in a.handle("ile do levelu?")[0] and "10000" in a.handle("ile do levelu?")[0]
    assert a.handle("gdzie jestem?")[0] == "Jesteś w Elwynn Forest, dokładnie Goldshire."
    assert a.handle("ile mam miejsca w torbach?")[0] == "Masz 11 wolnych miejsc na 64."


def test_small_talk_goes_to_the_llm_in_character_and_is_spoken():
    a, ins, ui, voice, llm = make_assistant()
    reply, line = a.handle("jak się masz piękna?")
    assert reply == "Jasne, przystojniaku. Zawsze do usług." and line == ""
    system = llm.calls[0][0]["content"]
    assert "Iskra" in system and "po polsku" in system
    a.handle("a co robisz wieczorem?")
    from wvt.assistant import STYLE_SHOTS

    assert len(llm.calls[1]) == 1 + 2 * len(STYLE_SHOTS) + 2 + 1  # system, style, previous exchange, question
    assert voice.said[-1][1] == "voice-iskra"


def test_without_a_chosen_voice_she_only_shows_text():
    a, ins, ui, voice, _ = make_assistant(voice_id="")
    a.handle("co umiesz?")
    assert voice.said == [] and ui.shown and "Iskra:" in ui.shown[-1][2][0]


def test_replies_are_cleaned_for_speech():
    assert clean_reply('„**Hej!** 😀 Jestem tu."') == "Hej! Jestem tu."
    long = "Pierwsze zdanie jest całkiem długie i ma sens. " * 8
    out = clean_reply(long, 120)
    assert len(out) <= 120 and out.endswith(".")


def test_chat_log_keeps_whisper_partners_and_names():
    log = ChatLog()
    log.add_packet(chat_pkt("Bob-Kazzak", "hi", kind=10))
    log.add_packet(chat_pkt("Ann", "you there?", kind=11, self_=True))  # you whispered Ann
    assert log.last_whisper_partner() == "Ann"
    assert log.names() == ["Ann", "Bob-Kazzak"]


@pytest.mark.parametrize("text,on", [
    ("włącz głosy na say", True), ("włącz sej", True), ("czytaj say", True), ("niech czyta say", True),
    ("włącz czytanie", True), ("czytaj mi co mówią ludzie obok", True), ("wyłącz say", False),
    ("wyłącz czytanie say", False),
])
def test_say_reading_is_switched_by_voice(text, on):
    it = parse_intent(text)
    assert (it.kind, it.on) == ("voice_say", on)


# --------------------------------------------------------------------------- quests

class QuestTranslator(FakeTranslator):
    EN = {"ile mi zostało świec?": "how many candles do I have left?",
          "gdzie są te wątroby?": "where are those livers?",
          "gdzie znajdę kask?": "where can I find a helmet?"}

    def translate(self, text, direction):
        return Result(self.EN.get(text, "")) if direction == "pl-en" else super().translate(text, direction)


def quest_pkt(**kw):
    return P.Packet(type=P.TYPE_QUEST, seq=0, quest=P.QuestEntry(**kw))


def quest_assistant():
    a, ins, ui, voice, llm = make_assistant()
    a.translator = QuestTranslator()
    a.on_packet(P.Packet(type=P.TYPE_POS, seq=1, pos=P.PlayerPos(37, 0.40, 0.70, 3470, 2314)))
    poi = P.QFLAG_POI
    a.on_packet(quest_pkt(sync=1, index=1, total=3, quest_id=101, level=8, flags=P.QFLAG_TRACKED | poi, poi_map=37,
                          poi_x=0.45, poi_y=0.60, title="The Fargodeep Mine", zone="Elwynn Forest",
                          objectives=[(False, "3/8 Kobold Candle")]))
    a.on_packet(quest_pkt(sync=1, index=2, total=3, quest_id=102, level=9, flags=P.QFLAG_COMPLETE | poi, poi_map=37,
                          poi_x=0.30, poi_y=0.70, title="Bounty on Murlocs", zone="Elwynn Forest",
                          objectives=[(True, "8/8 Murloc Fin")]))
    a.on_packet(quest_pkt(sync=1, index=3, total=3, quest_id=103, level=10, flags=poi | P.QFLAG_POI_OTHER_MAP,
                          poi_map=52, poi_x=0.5, poi_y=0.25, title="Westfall Stew", zone="Westfall",
                          objectives=[(False, "0/5 Goretusk Liver")]))
    return a, llm


def test_quest_log_answers_come_from_the_game():
    a, llm = quest_assistant()
    assert a.handle("co mam do zrobienia?")[0] == (
        "Masz 3 questy. Do oddania: Bounty on Murlocs. W toku: The Fargodeep Mine: Kobold Candle 3 z 8; "
        "Westfall Stew: Goretusk Liver 0 z 5.")
    assert a.handle("ile mi zostało świec?")[0] == "The Fargodeep Mine: Kobold Candle 3 z 8."
    # the tracked quest's marker: 173 yd east and 231 yd north of the player
    assert a.handle("gdzie mam iść?")[0] == "The Fargodeep Mine: około 290 jardów na północny wschód."
    assert a.handle("gdzie są te wątroby?")[0] == "Westfall Stew: cel jest w innej strefie: Westfall."
    assert a.handle("co mogę oddać?")[0] == (
        "Do oddania masz: Bounty on Murlocs. Najbliżej oddasz Bounty on Murlocs, około 350 jardów na zachód.")
    assert a.handle("który quest jest najbliżej?")[0] == "Najbliżej masz The Fargodeep Mine, około 290 jardów na północny wschód."
    assert llm.calls == []  # all of that without the LLM


def test_free_quest_questions_get_the_log_as_facts_and_other_places_go_to_chat():
    a, llm = quest_assistant()
    a.handle("o co chodzi w tym queście?")
    system = llm.calls[-1][0]["content"]
    assert "The Fargodeep Mine" in system and "Kobold Candle 3 z 8" in system and "jedyne fakty" in system
    a.handle("gdzie znajdę kask?")  # not in the quest log: an ordinary chat answer
    assert "Dziennik zadań" not in llm.calls[-1][0]["content"]


def test_without_a_quest_log_she_says_so_and_a_new_log_replaces_the_old_one():
    a, *_ = make_assistant()
    assert "Nie widzę jeszcze twojego dziennika" in a.handle("co mam do zrobienia?")[0]
    a.on_packet(quest_pkt(sync=1, index=1, total=2, quest_id=1, title="Old One"))
    a.on_packet(quest_pkt(sync=1, index=2, total=2, quest_id=2, title="Kept One"))
    a.on_packet(quest_pkt(sync=2, index=1, total=2, quest_id=2, title="Kept One"))
    assert sorted(q.title for q in a.quests.quests()) == ["Kept One", "Old One"]  # the new list is still arriving
    a.on_packet(quest_pkt(sync=2, index=2, total=2, quest_id=3, title="New One"))
    assert [q.title for q in a.quests.quests()] == ["Kept One", "New One"]
    a.on_packet(quest_pkt(sync=3, index=0, total=0))
    assert a.handle("jakie mam questy?")[0].startswith("Twój dziennik zadań jest pusty")


# --------------------------------------------------------------------------- where next, reminders, loot, guides

@pytest.mark.parametrize("text,kind", [
    ("przypomnij mi za 10 minut że mam pranie", "reminder_add"),
    ("jakie mam przypomnienia?", "reminder_list"),
    ("usuń przypomnienia", "reminder_cancel"),
    ("ile expa robię na godzinę?", "stats"),
    ("kiedy wbiję następny poziom?", "stats"),
    ("zresetuj statystyki", "stats_reset"),
    ("brać need czy greed?", "loot"),
    ("czy to dla mnie?", "loot"),
    ("postaw znacznik na trenera", "waypoint"),
    ("zaznacz to na mapie", "waypoint"),
    ("zaprowadź mnie do Hoggera", "waypoint"),
    ("usuń znacznik", "waypoint_clear"),
    ("gdzie mam się teraz udać?", "where_next"),
    ("co mam teraz robić?", "where_next"),
    ("gdzie najlepiej expić na moim poziomie?", "where_next"),
    ("jaki dungeon na mój poziom?", "dungeon_level"),
    ("gdzie jest najbliższy karczmarz?", "find"),
    ("gdzie jest trener pierwszej pomocy?", "find"),
    ("gdzie jest wejście do Deadmines?", "find"),
    ("pomoc", "help"),
])
def test_new_requests_become_intents(text, kind):
    assert parse_intent(text).kind == kind


def kb_assistant():
    from tests.test_kb import world

    a, ins, ui, voice, llm = make_assistant()
    w = world()
    a.world_getter = lambda: w
    a.on_packet(state_pkt(level=12, xp=100, xp_max=1000, name="Mati"))
    a.on_packet(P.Packet(type=P.TYPE_PLAYER, seq=1, player=P.PlayerInfo(8, 1, P.FACTION_ALLIANCE, 2)))
    a.on_packet(P.Packet(type=P.TYPE_DONE, seq=2, done=P.DoneChunk(1, 1, 1, [])))
    a.on_packet(P.Packet(type=P.TYPE_POS, seq=3, pos=P.PlayerPos(1436, 0.56, 0.47, 3500, 2333)))
    a.on_packet(quest_pkt(sync=1, index=0, total=0))
    return a, ins, ui, voice, llm


def test_where_next_and_places_prepare_a_waypoint():
    a, ins, ui, voice, llm = kb_assistant()
    reply, line = a.handle("gdzie mam się teraz udać?")  # a plan: her LLM says it, from the computed route
    assert reply == llm.answer + " Nie bierz Mage Lesson od Gryan's Aide: mało expa i nic ciekawego na końcu. " \
        "Znacznik czeka na Enter."  # what to let go is said even when her LLM leaves it out
    assert "Trasa" in llm.calls[-1][0]["content"] and "weź u Gryan's Aide" in llm.calls[-1][0]["content"]
    assert line == "/wvt way 1436 56.0 47.0 Gryan's Aide" and ins.lines[-1] == line
    a.llm = None  # without a model: the plan in plain words
    reply, line = a.handle("gdzie mam się teraz udać?")
    assert reply.startswith("Najpierw weź u Gryan's Aide:") and line == "/wvt way 1436 56.0 47.0 Gryan's Aide"
    reply, line = a.handle("gdzie jest najbliższy mistrz lotów?")
    assert reply.startswith("Najbliżej masz Thor") and line.endswith("Thor")
    reply, line = a.handle("zaznacz to na mapie")  # "it": the last place she named
    assert line.endswith("Thor") and reply == "Stawiam znacznik, Enter."
    assert a.handle("usuń znacznik") == ("Zdejmuję znacznik, Enter.", "/wvt way clear")
    a.cfg["assistant.auto_waypoint"] = False
    assert a.handle("gdzie jest najbliższy mistrz lotów?")[1] == ""


def test_reminders_session_and_loot_are_spoken():
    a, ins, ui, voice, llm = kb_assistant()
    reply, _ = a.handle("przypomnij mi na 13 poziomie, żeby kupić nowe czary")
    assert reply == "Dobra, przypomnę ci na 13 poziomie, żeby kupić nowe czary."
    assert a.handle("jakie mam przypomnienia?")[0] == "Masz 1: na 13 poziomie: żeby kupić nowe czary."
    a.on_packet(state_pkt(level=13, xp=50, xp_max=1200, name="Mati"))
    a._events.submit(lambda: None).result()  # her level-up comment runs off the reader thread
    said = [t for t, *_ in voice.said]
    assert "Przypominam, żeby kupić nowe czary." in said and a.reminders.pending() == []
    assert said[-1] == "Poziom 13! Brawo, rośniesz w oczach."
    assert a.handle("ile robię expa na godzinę?")[0].startswith("Sesja dopiero się zaczęła")
    gloves = P.LootItem(roll_id=9, flags=P.LFLAG_NEED | P.LFLAG_GREED, quality=2, class_id=4, subclass_id=1,
                        equip_loc="INVTYPE_HAND", name="Silk Gloves", stats={"int": 5, "spi": 3})
    a.on_packet(P.Packet(type=P.TYPE_LOOT, seq=1, loot=gloves))
    assert voice.said[-1][0] == ("Silk Gloves, rękawice, materiał. Nic tam nie nosisz, więc dla maga to czysty zysk. "
                                 "Bierz need.")
    a.on_packet(P.Packet(type=P.TYPE_LOOT, seq=2, loot=gloves))  # the same roll again: said once
    assert sum(1 for t, *_ in voice.said if t.startswith("Silk Gloves")) == 1
    assert a.handle("brać need czy greed?")[0].startswith("Silk Gloves")


class FakeKB:
    def __init__(self, cosine):
        self.cosine = cosine
        self.queries = []

    def search(self, query, query_en="", k=5, kinds=None):
        from wvt.kb.store import Hit

        self.queries.append((query, query_en))
        return [Hit(1, "Deadmines (Classic)", "Bosses", "Edwin VanCleef waits on the ship.", "", "wiki", None, 1.0,
                    self.cosine)]


class GuideTranslator(FakeTranslator):
    EN = {"jak pokonać Edwina VanCleefa?": "How to defeat Edwin VanCleef?", "powiedz mi coś miłego": "tell me something nice"}

    def translate(self, text, direction):
        return Result(self.EN.get(text, "")) if direction == "pl-en" else super().translate(text, direction)


def test_game_questions_are_answered_from_the_guides_and_small_talk_is_not():
    a, ins, ui, voice, llm = make_assistant()
    a.translator = GuideTranslator()
    kb = FakeKB(0.84)
    a.kb_getter = lambda: kb
    a.handle("jak pokonać Edwina VanCleefa?")
    system = llm.calls[-1][0]["content"]
    assert "Edwin VanCleef waits on the ship" in system and "wyłącznie na podstawie" in system
    a.handle("powiedz mi coś miłego")  # no game word, no name from the game: her usual small talk
    assert "Fragmenty" not in llm.calls[-1][0]["content"]
    a.kb_getter = lambda: FakeKB(0.70)  # nothing close in the base
    a.handle("jak pokonać Edwina VanCleefa?")
    assert "Fragmenty" not in llm.calls[-1][0]["content"]


# --------------------------------------------------------------------------- notes, places, events, quest tips

def flush(a):
    a._events.submit(lambda: None).result()


def test_notes_remind_in_a_city_with_an_auction_house():
    a, ins, ui, voice, llm = kb_assistant()
    reply, _ = a.handle("zanotuj, że chcę wystawić miecz na AH")
    assert reply == "Zanotowane. Przypomnę ci przy domu aukcyjnym."
    assert a.handle("jakie mam notatki?")[0] == "Masz 1: przy domu aukcyjnym: chcesz wystawić miecz na AH."
    a.on_packet(state_pkt(level=12, zone="Westfall", name="Mati"))
    a.on_packet(state_pkt(level=12, zone="Ironforge", subzone="The Commons", name="Mati"))
    assert voice.said[-1][0] == "Hej, pamiętasz? Notowałeś: chcesz wystawić miecz na AH."
    n = len(voice.said)
    a.on_packet(state_pkt(level=12, zone="Ironforge", subzone="The Great Forge", name="Mati"))
    assert len(voice.said) == n  # not again within half an hour
    assert a.handle("usuń notatkę o mieczu")[0] == "Odhaczone."
    assert a.reminders.pending(notes=True) == []


def test_a_place_reminder_fires_once_on_arrival():
    a, ins, ui, voice, llm = kb_assistant()
    assert a.handle("przypomnij mi w Westfall, żeby zabić Hoggera")[0] == \
        "Dobra, przypomnę ci w Westfall, żeby zabić Hoggera."
    a.on_packet(state_pkt(level=12, zone="Elwynn Forest", name="Mati"))
    a.on_packet(state_pkt(level=12, zone="Westfall", name="Mati"))
    assert "Przypominam, żeby zabić Hoggera." in [t for t, *_ in voice.said]
    assert a.reminders.pending(notes=None) == []


def test_zone_level_and_bag_events(monkeypatch):
    a, ins, ui, voice, llm = kb_assistant()
    a._started -= 60  # past the start-up quiet time
    a.on_packet(state_pkt(level=12, zone="Elwynn Forest", bag_free=1, bag_slots=16, name="Mati"))
    a.on_packet(state_pkt(level=12, zone="Westfall", bag_free=1, bag_slots=16, name="Mati"))
    flush(a)
    assert voice.said[-1][0] == "Witaj w Westfall. Questy tu są na 12–12, czyli w sam raz dla ciebie."
    a.on_packet(state_pkt(level=12, zone="Westfall", bag_free=0, bag_slots=16, name="Mati"))
    assert voice.said[-1][0].startswith("Torby pełne")
    a.on_packet(state_pkt(level=12, zone="Elwynn Forest", bag_free=0, bag_slots=16, name="Mati"))
    a.on_packet(state_pkt(level=12, zone="Westfall", bag_free=0, bag_slots=16, name="Mati"))
    flush(a)
    assert sum(1 for t, *_ in voice.said if t.startswith("Witaj w Westfall")) == 1  # once per half hour


def test_a_taken_quest_gets_a_verdict_and_can_be_asked_about():
    a, ins, ui, voice, llm = kb_assistant()  # the log at start is empty
    a.on_packet(quest_pkt(sync=2, index=1, total=1, quest_id=1, title="Report to Gryan", level=12))  # just taken
    a.on_packet(quest_pkt(sync=2, index=1, total=1, quest_id=1, title="Report to Gryan", level=12))  # the minute resend
    flush(a)  # the comment waits 2.5 s for more quests taken at once
    said = [t for t, *_ in voice.said if t.startswith("Report to Gryan")]
    assert len(said) == 1 and said[0].endswith("Może nie hit, ale exp to exp.")
    assert a.handle("czy warto robić ten quest?")[0] == said[0]


# --------------------------------------------------------------------------- chat channels, loot, items, journal

def channel_pkt(sender, text, channel, kind=14):
    return P.Packet(type=P.TYPE_CHAT, seq=0, chat_kind=kind, sender=sender, text=text, race=channel)


def test_chat_focus_names_the_channel():
    assert parse_intent("co piszą na trade?").channel == "trade"
    assert parse_intent("co słychać na gildii").channel == "guild"
    assert parse_intent("kto szuka grupy?").channel == "lfg"
    assert parse_intent("streść czat").channel == ""


def test_summary_reads_public_channels_and_can_focus_on_one():
    a, ins, ui, voice, llm = make_assistant()
    a.on_packet(channel_pkt("Seller", "WTS Linen Cloth cheap", "Trade"))
    a.on_packet(channel_pkt("Tank", "LFM Deadmines need heal", "LookingForGroup"))
    a.on_packet(channel_pkt("Bob", "anyone up for Hogger?", "General"))
    a.on_packet(chat_pkt("Ann", "hello there"))  # /say
    a.handle("co piszą na trade?")
    convo = llm.calls[-1][-1]["content"]
    assert convo == "[trade] Seller: WTS Linen Cloth cheap"
    a.handle("kto szuka grupy?")
    assert llm.calls[-1][-1]["content"] == "[lookingforgroup] Tank: LFM Deadmines need heal"
    a.handle("streść czat")
    everything = llm.calls[-1][-1]["content"]
    assert "[general] Bob" in everything and "[say] Ann" in everything and "[trade] Seller" in everything
    assert a.handle("co słychać na gildii?")[0] == "Cisza na gildii, nikt nic nie pisze."


def test_a_busy_channel_does_not_push_out_whispers():
    log = ChatLog()
    log.add_packet(chat_pkt("Bob", "inv pls", kind=10))
    for i in range(500):
        log.add_packet(channel_pkt(f"S{i}", f"WTS stuff {i}", "Trade"))
    assert [ln.text for ln in log.whispers()] == ["inv pls"]
    assert len(log.recent(1000, kinds=("CHANNEL",), max_age_s=60)) == 200


def loot_line(text, kind=24):
    return P.Packet(type=P.TYPE_CHAT, seq=0, flags=P.VFLAG_SELF, chat_kind=kind, sender="Mati", text=text)


def test_what_you_looted_is_counted_and_an_upgrade_gets_a_word():
    a, ins, ui, voice, llm = make_assistant()
    for text in ("You receive loot: [Linen Cloth]x2.", "You receive loot: [Linen Cloth].",
                 "You receive loot: [Gloves of the Monkey]."):
        a.on_packet(loot_line(text))
    a.on_packet(loot_line("You loot 1 Gold, 5 Silver, 3 Copper", kind=25))
    reply, _ = a.handle("co mi wpadło?")
    assert reply == ("W ostatnich 15 minutach wpadło ci: 3 sztuki Linen Cloth i Gloves of the Monkey. "
                     "Do tego 1 złota, 5 srebra i 3 miedzi z łupów.")
    # a rogue on level 20 loots gloves better than the ones he wears
    a.on_packet(P.Packet(type=P.TYPE_PLAYER, seq=1, player=P.PlayerInfo(class_id=4, race_id=1, faction=1, sex=2)))
    a.on_packet(state_pkt(level=20, name="Mati"))
    worn = [P.WornItem(15, 2, "Rough Leather Gloves", {"agi": 1, "armor": 40})]
    gloves = P.LootItem(roll_id=0, flags=P.LFLAG_PICKED, quality=2, item_level=24, min_level=19, class_id=4,
                        subclass_id=2, equip_loc="INVTYPE_HAND", name="Gloves of the Monkey",
                        stats={"agi": 6, "sta": 4, "armor": 62}, equipped=worn)
    a.on_packet(P.Packet(type=P.TYPE_LOOT, seq=2, loot=gloves))
    assert voice.said[-1][0].startswith("Nowy łup: Gloves of the Monkey. Lepsze niż Rough Leather Gloves: plus 5")
    assert voice.said[-1][0].endswith("Załóż.")
    said = len(voice.said)
    worse = P.LootItem(roll_id=0, flags=P.LFLAG_PICKED, quality=1, class_id=4, subclass_id=2, equip_loc="INVTYPE_HAND",
                       name="Ragged Gloves", stats={"armor": 10}, equipped=worn)
    a.on_packet(P.Packet(type=P.TYPE_LOOT, seq=3, loot=worse))
    assert len(voice.said) == said  # not an upgrade: nothing to say


def test_items_are_answered_from_the_world_data():
    from tests.test_advisor import item_world

    a, ins, ui, voice, llm = make_assistant(world_getter=item_world)
    reply, line = a.handle("gdzie kupić rhapsody malt?")
    assert reply.startswith("Rhapsody Malt kupisz u Innkeeper Belm, Innkeeper, w Dun Morogh, 47, 52.")
    assert line == "/wvt way 1426 47.4 52.5 Rhapsody Malt: Innkeeper Belm" and not llm.calls


def test_the_journal_keeps_questions_answers_and_comments(tmp_path):
    import json

    path = tmp_path / "logs" / "assistant.jsonl"
    a, ins, ui, voice, llm = make_assistant(journal_path=path)
    a.on_packet(state_pkt(level=12, zone="Westfall", gold=3, name="Mati"))
    a.handle("ile mam złota?")
    a.handle("jak się masz?")
    a._respond("Torby pełne.", "", "", event="bags")
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert [r.get("intent", r.get("event")) for r in rows] == ["gold", "chat", "bags"]
    assert rows[0]["heard"] == "ile mam złota?" and rows[0]["reply"].startswith("Masz 3 złota")
    assert rows[0]["zone"] == "Westfall" and rows[0]["level"] == 12
    assert rows[1]["reply"] == llm.answer and rows[2]["reply"] == "Torby pełne."
    b, *_ = make_assistant()  # without a path nothing is written anywhere
    b.handle("ile mam złota?")


# --------------------------------------------------------------------------- the names of the moment, what you carry

def bag_pkt(sync, part, parts, *lines):
    return P.Packet(type=P.TYPE_CHAT, seq=0, chat_kind=P.KIND_BAG, race=f"{sync}:{part}/{parts}", text="\n".join(lines))


def carrying_assistant():
    a, ins, ui, voice, llm = make_assistant()
    a.on_packet(P.Packet(type=P.TYPE_PLAYER, seq=1, player=P.PlayerInfo(class_id=8, race_id=7, faction=1, sex=2)))
    a.on_packet(state_pkt(level=10, name="Mati"))
    staff = P.LootItem(flags=P.LFLAG_PICKED, quality=2, item_level=12, min_level=8, class_id=2, subclass_id=10,
                       equip_loc="INVTYPE_2HWEAPON", name="Gnarled Short Staff", stats={"int": 4, "spi": 3, "dps": 5.1},
                       equipped=[P.WornItem(5, 10, "Worn Staff", {"int": 1, "dps": 3.0})])
    a.on_packet(P.Packet(type=P.TYPE_LOOT, seq=2, loot=staff))
    mittens = P.LootItem(flags=P.LFLAG_BAG, quality=1, class_id=4, subclass_id=1, equip_loc="INVTYPE_HAND",
                         name="Wooly Mittens", stats={"armor": 8},
                         equipped=[P.WornItem(10, 1, "Silk Gloves", {"int": 3, "armor": 9})])
    a.on_packet(P.Packet(type=P.TYPE_LOOT, seq=3, loot=mittens))
    a.on_packet(bag_pkt(1, 1, 1, "2589\t12\t1\t7\t5\t0\tLinen Cloth", "2894\t1\t1\t0\t0\t0\tRhapsody Malt",
                        "4000\t1\t2\t2\t10\t17\tGnarled Short Staff", "4100\t1\t1\t4\t1\t11\tWooly Mittens",
                        "3300\t3\t0\t15\t0\t0\tBroken Tooth", "3301\t1\t0\t15\t0\t0\tChipped Claw"))
    return a, voice, llm


@pytest.mark.parametrize("text, kind", [
    ("czy ten kij, który znalazłem, jest lepszy?", "item_about"),
    ("a te rękawice są dobre dla mnie?", "item_about"),
    ("czy warto zachować ten miecz?", "item_about"),
    ("gdzie kupię lepszy miecz?", "chat"),  # shopping, not your bags
    ("czy warto robić ten quest?", "quest_worth"),
    ("co mam w plecaku?", "bag_list"),
    ("co mogę sprzedać?", "bag_junk"),
    ("czy mam rhapsody malt?", "bag_count"),
    ("czy mam coś do oddania?", "quest_turnin"),
    ("ile mam wolnych miejsc w torbach?", "bags"),
])
def test_questions_about_what_you_carry_become_intents(text, kind):
    assert parse_intent(text).kind == kind


def test_the_staff_you_found_is_compared_with_what_you_wear():
    a, voice, llm = carrying_assistant()
    assert not any("Wooly Mittens" in t for t, *_ in voice.said)  # the bag scan itself is never commented on
    reply, line = a.handle("czy ten kij, który znalazłem, jest lepszy?")
    assert reply == "Gnarled Short Staff, kostur. Lepsze niż Worn Staff: plus 3 intelektu i plus 3 ducha. Załóż."
    assert line == "" and not llm.calls
    reply, _ = a.handle("a te rękawice są dobre?")  # a wearable from the bag scan: stats, never commented on
    assert reply.startswith("Wooly Mittens, rękawice, materiał. Nie ma statystyk, które przydają się magowi.")
    assert a.handle("czy ten miecz jest lepszy?")[0] == "Nie widzę takiego przedmiotu w twoich łupach ani w torbach."


def test_the_bags_are_listed_counted_and_the_junk_named():
    a, voice, llm = carrying_assistant()
    assert a.handle("co mam w plecaku?")[0] == (
        "W torbach masz 6 rzeczy. Do założenia: Gnarled Short Staff i Wooly Mittens. Poza tym 12 Linen Cloth "
        "i Rhapsody Malt. I 2 szare śmieci do sprzedania.")
    assert a.handle("co mogę sprzedać?")[0] == "Do sprzedania masz 4 szare śmieci: 3 Broken Tooth i Chipped Claw."
    assert a.handle("czy mam rhapsody malt?")[0] == "Masz Rhapsody Malt, jedną sztukę."
    assert a.handle("ile mam linen cloth?")[0] == "Masz 12 sztuk Linen Cloth."
    b, *_ = make_assistant()
    assert b.handle("co mam w plecaku?")[0].startswith("Jeszcze nie widzę twoich toreb.")


def test_a_misheard_quest_name_still_finds_the_quest(tmp_path):
    import json

    path = tmp_path / "assistant.jsonl"
    a, ins, ui, voice, llm = make_assistant(journal_path=path)
    a.on_packet(quest_pkt(sync=1, index=1, total=1, quest_id=384, level=7, title="Beer Basted Boar Ribs",
                          zone="Dun Morogh", objectives=[(False, "2/6 Crag Boar Rib"), (False, "0/1 Rhapsody Malt")]))
    reply, _ = a.handle("ile mi zostało do Beer Bastard Bore Ribs?")
    assert reply == "Beer Basted Boar Ribs: Crag Boar Rib 2 z 6, Rhapsody Malt 0 z 1."
    row = json.loads(path.read_text(encoding="utf-8").splitlines()[-1])
    assert row["heard"] == "ile mi zostało do Beer Bastard Bore Ribs?"
    assert row["fixed"] == "ile mi zostało do Beer Basted Boar Ribs?"
    assert row["fixes"] == ["Beer Bastard Bore Ribs -> Beer Basted Boar Ribs"]
    assert a.last_correction.changed


def test_her_llm_gets_a_picture_of_the_game():
    a, voice, llm = carrying_assistant()
    a.on_packet(chat_pkt("Thargas-Realm", "inv pls", kind=10))
    a.handle("jak się masz?")
    system = llm.calls[-1][0]["content"]
    assert "Co się teraz dzieje w grze" in system and "Gnarled Short Staff" in system and "Thargas" in system


def test_bag_lines_are_not_chat():
    a, *_ = make_assistant()
    a.on_packet(bag_pkt(2, 1, 1, "2589\t12\t1\t7\t5\t0\tLinen Cloth"))
    assert a.bags.known and a.chat.recent(10, kinds=READ_KINDS) == []


# --------------------------------------------------------------------------- the topic of the talk and the plan

def talk_assistant(llm=None):
    from tests.test_planner import book, plan_world

    a, ins, ui, voice, fake = make_assistant()
    if llm is not None:
        a.llm = llm
    w = plan_world()
    a.world_getter = lambda: w
    a.quests = book()
    a.on_packet(state_pkt(level=10, xp=0, xp_max=7000, name="Mati"))
    a.on_packet(P.Packet(type=P.TYPE_PLAYER, seq=1, player=P.PlayerInfo(1, 3, P.FACTION_ALLIANCE, 2)))
    return a, ins, a.llm


def test_follow_up_questions_are_about_the_quest_you_talk_about():
    a, ins, llm = talk_assistant()
    reply, _ = a.handle("czy warto robić Beer Bastard Bore Ribs?")  # misheard, put right, now the topic
    assert reply.startswith("Beer Basted Boar Ribs:")
    assert a.handle("a ile za niego expa?")[0] == "Za Beer Basted Boar Ribs dostaniesz 630 XP, czyli 9% poziomu."
    reply, line = a.handle("a gdzie go oddać?")
    assert reply == "Beer Basted Boar Ribs oddajesz u Ragnar Thunderbrew, około 80 jardów na wschód."
    assert line == "/wvt track 384"
    a.handle("ile mi zostało do The Boar Hunter?")  # a new topic
    reply, line = a.handle("a gdzie to jest?")
    assert reply.startswith("The Boar Hunter: około 400 jardów na wschód") and line == "/wvt track 183"


def test_her_llm_sees_the_conversation_and_its_topic():
    a, ins, llm = talk_assistant()
    a.handle("czy warto robić The Boar Hunter?")
    a.handle("o czym jest ten quest?")  # free question about the quest: the LLM with all about it
    system = llm.calls[-1][0]["content"]
    assert "Pytanie dotyczy questa The Boar Hunter (poziom 8)" in system and "1. z 2 questów w łańcuchu" in system
    assert [m["role"] for m in llm.calls[-1][1:]] == ["user", "assistant", "user"]  # the earlier question too
    a.handle("opowiedz mi coś")  # small talk still knows what you were talking about
    assert "Rozmawiacie teraz o queście The Boar Hunter" in llm.calls[-1][0]["content"]


class ThinkingLLM(FakeLLM):
    supports_thinking = True

    def chat(self, messages, max_tokens=120, temperature=0.0, stop=None, timeout=None, thinking=None):
        self.thinking = thinking
        return super().chat(messages, max_tokens, temperature, stop, timeout)


def test_what_now_is_a_plan_and_what_next_goes_on():
    a, ins, llm = talk_assistant(ThinkingLLM("Najpierw oddaj żeberka u Ragnara, potem dziki na wschodzie."))
    reply, line = a.handle("co teraz robić?")
    assert reply == ("Najpierw oddaj żeberka u Ragnara, potem dziki na wschodzie. Odpuść Old Errand: jest już szary, "
                     "poniżej 1% poziomu. Enter włączy strzałkę.")  # she left out what to let go: added
    assert line == "/wvt track 384" and llm.thinking == "low"  # assistant.plan_thinking
    system = llm.calls[-1][0]["content"]
    assert "1. oddaj Beer Basted Boar Ribs u Ragnar Thunderbrew" in system
    assert "Odpuść: Old Errand – jest już szary" in system and "Na później (inna mapa): Far Away" in system
    reply, line = a.handle("a potem?")
    assert reply.startswith("Teraz The Boar Hunter (Small Crag Boar 4 z 12), około 400 jardów na wschód.")
    assert line == "/wvt track 183"
    assert a.handle("a gdzie to jest?")[1] == "/wvt track 183"  # the step is the topic now
    assert parse_intent("co dalej?").kind == "plan_next" and parse_intent("ok, a potem?").kind == "plan_next"
    assert parse_intent("co dalej w tym łańcuchu?").kind == "quest_worth"
