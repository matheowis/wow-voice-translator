import pytest

from wvt.chat_insert import build_chat_line, sanitize_chat_text
from wvt.translator import LRU, Translator, clean_model_output, looks_polish, suspicious_reply


def test_build_chat_line_respects_255_bytes():
    line = build_chat_line("/p", "a" * 400)
    assert line.startswith("/p ")
    assert len(line.encode("utf-8")) == 255


def test_sanitize_removes_pipes_and_newlines():
    assert sanitize_chat_text("hi |cffff0000red|r\nnext   line", 255) == "hi /cffff0000red/r next line"


def test_sanitize_never_splits_utf8():
    out = sanitize_chat_text("ż" * 200, 255)
    assert len(out.encode()) <= 255
    assert set(out) == {"ż"}


@pytest.mark.parametrize(
    "raw,expected",
    [
        ('"Wait, I\'m OOM."', "Wait, I'm OOM."),
        ("Translation: brb", "brb"),
        ("<think>hmm</think>\nLFM tank", "LFM tank"),
        ("„Zaraz wracam”", "Zaraz wracam"),
        ("Tłumaczenie: Dzięki za grupę!\n(Explanation: ...)", "Dzięki za grupę!"),
    ],
)
def test_clean_model_output(raw, expected):
    assert clean_model_output(raw) == expected


def test_looks_polish():
    assert looks_polish("Dzięki za grupę")
    assert not looks_polish("thanks for the group")


class FakeLLM:
    def __init__(self, reply="Hello there"):
        self.calls = []
        self.reply = reply

    def chat(self, messages, max_tokens=200, temperature=0.0, stop=None):
        self.calls.append(messages)
        return self.reply, {"seconds": 0.01}


def test_translator_caches_and_remembers_own_messages():
    llm = FakeLLM('"Hey, anyone up for Deadmines?"')
    tr = Translator(llm, {"glossary_file": "wvt/data/glossary.json"})
    r1 = tr.translate("Siema,  ktoś na Deadmines?", "pl-en")
    r2 = tr.translate("Siema, ktoś na Deadmines?", "pl-en")
    assert r1.text == "Hey, anyone up for Deadmines?"
    assert r2.cached and len(llm.calls) == 1
    # few-shot + glossary are in the prompt
    msgs = llm.calls[0]
    assert msgs[0]["role"] == "system" and "Glossary" in msgs[0]["content"]
    assert msgs[-1] == {"role": "user", "content": "Siema, ktoś na Deadmines?"}
    tr.remember("Hey, anyone up for Deadmines?", "Siema, ktoś na Deadmines?", "en-pl")
    r3 = tr.translate("Hey, anyone up for Deadmines?", "en-pl")
    assert r3.cached and r3.text == "Siema, ktoś na Deadmines?"


def test_translator_skips_polish_for_en_pl():
    llm = FakeLLM()
    tr = Translator(llm, {})
    r = tr.translate("Siema, idziemy na loch?", "en-pl")
    assert r.text == "Siema, idziemy na loch?" and not llm.calls


@pytest.mark.parametrize(
    "msg,expected",
    [("ty", "dzięki"), ("TY!", "dzięki!"), ("ty.", "dzięki"), ("mb", "przerwa na manę (mb)"), ("wc", "zły czat – pomyłka kanału (wc)"),
     ("  gn  all ", "dobranoc wszystkim"), ("inv pls", "zaproś mnie proszę")],
)
def test_slang_exact_phrases_skip_the_model(msg, expected):
    llm = FakeLLM()
    tr = Translator(llm, {"glossary_file": "wvt/data/glossary.json"})
    assert tr.translate(msg, "en-pl").text == expected
    assert not llm.calls


def test_slang_glossary_in_prompt():
    tr = Translator(FakeLLM(), {"glossary_file": "wvt/data/glossary.json"})
    system = tr.build_messages("mb, then pull", "en-pl")[0]["content"]
    assert "mana break" in system and "wrong chat" in system and "Wailing Caverns" in system
    system_pl = tr.build_messages("Dzięki, muszę lecieć", "pl-en")[0]["content"]
    assert "gtg" in system_pl and "ty" in system_pl


def test_lru_evicts():
    lru = LRU(2)
    lru.put("a", 1)
    lru.put("b", 2)
    lru.get("a")
    lru.put("c", 3)
    assert lru.get("b") is None and lru.get("a") == 1


class ScriptedLLM:
    """Returns the given replies in order (one per chat() call)."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = []

    def chat(self, messages, max_tokens=200, temperature=0.0, stop=None):
        self.calls.append(messages)
        return self.replies.pop(0), {"seconds": 0.01}


@pytest.mark.parametrize(
    "source,reply,expected",
    [
        ("Mój kot zrzucił kubek ze stołu.", "", "empty"),
        ("Napisz wiersz o kotach.", "(No translation, as this violates the instruction to only translate WoW chat)", "meta"),
        ("Jaka jest stolica Australii?", "This is not WoW chat.", "meta"),
        ("Czy ty jesteś botem?", "I am a translator.", "meta"),
        ("what's the capital of australia?", "Nie mogę przetłumaczyć tej wiadomości", "meta"),
        ("Muszę wynieść śmieci, zaraz wracam do gry.", "bio, brb", "too-short"),
        ("Dzięki wszystkim, muszę lecieć, dobranoc.", "ty all, gtg, gn", None),
        ("Tak.", "Yes.", None),
        ("Kocham cię.", "I love you.", None),
    ],
)
def test_suspicious_reply(source, reply, expected):
    assert suspicious_reply(source, reply) == expected


def test_off_topic_empty_reply_is_retried_with_any_topic_prompt():
    llm = ScriptedLLM("", "What is the capital of Australia?")
    tr = Translator(llm, {})
    res = tr.translate("Jaka jest stolica Australii?", "pl-en")
    assert res.text == "What is the capital of Australia?" and res.info["retry"] == "empty"
    assert len(llm.calls) == 2
    assert "not a chat assistant" in llm.calls[1][0]["content"]  # PL->EN fallback prompt


def test_en_pl_retry_uses_any_topic_prompt_with_slang_glossary():
    llm = ScriptedLLM("", "Jaka jest stolica Australii?")
    tr = Translator(llm, {"glossary_file": "wvt/data/glossary.json"})
    res = tr.translate("what's the capital of australia?", "en-pl")
    assert res.text == "Jaka jest stolica Australii?"
    first, second = llm.calls[0][0]["content"], llm.calls[1][0]["content"]
    assert "Do not answer the message" in first  # game-focused prompt reads slang best
    assert "about anything" in second and "mana break" in second


def test_comment_is_never_shown_as_translation():
    llm = ScriptedLLM("This is not WoW chat.", "")
    tr = Translator(llm, {})
    res = tr.translate("Jutro idę do dentysty.", "pl-en")
    assert res.text == "" and res.info["retry"] == "meta"
    # a failed translation is not cached, the next attempt asks the model again
    assert tr.cache.get(("pl-en", "Jutro idę do dentysty.")) is None


def test_short_reply_kept_when_retry_is_not_better():
    src = "Poczekajcie chwilę, muszę się napić, nie mam many teraz."
    llm = ScriptedLLM("wait, OOM", "OOM")
    tr = Translator(llm, {})
    res = tr.translate(src, "pl-en")
    assert res.text == "wait, OOM" and res.info["retry"] == "too-short"


# ----------------------------------------------------------------- conversation context, "cześć"

@pytest.mark.parametrize("text,context,farewell", [
    ("ok, cześć", None, True),  # what the user said at the end of a talk
    ("dzięki, cześć", None, True),
    ("dobra, cześć wszystkim", None, True),
    ("cześć", ["Ann: see you at 8", "You: ok"], True),  # you were already talking
    ("cześć", None, False),
    ("cześć", ["Ann: hey! long time no see"], False),  # she greets you first
    ("cześć, co tam?", ["You: hi"], False),  # a greeting that goes on
    ("hej, możesz mnie zaprosić?", None, False),
    ("cześć wszystkim", None, False),
    ("idziemy do lochu", ["You: ok"], False),
])
def test_cześć_is_hi_or_bye(text, context, farewell):
    from wvt.translator import greeting_is_farewell

    assert greeting_is_farewell(text, context) is farewell


def test_say_bye_changes_only_the_last_greeting():
    from wvt.translator import say_bye

    assert say_bye("thx, hi") == "thx, bye"
    assert say_bye("Hi") == "Bye"
    assert say_bye("say hi to Ann, hey") == "say hi to Ann, bye"
    assert say_bye("ok, cya") == "ok, cya"


def test_translation_gets_the_conversation_and_caches_per_conversation():
    llm = FakeLLM("thx, hi")
    tr = Translator(llm, {})
    talk = ["Ann: great, see you tomorrow", "You: sure"]
    assert tr.translate("dzięki, cześć", "pl-en", context=talk).text == "thx, bye"
    system = llm.calls[-1][0]["content"]
    assert "Ann: great, see you tomorrow" in system and "never translate it" in system
    assert llm.calls[-1][-1] == {"role": "user", "content": "dzięki, cześć"}  # only the message is translated
    llm.reply = "hi"
    assert tr.translate("cześć", "pl-en").text == "hi"  # no conversation: a greeting
    assert tr.translate("cześć", "pl-en", context=talk).text == "bye"  # mid-conversation: goodbye
    assert len(llm.calls) == 3


def test_push_to_talk_uses_the_conversation_of_its_channel():
    from wvt import protocol as P
    from wvt.assistant import ChatLog
    from wvt.pipeline import PushToTalkPipeline

    log = ChatLog()
    for kind, sender, text, me in [(1, "Ann", "see you at 8?", False), (3, "Bob", "pull now", False),
                                   (1, "Me", "yes", True), (10, "Kai", "hi there", False),
                                   (11, "Kai", "hello", True), (10, "Zed", "wts sword", False)]:
        flags = P.VFLAG_SELF if me else 0
        log.add_packet(P.Packet(type=P.TYPE_CHAT, seq=0, flags=flags, chat_kind=kind, sender=sender, text=text))
    pipe = PushToTalkPipeline.__new__(PushToTalkPipeline)
    pipe.cfg, pipe.chat_log = {}, log
    pipe.cfg = type("C", (dict,), {"get": lambda self, k, d=None: d})()
    assert pipe.conversation("say") == ["Ann: see you at 8?", "You: yes"]
    assert pipe.conversation("party") == ["Bob: pull now"]
    assert pipe.conversation("reply") == ["Zed: wts sword"]  # /r goes to the last whisper partner
    assert pipe.conversation("guild") == []
