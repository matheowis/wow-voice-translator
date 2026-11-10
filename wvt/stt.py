"""Speech-to-text via onnx-asr: Whisper large-v3-turbo by default, NVIDIA Parakeet TDT 0.6B v3 as the fallback.

Whisper runs as the ONNX export ``onnx-community/whisper-large-v3-turbo`` (MIT) in fp16 on CUDA, with Polish set
on every call: without ``language="pl"`` it guesses the language for each utterance.  Parakeet
(``istupakov/parakeet-tdt-0.6b-v3-onnx``, CC-BY-4.0) takes over when Whisper cannot run on the GPU: on CUDA when
there is room for it, otherwise int8 on the CPU.  Whisper is never run on the CPU while Parakeet is there
(about 10 s per line).

Measured on the user's 85 in-game recordings (RØDE PodMic, colloquial Polish; reference: ElevenLabs Scribe
transcripts; RTX 5080; 2026-11-09, in the Gothic voice chat project):

====================================  ==============  ===========  =====================
model                                 wrong words     short line   memory
====================================  ==============  ===========  =====================
Whisper large-v3-turbo fp16, CUDA     10.4 %          0.21 s       ~2.5 GB VRAM
Parakeet TDT 0.6B v3 fp32, CUDA       12.0 %          ~0.1 s       ~3.7 GB VRAM
Parakeet v3 int8, CPU                 17.5 %          0.24 s       1.2 GB RAM
====================================  ==============  ===========  =====================

Whisper writes a sentence nobody said when it gets silence, noise or a breath ("Dziękuję za uwagę.", "Napisy
stworzone przez społeczność Amara.org"...).  The pipeline only gives it recordings with speech, and a result that is
nothing but one of those phrases is dropped (``stt.drop_phrases``).

**Names of the moment.**  ``transcribe(audio, prompt=...)`` gives Whisper the names that matter right now
(:mod:`wvt.lexicon`: your quests, the people and places around you, your loot) as its "previous text", so it
spells them the way the game does.  Measured 2026-11-09 (RTX 5080, fp16): 56 Polish lines with WoW names, 2 voices -
52/82 names right without a prompt, 62-63/82 with ~200 prompt tokens; the user's 96 Gothic notes (no WoW names at
all) 11.1 % -> 10.0 % wrong words, so the prompt does not hurt normal speech; +5 ms.  On silence or noise a prompted
Whisper reels off the prompt ("Tannok Frosthammer, Tannok Frosthammer, ..."): the output is capped by the length of
the recording, and a result that repeats itself, is far too long or is only the prompt's names is recognised
again without the prompt.
"""

from __future__ import annotations

import logging
import re
import threading
import time
import types
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np

from .config import resolve_path
from .gpu import preload_cuda_dlls

log = logging.getLogger(__name__)

# typical Whisper hallucinations on silence, noise or a breath: a result that is only one of them is dropped
DEFAULT_DROP_PHRASES = (
    "Dziękuję za uwagę.", "Dziękuję.", "Dzięki za obejrzenie.", "Napisy stworzone przez społeczność Amara.org",
    "Napisy wykonane przez społeczność Amara.org", "Subskrybuj!", "Do zobaczenia.", "Thank you.",
    "Thanks for watching!",
)
# measured on RTX 5080 with the CUDA options below: cuDNN heuristics and an arena that grows only as needed
CUDA_OPTIONS = {"arena_extend_strategy": "kSameAsRequested", "cudnn_conv_algo_search": "HEURISTIC"}


def normalize_phrase(text: str) -> str:
    """Lower case, no punctuation, single spaces: how results are compared with the drop list."""
    return " ".join(re.sub(r"[^\w\s]", " ", (text or "").lower()).split())


def looks_runaway(text: str, seconds: float, prompt: str = "") -> bool:
    """A result Whisper made up rather than heard: far more words than anybody says in ``seconds``, the same
    phrase three times in a row, or nothing but the prompt's own names."""
    words = normalize_phrase(text).split()
    if not words:
        return False
    if len(words) > 6 + 6 * seconds:
        return True
    for n in (1, 2, 3, 4):
        for i in range(len(words) - 3 * n + 1):
            if words[i:i + n] == words[i + n:i + 2 * n] == words[i + 2 * n:i + 3 * n]:
                return True
    if prompt:
        names = {normalize_phrase(x) for x in re.split(r"[,.]", prompt) if x.strip()}
        parts = [normalize_phrase(x) for x in re.split(r"[,.;!?]", text) if x.strip()]
        if len(parts) >= 3 and sum(p in names for p in parts) >= 0.6 * len(parts):
            return True
    return False


def collapse_repeats(text: str) -> str:
    """"a b, a b, a b, a b" -> "a b,": a loop Whisper fell into, kept once."""
    tokens = text.split()
    norm = [normalize_phrase(t) for t in tokens]
    for n in (4, 3, 2, 1):
        i = 0
        while i + 3 * n <= len(tokens):
            run = 1
            while norm[i + run * n:i + (run + 1) * n] == norm[i:i + n] and i + (run + 1) * n <= len(tokens):
                run += 1
            if run >= 3:
                del tokens[i + n:i + run * n]
                del norm[i + n:i + run * n]
            i += 1
    return " ".join(tokens)


def install_whisper_prompting(asr) -> bool:
    """Teach an onnx-asr Whisper (``model.asr``) two more options: ``prompt_ids`` (previous-text tokens before
    ``<|startoftranscript|>``) and ``max_new_tokens``.  False when this onnx-asr version looks different."""
    need = ("_encode", "_decoding", "_decode_tokens", "_transcribe_input", "_tokens")
    if not all(hasattr(asr, n) for n in need) or "<|startofprev|>" not in getattr(asr, "_tokens", {}):
        return False
    original = asr.recognize_batch

    def recognize_batch(self, waveforms, waveforms_len, /, **kwargs):
        prompt_ids = kwargs.pop("prompt_ids", None)
        max_new = kwargs.pop("max_new_tokens", None)
        language = kwargs.get("language")
        if not language or (not prompt_ids and not max_new):
            return original(waveforms, waveforms_len, **kwargs)
        encoded = self._encode(waveforms, waveforms_len)
        tokens = np.repeat(self._transcribe_input, len(waveforms), axis=0)
        tokens[:, 1] = self._tokens[f"<|{language}|>"]
        if prompt_ids:
            prev = np.array([[self._tokens["<|startofprev|>"], *prompt_ids]], dtype=np.int64)
            tokens = np.hstack((np.repeat(prev, len(waveforms), axis=0), tokens))
        start = tokens.shape[1]
        limit = min(448, start + int(max_new)) if max_new else 448
        out = self._decoding(encoded, tokens, limit)
        return map(self._decode_tokens, out[:, start:])  # the prompt is not part of the result

    asr.recognize_batch = types.MethodType(recognize_batch, asr)
    return True


@dataclass
class SttModel:
    """One onnx-asr model on disk and how to run it."""

    name: str  # onnx-asr model type: "whisper", "nemo-parakeet-tdt-0.6b-v3"
    path: Path
    gpu_quantization: Optional[str] = None  # Whisper: "fp16" (CUDA only); None = fp32 files
    cpu_quantization: Optional[str] = "int8"
    gpu_need_mb: int = 3800  # VRAM the CUDA session takes (measured)
    language: Optional[str] = None  # Whisper only: "pl" on every call

    @property
    def is_whisper(self) -> bool:
        return self.name.lower().startswith("whisper")

    @property
    def label(self) -> str:
        """"whisper-large-v3-turbo", "parakeet-tdt-0.6b-v3" - from the folder name."""
        name = self.path.name
        return name[:-5] if name.endswith("-onnx") else name

    def present(self) -> bool:
        return self.path.is_dir() and any(self.path.rglob("*.onnx"))

    def has_quantized(self, quant: Optional[str]) -> bool:
        # Parakeet: encoder-model.int8.onnx; Whisper: onnx/encoder_model_int8.onnx
        return bool(quant) and any(self.path.rglob(f"*{quant}.onnx"))


def _sessions(model) -> list:
    """The onnxruntime sessions inside an onnx-asr model (encoder, decoder...)."""
    holder = getattr(model, "asr", model)
    found = [v for v in vars(holder).values() if hasattr(v, "get_providers") and hasattr(v, "run")] \
        if hasattr(holder, "__dict__") else []
    return found


class SpeechToText:
    def __init__(self, model_dir: str, model_name: str = "whisper", device: str = "auto",
                 cpu_quantization: str | None = "int8", vram_reserve_mb: int = 2500, *, language: str = "pl",
                 gpu_quantization: str | None = None, gpu_need_mb: int | None = None,
                 cpu_fallback: dict | None = None, drop_phrases: tuple | list | None = None,
                 prompt_max_tokens: int = 200):
        whisper = model_name.lower().startswith("whisper")
        # Whisper's settings must not leak onto Parakeet when only the model name is changed in config.json:
        # Parakeet's CUDA session takes ~3.7 GB, and it has no fp16 files (see _candidates)
        need = int(gpu_need_mb or 2600) if whisper else max(int(gpu_need_mb or 0), 3800)
        self.primary = SttModel(model_name, resolve_path(model_dir),
                                gpu_quantization if gpu_quantization is not None else ("fp16" if whisper else None),
                                cpu_quantization, need, language if whisper else None)
        fb = cpu_fallback or {}
        self.fallback: Optional[SttModel] = None
        if fb.get("model_dir") and fb.get("model_name"):
            self.fallback = SttModel(fb["model_name"], resolve_path(fb["model_dir"]), fb.get("gpu_quantization"),
                                     fb.get("cpu_quantization", "int8"), int(fb.get("gpu_need_mb", 3800)))
        self.device_pref = device
        self.vram_reserve_mb = vram_reserve_mb
        self.drop_phrases = {normalize_phrase(p) for p in (DEFAULT_DROP_PHRASES if drop_phrases is None
                                                           else drop_phrases)}
        self.model = None
        self.active: Optional[SttModel] = None
        self.device = "none"
        self.device_reason = ""
        self.load_seconds = 0.0
        self.dropped = 0  # results thrown away as hallucinations
        self.prompt_max_tokens = int(prompt_max_tokens)
        self.prompt_ok = False  # Whisper with names of the moment (see install_whisper_prompting)
        self.prompt_dropped = 0  # prompted results that ran away and were recognised again without the prompt
        self._tok = None
        self._prompt_cache: tuple[str, list[int]] = ("", [])
        self._kwargs: dict = {}
        self._lock = threading.Lock()  # one recognition at a time: push-to-talk and the local STT service

    @classmethod
    def from_config(cls, s: dict) -> "SpeechToText":
        return cls(s["model_dir"], s.get("model_name", "whisper"), s.get("device", "auto"),
                   s.get("cpu_quantization", "int8"), int(s.get("vram_reserve_mb", 2500)),
                   language=s.get("language", "pl"), gpu_quantization=s.get("gpu_quantization"),
                   gpu_need_mb=s.get("gpu_need_mb"), cpu_fallback=s.get("cpu_fallback"),
                   drop_phrases=s.get("drop_phrases"), prompt_max_tokens=int(s.get("prompt_max_tokens", 200)))

    # ------------------------------------------------------------------ what is loaded
    @property
    def model_label(self) -> str:
        return (self.active or self.primary).label

    def describe(self) -> str:
        """For the window and the CLI: "whisper-large-v3-turbo na cuda-fp16"."""
        return f"{self.model_label} na {self.device}" if self.model is not None else f"{self.model_label} (nie załadowany)"

    # ------------------------------------------------------------------ loading
    def _gpu_has_room(self, need_mb: int) -> bool:
        """auto mode: use the GPU only if it leaves `vram_reserve_mb` free for the game."""
        from .gpu import vram_used_mb

        v = vram_used_mb()
        if v is None:
            self.device_reason = "brak danych NVML"
            return True
        free = v["total"] - v["used"]
        ok = free >= need_mb + self.vram_reserve_mb
        self.device_reason = (f"wolne VRAM {free:.0f} MB {'≥' if ok else '<'} "
                              f"{need_mb} (STT) + {self.vram_reserve_mb} (zapas dla gry)")
        return ok

    def _candidates(self, ort) -> list[tuple[SttModel, str, list, Optional[str]]]:
        """(model, device, providers, quantization) to try in order."""
        models = [m for m in (self.primary, self.fallback) if m is not None and m.present()]
        if not models:
            models = [self.primary]  # let onnx-asr report what is missing
        out: list[tuple[SttModel, str, list, Optional[str]]] = []
        self._reasons: dict[str, str] = {}  # why each model got (or did not get) the GPU, for the log
        if self.device_pref == "cpu":
            self.device_reason = "ustawione na CPU"
        else:
            providers = None
            for m in models:
                if self.device_pref == "auto":
                    room = self._gpu_has_room(m.gpu_need_mb)
                    self._reasons[m.label] = self.device_reason
                    if not room:
                        continue
                if providers is None:
                    preload_cuda_dlls()
                    providers = [("CUDAExecutionProvider", CUDA_OPTIONS), "CPUExecutionProvider"] \
                        if "CUDAExecutionProvider" in ort.get_available_providers() else []
                if providers:  # fp16 files only where the model has them (Whisper); fp32 otherwise
                    out.append((m, "cuda", providers,
                                m.gpu_quantization if m.has_quantized(m.gpu_quantization) else None))
        # the CPU is the last resort: Parakeet (Whisper needs ~10 s per line there), int8 when available
        cpu = next((m for m in models if not m.is_whisper), models[0])
        out.append((cpu, "cpu", ["CPUExecutionProvider"],
                    cpu.cpu_quantization if cpu.has_quantized(cpu.cpu_quantization) else None))
        return out

    def load(self) -> None:
        import onnx_asr
        import onnxruntime as ort

        t0 = time.perf_counter()
        ort.set_default_logger_severity(3)  # hide harmless "Memcpy nodes added" warnings
        errors = []
        for m, device, providers, quant in self._candidates(ort):
            try:
                model = onnx_asr.load_model(m.name, path=str(m.path), quantization=quant, providers=providers)
                if device == "cuda":
                    # onnxruntime falls back to the CPU silently when CUDA does not start - Whisper would then
                    # take ~10 s per line, and a warm-up would not notice
                    sessions = _sessions(model)
                    if not sessions:
                        log.warning("STT: cannot see the onnxruntime sessions of %s to check CUDA", m.label)
                    elif not all("CUDAExecutionProvider" in s.get_providers() for s in sessions):
                        raise RuntimeError("CUDA nie wystartowała (sesja liczyłaby na CPU)")
                kwargs = {"language": m.language} if m.language else {}
                model.recognize(np.zeros(16000, dtype=np.float32), sample_rate=16000, **kwargs)
                self.model, self.active, self._kwargs = model, m, kwargs
                self.device = device if quant is None else f"{device}-{quant}"
                if device == "cuda":
                    self.device_reason = getattr(self, "_reasons", {}).get(m.label, self.device_reason)
                break
            except Exception as exc:
                errors.append(f"{m.label} {device}: {exc}")
                log.warning("STT %s on %s failed: %s", m.label, device, exc)
        if self.model is None:
            raise RuntimeError("Nie udało się załadować rozpoznawania mowy: " + " | ".join(errors))
        self.load_seconds = time.perf_counter() - t0
        if self.active is not None and self.active.is_whisper and self.active.language:
            self._enable_prompts(self.active)
        log.info("STT ready on %s in %.1fs: %s (%s)%s", self.device, self.load_seconds, self.model_label,
                 self.device_reason, ", names of the moment on" if self.prompt_ok else "")

    def _enable_prompts(self, m: SttModel) -> None:
        tok_file = m.path / "tokenizer.json"
        try:
            from tokenizers import Tokenizer

            if not tok_file.exists() or not install_whisper_prompting(self.model.asr):
                raise RuntimeError("no tokenizer.json or an unknown onnx-asr Whisper")
            self._tok = Tokenizer.from_file(str(tok_file))
            self.prompt_ok = True
        except Exception as exc:
            self.prompt_ok = False
            log.warning("STT: Whisper without the names of the moment (%s)", exc)

    def prompt_ids(self, prompt: str) -> list[int]:
        """The prompt as Whisper tokens; the most important names are last, so a long prompt loses its start."""
        prompt = " ".join((prompt or "").split())
        if not prompt or self._tok is None:
            return []
        cached, ids = self._prompt_cache
        if cached != prompt:
            ids = self._tok.encode(" " + prompt, add_special_tokens=False).ids[-self.prompt_max_tokens:]
            self._prompt_cache = (prompt, ids)
        return list(ids)

    def warmup(self) -> None:
        if self.model is not None:
            silence = np.zeros(16000 * 2, dtype=np.float32)
            with self._lock:
                self.model.recognize(silence, sample_rate=16000, **self._kwargs)
                if self.prompt_ok:  # the first decode with a long prompt took 0.8 s instead of 0.1 - not on a question
                    self._recognize(silence, 16000, self.prompt_ids(", ".join(["World of Warcraft"] * 40)), 2.0)

    # ------------------------------------------------------------------ recognition
    def _recognize(self, audio: np.ndarray, sample_rate: int, ids: list[int], seconds: float) -> str:
        kwargs = dict(self._kwargs)
        if self.prompt_ok:
            kwargs["max_new_tokens"] = int(16 + 10 * seconds)  # no runaway loop up to 448 tokens
            if ids:
                kwargs["prompt_ids"] = ids
        text = self.model.recognize(audio, sample_rate=sample_rate, **kwargs)
        if isinstance(text, (list, tuple)):
            text = " ".join(str(t) for t in text)
        return str(text).strip()

    def transcribe(self, audio: np.ndarray, sample_rate: int = 16000, prompt: str = "") -> str:
        """Speech -> text.  ``prompt``: names Whisper should know (ignored by Parakeet)."""
        if self.model is None:
            raise RuntimeError("STT not loaded")
        if audio.size == 0:
            return ""
        audio = audio.astype(np.float32, copy=False)
        seconds = audio.size / float(sample_rate)
        ids = self.prompt_ids(prompt) if self.prompt_ok else []
        with self._lock:
            text = self._recognize(audio, sample_rate, ids, seconds)
            if ids and looks_runaway(text, seconds, prompt):
                self.prompt_dropped += 1
                log.info("STT: the prompt made Whisper make things up (%d so far), recognised again without it",
                         self.prompt_dropped)
                text = self._recognize(audio, sample_rate, [], seconds)
        if looks_runaway(text, seconds):
            text = collapse_repeats(text)
        if text and all(len(w) < 2 for w in normalize_phrase(text).split()):
            self.dropped += 1  # "D-", "A." - noise, not speech
            return ""
        if self.active is not None and self.active.is_whisper and normalize_phrase(text) in self.drop_phrases:
            self.dropped += 1
            log.info("STT: dropped a typical Whisper hallucination (%d so far)", self.dropped)  # no text: privacy
            return ""
        return text
