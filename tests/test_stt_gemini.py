"""Speech-to-text model choice and the local STT service; Gemini for the assistant (no network, no models)."""

import io
import json
import time
import urllib.error
import urllib.request
import wave

import httpx
import numpy as np
import pytest

from wvt import stt as stt_mod
from wvt.gemini import (FallbackLLM, GeminiEmbedder, GeminiError, GeminiLLM, assistant_llm, lexicon_embedder,
                        to_gemini)
from wvt.stt import SpeechToText, normalize_phrase
from wvt.stt_server import SttServer, read_wav


# --------------------------------------------------------------------------- STT: which model, where

def _model_dirs(tmp_path, whisper=True, parakeet=True):
    w = tmp_path / "whisper-large-v3-turbo-onnx"
    p = tmp_path / "parakeet-tdt-0.6b-v3-onnx"
    if whisper:
        (w / "onnx").mkdir(parents=True)
        for f in ("encoder_model_fp16.onnx", "decoder_model_merged_fp16.onnx"):
            (w / "onnx" / f).write_bytes(b"x")
    if parakeet:
        p.mkdir()
        for f in ("encoder-model.onnx", "encoder-model.int8.onnx", "decoder_joint-model.onnx"):
            (p / f).write_bytes(b"x")
    return w, p


def make_stt(tmp_path, device="auto", **kw):
    w, p = _model_dirs(tmp_path, **kw)
    return SpeechToText.from_config({
        "model_dir": str(w), "model_name": "whisper", "language": "pl", "device": device, "gpu_quantization": "fp16",
        "gpu_need_mb": 2600, "vram_reserve_mb": 2500, "cpu_quantization": "int8",
        "cpu_fallback": {"model_dir": str(p), "model_name": "nemo-parakeet-tdt-0.6b-v3", "cpu_quantization": "int8",
                         "gpu_need_mb": 3800}})


class FakeOrt:
    def __init__(self, cuda=True):
        self.cuda = cuda

    def get_available_providers(self):
        return ["CUDAExecutionProvider", "CPUExecutionProvider"] if self.cuda else ["CPUExecutionProvider"]


def _plan(stt, free_mb, monkeypatch, cuda=True):
    import wvt.gpu

    monkeypatch.setattr(wvt.gpu, "vram_used_mb", lambda: {"total": 16000, "used": 16000 - free_mb})
    monkeypatch.setattr(stt_mod, "preload_cuda_dlls", lambda: None)
    return [(m.label, dev, q) for m, dev, _, q in stt._candidates(FakeOrt(cuda))]


def test_whisper_on_the_gpu_first_then_parakeet(tmp_path, monkeypatch):
    stt = make_stt(tmp_path)
    plan = _plan(stt, 9000, monkeypatch)
    assert plan == [("whisper-large-v3-turbo", "cuda", "fp16"), ("parakeet-tdt-0.6b-v3", "cuda", None),
                    ("parakeet-tdt-0.6b-v3", "cpu", "int8")]
    assert stt.primary.language == "pl" and stt.fallback.language is None


def test_little_vram_means_parakeet_on_the_cpu_never_whisper(tmp_path, monkeypatch):
    stt = make_stt(tmp_path)
    assert _plan(stt, 5500, monkeypatch) == [("whisper-large-v3-turbo", "cuda", "fp16"),
                                             ("parakeet-tdt-0.6b-v3", "cpu", "int8")]  # 5500 < 3800 + 2500
    assert _plan(stt, 4000, monkeypatch) == [("parakeet-tdt-0.6b-v3", "cpu", "int8")]
    assert "4000 MB < 2600" in stt.device_reason or "4000 MB < 3800" in stt.device_reason
    stt_cpu = make_stt(tmp_path / "cpu", device="cpu")
    assert _plan(stt_cpu, 9000, monkeypatch) == [("parakeet-tdt-0.6b-v3", "cpu", "int8")]


def test_without_whisper_files_parakeet_keeps_the_gpu(tmp_path, monkeypatch):
    stt = make_stt(tmp_path, whisper=False)
    assert _plan(stt, 9000, monkeypatch) == [("parakeet-tdt-0.6b-v3", "cuda", None),
                                             ("parakeet-tdt-0.6b-v3", "cpu", "int8")]


def test_parakeet_alone_ignores_whisper_settings(tmp_path, monkeypatch):
    _, p = _model_dirs(tmp_path, whisper=False)
    stt = SpeechToText.from_config({"model_dir": str(p), "model_name": "nemo-parakeet-tdt-0.6b-v3",
                                    "gpu_quantization": "fp16", "gpu_need_mb": 2600})
    assert stt.primary.gpu_need_mb == 3800 and stt.primary.language is None
    assert _plan(stt, 9000, monkeypatch)[0] == ("parakeet-tdt-0.6b-v3", "cuda", None)  # no fp16 files: fp32


class FakeSession:
    def __init__(self, providers):
        self._p = providers

    def get_providers(self):
        return self._p

    def run(self, *a):
        return None


class FakeModel:
    def __init__(self, providers, says="Idę po wodę."):
        class Asr:
            pass

        self.asr = Asr()
        self.asr._encoder = FakeSession(providers)
        self.asr._decoder = FakeSession(providers)
        self.says = says
        self.calls = []

    def recognize(self, audio, sample_rate=16000, **kw):
        self.calls.append(kw)
        return self.says


def test_load_rejects_a_silent_cpu_fallback_and_whisper_gets_polish(tmp_path, monkeypatch):
    import sys
    import types

    stt = make_stt(tmp_path)
    loaded = []

    def load_model(name, path=None, quantization=None, providers=None):
        loaded.append((name, quantization))
        if name == "whisper":  # CUDA did not start: onnxruntime would run it on the CPU
            return FakeModel(["CPUExecutionProvider"])
        return FakeModel(["CUDAExecutionProvider", "CPUExecutionProvider"])

    fake_asr = types.SimpleNamespace(load_model=load_model)
    fake_ort = types.SimpleNamespace(set_default_logger_severity=lambda n: None,
                                     get_available_providers=FakeOrt().get_available_providers)
    monkeypatch.setitem(sys.modules, "onnx_asr", fake_asr)
    monkeypatch.setitem(sys.modules, "onnxruntime", fake_ort)
    _plan(stt, 9000, monkeypatch)
    stt.load()
    assert loaded == [("whisper", "fp16"), ("nemo-parakeet-tdt-0.6b-v3", None)]
    assert stt.model_label == "parakeet-tdt-0.6b-v3" and stt.device == "cuda"
    assert stt.model.calls == [{}]  # Parakeet gets no language

    loaded.clear()
    monkeypatch.setitem(sys.modules, "onnx_asr", types.SimpleNamespace(
        load_model=lambda *a, **k: FakeModel(["CUDAExecutionProvider"], says="Dziękuję.")))
    stt2 = make_stt(tmp_path / "b")
    _plan(stt2, 5459, monkeypatch)  # room for Whisper (2600 + 2500), not for Parakeet (3800 + 2500)
    stt2.load()
    assert stt2.describe() == "whisper-large-v3-turbo na cuda-fp16" and stt2.model.calls == [{"language": "pl"}]
    assert stt2.device_reason == "wolne VRAM 5459 MB ≥ 2600 (STT) + 2500 (zapas dla gry)"  # Whisper's own check
    # a typical Whisper hallucination on a breath is dropped
    assert stt2.transcribe(np.ones(16000, dtype=np.float32) * 0.1) == "" and stt2.dropped == 1
    stt2.model.says = "Dziękuję za pomoc w Westfall"
    assert stt2.transcribe(np.ones(16000, dtype=np.float32) * 0.1) == "Dziękuję za pomoc w Westfall"
    assert stt2.model.calls[-1] == {"language": "pl"}


def test_phrases_are_compared_without_case_and_punctuation():
    assert normalize_phrase("  Napisy stworzone przez społeczność Amara.org ") == \
        "napisy stworzone przez społeczność amara org"
    assert normalize_phrase("Thanks for watching!") == "thanks for watching"


# --------------------------------------------------------------------------- Whisper with the names of the moment

PROMPT = "Rozmowa w World of Warcraft. Wendigo Mane, The Perfect Stout, Tools for Steelgrill, Tannok Frosthammer."


def test_what_whisper_made_up_from_the_prompt_is_recognised():
    # what a prompted Whisper really wrote on silence and noise in the bench (2026-11-09)
    assert stt_mod.looks_runaway("Tannok Frosthammer, " * 12, 1.5)
    assert stt_mod.looks_runaway("D-Fatty, D-Fatty, D-Fatty, D-Fatty", 1.5)
    assert stt_mod.looks_runaway("Wendigo Mane, The Perfect Stout, Tools for Steelgrill.", 3.0, PROMPT)
    assert stt_mod.looks_runaway("słowo " * 40, 2.0)  # nobody says 20 words a second
    assert not stt_mod.looks_runaway("Gdzie kupię Rhapsody Malt? Muszę oddać The Perfect Stout.", 2.5, PROMPT)
    assert not stt_mod.looks_runaway("Idę do Ironforge, spotkajmy się przy bramie.", 2.5)
    assert stt_mod.collapse_repeats("Tannok Frosthammer, Tannok Frosthammer, Tannok Frosthammer, Tannok "
                                    "Frosthammer") == "Tannok Frosthammer,"
    assert stt_mod.collapse_repeats("nie nie nie, mówię ci") == "nie mówię ci"


class FakeTok:
    def encode(self, text, add_special_tokens=False):
        import types

        return types.SimpleNamespace(ids=[len(w) for w in text.split()])


class PromptModel:
    """A Whisper that reels off its prompt when it gets one."""

    def __init__(self):
        self.calls = []

    def recognize(self, audio, sample_rate=16000, **kw):
        self.calls.append(kw)
        return "Tannok Frosthammer, " * 15 if kw.get("prompt_ids") else "Gdzie kupię Rhapsody Malt?"


def test_a_prompt_that_makes_whisper_run_away_is_dropped(tmp_path):
    stt = make_stt(tmp_path)
    stt.model, stt.active, stt._kwargs = PromptModel(), stt.primary, {"language": "pl"}
    stt.prompt_ok, stt._tok, stt.prompt_max_tokens = True, FakeTok(), 4
    assert stt.prompt_ids(PROMPT) == [3, 11, 6, 12]  # only the end: the most important names are last
    text = stt.transcribe(np.zeros(16000, dtype=np.float32), prompt=PROMPT)
    assert text == "Gdzie kupię Rhapsody Malt?" and stt.prompt_dropped == 1
    first, second = stt.model.calls
    assert first == {"language": "pl", "max_new_tokens": 26, "prompt_ids": [3, 11, 6, 12]}
    assert second == {"language": "pl", "max_new_tokens": 26}  # again, without the prompt
    stt.model.calls.clear()
    stt.transcribe(np.zeros(32000, dtype=np.float32))
    assert stt.model.calls == [{"language": "pl", "max_new_tokens": 36}]  # no prompt: one call, a length cap
    stt.model.calls.clear()
    stt.warmup()  # the prompted decode is warmed up too, so the first question is not slower
    assert [("prompt_ids" in c) for c in stt.model.calls] == [False, True]
    stt.model.recognize = lambda *a, **k: "D-"  # what a prompted Whisper made of noise in the bench
    assert stt.transcribe(np.zeros(16000, dtype=np.float32), prompt=PROMPT) == ""


class FakeWhisperAsr:
    def __init__(self):
        self._tokens = {"<|startofprev|>": 1, "<|startoftranscript|>": 2, "<|pl|>": 3, "<|transcribe|>": 4,
                        "<|notimestamps|>": 5}
        self._transcribe_input = np.array([[2, 0, 4, 5]], dtype=np.int64)
        self.seen = None

    def _encode(self, waveforms, lens):
        return "encoded"

    def _decoding(self, encoded, tokens, max_length=448):
        self.seen = (tokens.tolist(), max_length)
        return np.hstack((tokens, np.array([[42, 43]] * len(tokens))))

    def _decode_tokens(self, tokens):
        return tokens.tolist()

    def recognize_batch(self, waveforms, lens, **kw):
        return iter(["the original"])


def test_whisper_learns_the_prompt_and_a_length_cap():
    asr = FakeWhisperAsr()
    assert stt_mod.install_whisper_prompting(asr)
    out = list(asr.recognize_batch(np.zeros((1, 10)), np.array([10]), language="pl", prompt_ids=[7, 8],
                                   max_new_tokens=10))
    assert asr.seen == ([[1, 7, 8, 2, 3, 4, 5]], 17)  # <|startofprev|> prompt, then the usual start
    assert out == [[42, 43]]  # the prompt is not part of the result
    assert list(asr.recognize_batch(np.zeros((1, 10)), np.array([10]), language="pl")) == ["the original"]
    assert not stt_mod.install_whisper_prompting(object())  # another onnx-asr: no prompts, nothing breaks


# --------------------------------------------------------------------------- the local STT service

def _wav(seconds=0.5, rate=16000, channels=1):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes((np.sin(np.linspace(0, 200, int(rate * seconds) * channels)) * 8000).astype("<i2").tobytes())
    return buf.getvalue()


class FakeStt:
    def __init__(self, loaded=True):
        self.model = object() if loaded else None
        self.model_label = "whisper-large-v3-turbo"
        self.device = "cuda-fp16"
        self.heard = []

    def transcribe(self, audio):
        self.heard.append(audio)
        return "Zapłacę ci 100 sztuk złota"


def _call(port, method, path, body=None, host=None):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=body, method=method,
                                 headers={"Content-Type": "audio/wav", **({"Host": host} if host else {})})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_the_stt_service_answers_health_and_transcribes_wav():
    holder = {"stt": None}
    server = SttServer(lambda: holder["stt"], port=0)
    assert server.start()
    try:
        port = server.port
        assert _call(port, "GET", "/health") == (200, {"ok": False, "model": "", "device": "loading"})
        assert _call(port, "POST", "/stt", _wav())[0] == 503  # still loading
        holder["stt"] = FakeStt()
        assert _call(port, "GET", "/health") == (200, {"ok": True, "model": "whisper-large-v3-turbo",
                                                         "device": "cuda-fp16"})
        code, out = _call(port, "POST", "/stt", _wav(0.5))
        assert code == 200 and out["text"] == "Zapłacę ci 100 sztuk złota" and isinstance(out["ms"], int)
        assert holder["stt"].heard[0].dtype == np.float32 and holder["stt"].heard[0].size == 8000
        assert _call(port, "POST", "/stt", _wav(rate=44100))[0] == 400
        assert _call(port, "POST", "/stt", _wav(channels=2))[0] == 400
        assert _call(port, "POST", "/stt", b"not a wav at all")[0] == 400
        assert _call(port, "GET", "/nothing")[0] == 404
        assert _call(port, "GET", "/health", host="evil.example.com")[0] == 403  # DNS rebinding
        assert server.served == 1
    finally:
        server.stop()


class PromptStt(FakeStt):
    def transcribe(self, audio, prompt=""):
        self.heard.append(prompt)
        return "Xardas czeka"


def test_other_apps_can_give_whisper_their_names():
    stt = PromptStt()
    server = SttServer(lambda: stt, port=0)
    assert server.start()
    try:
        code, out = _call(server.port, "POST", "/stt?prompt=Xardas%2C%20Lester%2C%20Khorinis", _wav())
        assert code == 200 and out["text"] == "Xardas czeka" and stt.heard == ["Xardas, Lester, Khorinis"]
    finally:
        server.stop()


def test_a_busy_port_does_not_stop_the_app():
    first = SttServer(lambda: None, port=0)
    assert first.start()
    try:
        assert SttServer(lambda: None, port=first.port).start() is False
    finally:
        first.stop()


def test_read_wav_gives_float_samples():
    x = read_wav(_wav(0.25))
    assert x.dtype == np.float32 and x.size == 4000 and float(np.abs(x).max()) <= 1.0


# --------------------------------------------------------------------------- Gemini for the assistant

def test_messages_become_gemini_turns():
    system, contents = to_gemini([
        {"role": "system", "content": "Jesteś Iskra."}, {"role": "system", "content": "Fakty: brak."},
        {"role": "user", "content": "Hej"}, {"role": "assistant", "content": "No hej!"},
        {"role": "user", "content": "Co robimy?"}, {"role": "user", "content": "Szybko."},
        {"role": "assistant", "content": "prefill"}])
    assert system == "Jesteś Iskra.\n\nFakty: brak."
    assert [(c["role"], c["parts"][0]["text"]) for c in contents] == [
        ("user", "Hej"), ("model", "No hej!"), ("user", "Co robimy?\nSzybko.")]  # merged, no model turn last


def _gemini(handler):
    seen = []

    def wrapped(request):
        seen.append(request)
        return handler(request)

    client = httpx.Client(transport=httpx.MockTransport(wrapped))
    return GeminiLLM("secret-key-123", client=client), seen


def test_gemini_request_uses_low_thinking_the_header_key_and_no_temperature():
    answer = {"candidates": [{"content": {"role": "model", "parts": [
        {"text": "myślę...", "thought": True}, {"text": "Hogger stoi w Elwynn.", "thoughtSignature": "abc"}]},
        "finishReason": "STOP"}], "usageMetadata": {"thoughtsTokenCount": 12}, "modelVersion": "gemini-3.8-flash"}
    g, seen = _gemini(lambda r: httpx.Response(200, json=answer))
    text, info = g.chat([{"role": "system", "content": "Iskra"}, {"role": "user", "content": "Gdzie Hogger?"}],
                        max_tokens=120, temperature=0.8)
    assert text == "Hogger stoi w Elwynn." and info["finish"] == "STOP"
    req = seen[0]
    assert str(req.url) == "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.8-flash:generateContent"
    assert "secret-key-123" not in str(req.url) and req.headers["x-goog-api-key"] == "secret-key-123"
    body = json.loads(req.content)
    assert body["generationConfig"]["thinkingConfig"] == {"thinkingLevel": "low"}
    assert "temperature" not in json.dumps(body) and body["systemInstruction"]["parts"][0]["text"] == "Iskra"


class FakeLocal:
    model_name = "Nemotron"

    def __init__(self):
        self.calls = 0

    def chat(self, messages, max_tokens=200, temperature=0.0, stop=None, timeout=None):
        self.calls += 1
        return "lokalnie", {}


def test_gemini_errors_fall_back_to_the_local_model_and_rest():
    g, seen = _gemini(lambda r: httpx.Response(429, json={"error": {"code": 429, "status": "RESOURCE_EXHAUSTED",
                                                                     "message": "quota"}}))
    local = FakeLocal()
    llm = FallbackLLM(g, local)
    msgs = [{"role": "user", "content": "Hej"}]
    assert llm.chat(msgs)[0] == "lokalnie" and len(seen) == 1
    assert llm.chat(msgs)[0] == "lokalnie" and len(seen) == 1  # resting: Gemini is not asked again at once
    assert not g.available() and local.calls == 2 and llm.fallbacks == 2
    empty, _ = _gemini(lambda r: httpx.Response(200, json={"candidates": [{"finishReason": "SAFETY"}]}))
    with pytest.raises(GeminiError, match="SAFETY"):
        empty.chat(msgs)


def test_gemini_is_used_only_when_chosen_and_the_key_is_there(monkeypatch):
    class Cfg(dict):
        def get(self, k, d=None):
            return super().get(k, d)

    local = FakeLocal()
    assert assistant_llm(Cfg({"assistant.llm": "local"}), local) is local
    monkeypatch.delenv("GOOGLE_AI_API_KEY", raising=False)
    assert assistant_llm(Cfg({"assistant.llm": "gemini"}), local) is local  # no key: stays local
    monkeypatch.setenv("GOOGLE_AI_API_KEY", "k")
    chosen = assistant_llm(Cfg({"assistant.llm": "gemini", "gemini": {"model": "gemini-3.8-flash"}}), local)
    assert isinstance(chosen, FallbackLLM) and chosen.model_name == "gemini-3.8-flash (zapas: Nemotron)"


# --------------------------------------------------------------------------- Google's vectors for the names of the moment

def _embedder(model="gemini-embedding-2", status=200, text=""):
    seen = []

    def handler(request):
        seen.append(request)
        body = json.loads(request.content)
        if status != 200:
            return httpx.Response(status, json={"error": {"status": "RESOURCE_EXHAUSTED", "message": text}})
        return httpx.Response(200, json={"embeddings": [{"values": [3.0, 4.0] + [0.0] * 766}
                                                        for _ in body["requests"]]})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    return GeminiEmbedder("secret-key-123", model, client=client), seen


def test_gemini_vectors_take_the_task_as_text_and_one_call_per_question():
    emb, seen = _embedder()
    assert emb.doc_text("Gnarled Short Staff", "przedmiot: kostur") == "title: Gnarled Short Staff | text: przedmiot: kostur"
    docs, q = emb.embed_with_query([emb.doc_text("A", "b"), emb.doc_text("C", "d")], "czy ten kij jest lepszy?")
    assert len(seen) == 1 and docs.shape == (2, 768) and q.shape == (768,)
    req = seen[0]
    assert req.url.path.endswith("/models/gemini-embedding-2:batchEmbedContents")
    assert req.headers["x-goog-api-key"] == "secret-key-123" and "secret-key-123" not in str(req.url)
    body = json.loads(req.content)["requests"]
    assert body[-1]["content"]["parts"][0]["text"] == "task: search result | query: czy ten kij jest lepszy?"
    assert body[0]["outputDimensionality"] == 768 and "taskType" not in body[0]
    assert abs(float(np.linalg.norm(q)) - 1.0) < 1e-6 and abs(float(q[0]) - 0.6) < 1e-6  # normalised


def test_the_older_gemini_vectors_get_task_types():
    emb, seen = _embedder("gemini-embedding-001")
    emb.query("quest z trollami")
    emb.embed(["The Troll Cave. quest"])
    assert [json.loads(r.content)["requests"][0]["taskType"] for r in seen] == ["RETRIEVAL_QUERY", "RETRIEVAL_DOCUMENT"]
    assert json.loads(seen[0].content)["requests"][0]["content"]["parts"][0]["text"] == "quest z trollami"


def test_a_limit_makes_the_vectors_rest():
    emb, seen = _embedder(status=429, text="Quota exceeded per day")
    with pytest.raises(GeminiError):
        emb.query("hej")
    assert not emb.available() and emb.failures == 1


def test_google_vectors_are_used_while_iskra_talks_through_gemini(monkeypatch):
    class C(dict):
        def get(self, k, d=None):
            return super().get(k, d)

    monkeypatch.setenv("GOOGLE_AI_API_KEY", "k" * 40)
    assert isinstance(lexicon_embedder(C({"assistant.llm": "gemini"})), GeminiEmbedder)
    assert lexicon_embedder(C({"assistant.llm": "local"})) is None  # auto: only with Gemini
    assert isinstance(lexicon_embedder(C({"assistant.llm": "local", "lexicon.embedder": "google"})), GeminiEmbedder)
    assert lexicon_embedder(C({"assistant.llm": "gemini", "lexicon.embedder": "local"})) is None
    monkeypatch.delenv("GOOGLE_AI_API_KEY")
    assert lexicon_embedder(C({"assistant.llm": "gemini"})) is None  # no key: the local e5
