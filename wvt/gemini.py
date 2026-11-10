"""Google Gemini for the assistant only (``assistant.llm: "gemini"``); translations stay on the local model.

Plain REST (``generateContent``), no SDK.  What the client relies on was checked in Google's docs on 2026-11-09
(aistudio.google.com/docs, formerly ai.google.dev):

* model ``gemini-3.8-flash`` (stable since 2026-09-02);
* thinking cannot be switched off on 3.8 Flash: the lowest level is ``low``
  (``generationConfig.thinkingConfig.thinkingLevel``; ``minimal`` is an error);
* temperature, topP and topK are ignored by Gemini 3.6+ and are to become 400 errors, so they are never sent;
* ``maxOutputTokens`` counts thinking tokens too: a small cap returns MAX_TOKENS with an empty answer, so it stays
  generous and the length of her answers comes from the prompt and :func:`wvt.assistant.clean_reply`;
* the key travels in the ``x-goog-api-key`` header, never in the URL; the last turn must be the user's, not empty;
* 429 / 5xx mean a rate limit or overload (try later); 400 / 401 / 402 / 403 / 404 a bad key, no credit, no access or
  no such model (retrying does not help).

Any failure costs only that one answer: :class:`FallbackLLM` asks the local model instead, and after a limit or a
key error Gemini rests for a while.  The key is never logged.

:class:`GeminiEmbedder` gives the names of the moment (wvt/lexicon.py) Google's vectors instead of the local
e5-small, which could not tell them apart (see its docstring for the measurement).
"""

from __future__ import annotations

import logging
import time
from typing import Optional

import httpx
import numpy as np

log = logging.getLogger(__name__)

API_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
EMBED_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:batchEmbedContents"
# how long Gemini rests after an error before it is asked again (seconds)
REST_AFTER = {429: 60, 500: 20, 503: 20, 504: 20, 0: 20, 400: 1800, 401: 1800, 402: 1800, 403: 1800, 404: 1800}


def rest_seconds(status: int, reason: str = "") -> int:
    if status == 429 and "day" in (reason or "").lower():  # the daily quota is gone
        return 3600
    return REST_AFTER.get(status, 60)


class GeminiError(RuntimeError):
    def __init__(self, status: int, reason: str):
        super().__init__(f"Gemini {status or 'connection'}: {reason}")
        self.status = status


def to_gemini(messages: list[dict]) -> tuple[str, list[dict]]:
    """OpenAI-style messages -> (system instruction, contents): roles user/model, alternating, the user's turn last."""
    system = "\n\n".join(str(m.get("content") or "").strip() for m in messages
                         if m.get("role") == "system" and str(m.get("content") or "").strip())
    contents: list[dict] = []
    for m in messages:
        if m.get("role") == "system":
            continue
        role = "model" if m.get("role") == "assistant" else "user"
        text = str(m.get("content") or "").strip()
        if not text:
            continue
        if contents and contents[-1]["role"] == role:
            contents[-1]["parts"][0]["text"] += "\n" + text  # turns must alternate
        else:
            contents.append({"role": role, "parts": [{"text": text}]})
    while contents and contents[-1]["role"] != "user":
        contents.pop()  # a request ending with the model's turn is a 400 since Gemini 3.6
    return system, contents


def _reason(r: httpx.Response) -> str:
    try:
        err = r.json().get("error", {})
        return f"{err.get('status', '')} {str(err.get('message', ''))[:160]}".strip() or r.reason_phrase
    except ValueError:
        return r.reason_phrase


class GeminiLLM:
    """The ``chat`` interface of :class:`wvt.llm.LocalLLM`, answered by Gemini."""

    def __init__(self, api_key: str, model: str = "gemini-3.8-flash", thinking_level: str = "low",
                 timeout_s: float = 15.0, max_output_tokens: int = 4096, client: Optional[httpx.Client] = None):
        self._key = (api_key or "").strip()
        self.model = model
        self.thinking_level = thinking_level
        self.timeout_s = float(timeout_s)
        self.max_output_tokens = int(max_output_tokens)
        self._client = client or httpx.Client(timeout=self.timeout_s)
        self._resting_until = 0.0
        self.calls = 0
        self.failures = 0
        self.last_ms = 0

    @property
    def model_name(self) -> str:
        return self.model

    def available(self) -> bool:
        return bool(self._key) and time.monotonic() >= self._resting_until

    def _rest(self, status: int, reason: str = "") -> None:
        pause = rest_seconds(status, reason)
        self._resting_until = time.monotonic() + pause
        if pause >= 600:
            log.warning("Gemini answered %s - check GOOGLE_AI_API_KEY, billing and the model name; local model for "
                        "the next %d minutes", status, pause // 60)

    supports_thinking = True

    def chat(self, messages: list[dict], max_tokens: int = 200, temperature: float = 0.0,
             stop: list[str] | None = None, timeout: float | None = None,
             thinking: Optional[str] = None) -> tuple[str, dict]:
        """``thinking``: this answer only ("medium" when she plans what you do next)."""
        # temperature and stop are not sent: Gemini 3.6+ ignores sampling settings; the prompt sets the length
        system, contents = to_gemini(messages)
        if not contents:
            raise GeminiError(400, "nothing to answer")
        body: dict = {"contents": contents,
                      "generationConfig": {"thinkingConfig": {"thinkingLevel": thinking or self.thinking_level},
                                           "maxOutputTokens": self.max_output_tokens}}
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}
        self.calls += 1
        t0 = time.perf_counter()
        try:
            r = self._client.post(API_URL.format(model=self.model), json=body, timeout=self.timeout_s,
                                  headers={"x-goog-api-key": self._key, "Content-Type": "application/json"})
        except httpx.HTTPError as exc:
            self.failures += 1
            self._rest(0)
            raise GeminiError(0, type(exc).__name__) from None
        self.last_ms = round((time.perf_counter() - t0) * 1000)
        if r.status_code != 200:
            self.failures += 1
            reason = _reason(r)
            self._rest(r.status_code, reason)
            raise GeminiError(r.status_code, reason)
        data = r.json()
        cand = (data.get("candidates") or [{}])[0]
        parts = (cand.get("content") or {}).get("parts") or []
        text = "".join(str(p.get("text", "")) for p in parts if not p.get("thought")).strip()
        finish = cand.get("finishReason", "")
        if not text:
            self.failures += 1
            block = (data.get("promptFeedback") or {}).get("blockReason", "")
            raise GeminiError(200, f"empty answer ({finish or block or 'no candidate'})")
        return text, {"ms": self.last_ms, "finish": finish, "usage": data.get("usageMetadata", {}),
                      "model": data.get("modelVersion", self.model)}

    def close(self) -> None:
        self._client.close()


class FallbackLLM:
    """Gemini first; the local model when Gemini fails, rests or has no key (one answer is never lost)."""

    def __init__(self, primary: GeminiLLM, fallback):
        self.primary = primary
        self.fallback = fallback
        self.fallbacks = 0

    @property
    def model_name(self) -> str:
        return f"{self.primary.model_name} (zapas: {getattr(self.fallback, 'model_name', 'lokalny')})"

    supports_thinking = True

    def chat(self, messages: list[dict], max_tokens: int = 200, temperature: float = 0.0,
             stop: list[str] | None = None, timeout: float | None = None,
             thinking: Optional[str] = None) -> tuple[str, dict]:
        if self.primary.available():
            try:
                return self.primary.chat(messages, max_tokens=max_tokens, temperature=temperature, stop=stop,
                                         timeout=timeout, thinking=thinking)
            except GeminiError as exc:
                log.info("%s - the local model answers this one", exc)
        self.fallbacks += 1
        return self.fallback.chat(messages, max_tokens=max_tokens, temperature=temperature, stop=stop, timeout=timeout)


class GeminiEmbedder:
    """Google text vectors for the names of the moment, with the interface of :class:`wvt.kb.embedder.Embedder`
    (``embed`` for entries, ``query`` for a question) plus ``doc_text`` and ``embed_with_query``.

    ``gemini-embedding-2`` (generally available since 2026-04-22, >100 languages) takes the task as text:
    ``task: search result | query: ...`` for a question, ``title: ... | text: ...`` for an entry, and its 768
    dimensions come normalised.  ``gemini-embedding-001`` takes ``taskType`` and is normalised here.

    Measured 2026-11-10 on 76 entries of a Dun Morogh game (12 quests, 13 bag items, people, NPCs), 12 Polish
    descriptions of your items ("co to za ząb?"), 10 of your quests ("quest z trollami") and 8 small-talk lines:

    ==========================  ===============  ================  =================================
    model                       item first       quest first       small talk vs. the right answer
    ==========================  ===============  ================  =================================
    multilingual-e5-small       8/12             5/10              0.82 vs 0.83: no threshold works
    gemini-embedding-2          11/12            10/10             0.61 vs 0.72-0.76: one threshold
    gemini-embedding-001        12/12            10/10             0.60 vs 0.71-0.74: one threshold
    ==========================  ===============  ================  =================================

    The lowest right answer scored 0.674 (items) and 0.708 (quests), the highest small talk 0.628 and 0.659 - hence
    ``lexicon.item_min`` 0.65 and ``lexicon.quest_min`` 0.69.  One call (the question and any new entries) ~350 ms.
    Free tier: free, but Google may use the texts to improve its products; paid $0.20 per million tokens."""

    def __init__(self, api_key: str, model: str = "gemini-embedding-2", dims: int = 768, timeout_s: float = 6.0,
                 client: Optional[httpx.Client] = None):
        self._key = (api_key or "").strip()
        self.model = model
        self.dims = int(dims)
        self.timeout_s = float(timeout_s)
        self._client = client or httpx.Client(timeout=self.timeout_s)
        self._resting_until = 0.0
        self.calls = 0
        self.failures = 0
        self.last_ms = 0

    @property
    def model_name(self) -> str:
        return self.model

    @property
    def _v2(self) -> bool:
        return not self.model.startswith("gemini-embedding-001")

    def available(self) -> bool:
        return bool(self._key) and time.monotonic() >= self._resting_until

    def doc_text(self, title: str, text: str) -> str:
        return f"title: {title} | text: {text or 'none'}" if self._v2 else f"{title}. {text}"

    def _query_text(self, text: str) -> str:
        return f"task: search result | query: {text}" if self._v2 else text

    def _requests(self, texts: list[str], task: str) -> list[dict]:
        out = []
        for t in texts:
            r: dict = {"model": f"models/{self.model}", "content": {"parts": [{"text": t}]},
                       "outputDimensionality": self.dims}
            if not self._v2:
                r["taskType"] = task
            out.append(r)
        return out

    def _post(self, requests: list[dict]) -> np.ndarray:
        self.calls += 1
        t0 = time.perf_counter()
        try:
            r = self._client.post(EMBED_URL.format(model=self.model), json={"requests": requests},
                                  timeout=self.timeout_s, headers={"x-goog-api-key": self._key})
        except httpx.HTTPError as exc:
            self.failures += 1
            self._resting_until = time.monotonic() + rest_seconds(0)
            raise GeminiError(0, type(exc).__name__) from None
        self.last_ms = round((time.perf_counter() - t0) * 1000)
        if r.status_code != 200:
            self.failures += 1
            reason = _reason(r)
            self._resting_until = time.monotonic() + rest_seconds(r.status_code, reason)
            raise GeminiError(r.status_code, reason)
        vecs = np.array([e.get("values") or [] for e in r.json().get("embeddings") or []], dtype=np.float32)
        if vecs.ndim != 2 or vecs.shape[0] != len(requests):
            raise GeminiError(200, f"unexpected vectors {vecs.shape}")
        return vecs / np.maximum(np.linalg.norm(vecs, axis=1, keepdims=True), 1e-12)

    def embed(self, texts: list[str], prefix: str = "passage: ") -> np.ndarray:
        """Entries, already in :meth:`doc_text` form (``prefix`` is e5's and is ignored)."""
        parts = [self._post(self._requests(texts[i:i + 100], "RETRIEVAL_DOCUMENT")) for i in range(0, len(texts), 100)]
        return np.vstack(parts) if parts else np.zeros((0, self.dims), dtype=np.float32)

    def query(self, text: str) -> np.ndarray:
        return self._post(self._requests([self._query_text(text)], "RETRIEVAL_QUERY"))[0]

    def embed_with_query(self, docs: list[str], text: str) -> tuple[np.ndarray, np.ndarray]:
        """New entries and the question in one call: one round trip per question."""
        if len(docs) >= 100:
            return self.embed(docs), self.query(text)
        vecs = self._post(self._requests(docs, "RETRIEVAL_DOCUMENT")
                          + self._requests([self._query_text(text)], "RETRIEVAL_QUERY"))
        return vecs[:-1], vecs[-1]

    def close(self) -> None:
        self._client.close()


def lexicon_embedder(cfg) -> Optional[GeminiEmbedder]:
    """Google's vectors for the names of the moment: ``lexicon.embedder`` "google", or "auto" while Iskra talks
    through Gemini (the same key and the same choice to send her questions to Google); None = the local e5."""
    mode = str(cfg.get("lexicon.embedder", "auto") or "auto").lower()
    if mode == "local" or (mode == "auto" and str(cfg.get("assistant.llm", "local") or "local").lower() != "gemini"):
        return None
    from .envfile import secret

    env = (cfg.get("gemini", {}) or {}).get("api_key_env", "GOOGLE_AI_API_KEY")
    key = secret(env)
    if not key:
        log.info("lexicon.embedder wants Google vectors, but there is no %s in .env - the local e5 is used", env)
        return None
    return GeminiEmbedder(key, str(cfg.get("lexicon.embedder_model", "gemini-embedding-2") or "gemini-embedding-2"),
                          int(cfg.get("lexicon.embedder_dims", 768) or 768),
                          float(cfg.get("lexicon.embedder_timeout_s", 6) or 6))


def assistant_llm(cfg, local):
    """The model the assistant talks with: Gemini (with the local model behind it) or just the local model."""
    if str(cfg.get("assistant.llm", "local") or "local").lower() != "gemini":
        return local
    from .envfile import secret

    g = cfg.get("gemini", {}) or {}
    key = secret(g.get("api_key_env", "GOOGLE_AI_API_KEY"))
    if not key:
        log.warning("assistant.llm is gemini, but there is no %s in .env - the local model answers",
                    g.get("api_key_env", "GOOGLE_AI_API_KEY"))
        return local
    gemini = GeminiLLM(key, g.get("model", "gemini-3.8-flash"), g.get("thinking_level", "low"),
                       float(g.get("timeout_s", 15)), int(g.get("max_output_tokens", 4096)))
    return FallbackLLM(gemini, local)
