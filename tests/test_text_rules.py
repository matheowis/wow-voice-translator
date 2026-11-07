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
