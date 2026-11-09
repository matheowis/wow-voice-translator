"""Voice assistant: intents from Polish speech, invite requests in chat, group commands, answers."""

import time

import pytest

from wvt import protocol as P
from wvt.assistant import Assistant, ChatLog, clean_reply, is_invite_request, match_name, parse_intent


@pytest.mark.parametrize("text,kind", [
    ("ej weź ich wysapraszaj", "invite_requests"),  # the user's own example
    ("Zaproś ich wszystkich", "invite_requests"),
    ("zinwajtuj tych co piszą inv", "invite_requests"),
    ("zaproś Artasa", "invite_name"),
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
    "Czy mogę solo zrobić Deadmines na 18 levelu?", "gdzie znajdę kask?", "czy znasz polskich graczy?",
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


def make_assistant(voice_id="voice-iskra"):
    cfg = Cfg({"assistant.name": "Iskra", "assistant.voice_id": voice_id, "assistant.invite_window_s": 300})
    ins, ui, voice, llm = FakeInserter(), FakeUI(), FakeVoice(), FakeLLM()
    a = Assistant(cfg, FakeTranslator(), llm, ins, ui, lambda: True, voice_getter=lambda: voice)
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
