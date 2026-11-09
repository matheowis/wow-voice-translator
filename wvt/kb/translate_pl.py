"""Polish versions of the quest texts, so the knowledge base can be searched in Polish.

The local LLM translates once; results are cached in ``data/kb/raw/quests_pl.json`` (key: SHA-1 of
the English text), so a rebuild only translates new or changed quests.

WoW names stay in English - there is no Polish client and players use the English names. Every name
the text mentions (NPCs, items, objects, zones, quests of the world data) is wrapped in «» before
translation; an answer that drops or changes a protected name is rejected and retried alone, and a
quest whose translation keeps failing simply has no Polish text.
"""

from __future__ import annotations

import concurrent.futures
import hashlib
import json
import logging
import re
import threading
from pathlib import Path
from typing import Callable, Iterable, Optional

log = logging.getLogger(__name__)

SYSTEM = (
    "Tłumaczysz teksty questów z World of Warcraft z angielskiego na polski. Tłumacz naturalnie i krótko, "
    "zwracając się do gracza per ty. Fragmenty w «» to nazwy z gry: przepisz je dokładnie, razem z «», bez "
    "odmiany i bez tłumaczenia. Liczby zostaw. Odpowiedz wyłącznie tłumaczeniami: na każdą linię wejścia "
    "jedna linia wyjścia z tym samym numerem, w formacie '3. tłumaczenie'."
)
_SHOTS = [
    ("1. Bring 8 «Kobold Candle» to «Remy \"Two Times\"» in «Goldshire».\n2. Kobold Candles",
     "1. Przynieś 8 «Kobold Candle» do «Remy \"Two Times\"» w «Goldshire».\n2. Świece koboldów"),
]
_LINE = re.compile(r"^\s*(\d{1,3})\s*[.)]\s*(.*?)\s*$")
_GUARD = re.compile(r"«[^»]*»")
_WORD = re.compile(r"[A-Za-z][A-Za-z'\-]*|\"[^\"]+\"")


def sha(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


class NameGuard:
    """Finds WoW names in English text (longest first, whole words) and wraps them in «»."""

    def __init__(self, names: Iterable[str], max_words: int = 6):
        self.names = {n.strip() for n in names if n and len(n.strip()) >= 3}
        self.max_words = max_words

    def protect(self, text: str, extra: Iterable[str] = ()) -> str:
        names = self.names | {n for n in extra if n and len(n) >= 3}
        spans: list[tuple[int, int]] = []
        tokens = [(m.start(), m.end()) for m in re.finditer(r"\S+", text)]
        i = 0
        while i < len(tokens):
            hit = None
            for n in range(min(self.max_words, len(tokens) - i), 0, -1):
                a, b = tokens[i][0], tokens[i + n - 1][1]
                piece = text[a:b]
                core = piece.rstrip(".,;:!?)").lstrip("(")
                if core in names:
                    start = a + piece.index(core)
                    hit = (start, start + len(core), n)
                    break
            if hit:
                spans.append(hit[:2])
                i += hit[2]
            else:
                i += 1
        out, last = [], 0
        for a, b in spans:
            out.append(text[last:a])
            out.append(f"«{text[a:b]}»")
            last = b
        out.append(text[last:])
        return "".join(out)

    _CAPS = re.compile(r"(?<![.!?«]\s)(?<=\s)([A-Z][\w'\-]+(?:\s+(?:of|the)\s+[A-Z][\w'\-]+|\s+[A-Z][\w'\-]+)*)")

    def protect_caps(self, guarded: str) -> str:
        """Also guard capitalised names the world data does not know ("the Fargodeep Mine", "Splintertree Post"),
        except at the start of a sentence."""
        parts = re.split(r"(«[^»]*»)", guarded)
        out = []
        for part in parts:
            if part.startswith("«"):
                out.append(part)
            else:
                out.append(self._CAPS.sub(lambda m: f"«{m.group(1)}»", part))
        return "".join(out)


def unguard(text: str) -> str:
    return text.replace("«", "").replace("»", "")


def guards_kept(src: str, out: str) -> bool:
    return sorted(_GUARD.findall(src)) == sorted(_GUARD.findall(out)) and "«" not in _GUARD.sub("", out)


class QuestTranslator:
    def __init__(self, llm, cache_path: Path, workers: int = 2, batch: int = 8):
        self.llm = llm
        self.cache_path = Path(cache_path)
        self.workers = workers
        self.batch = batch
        self._lock = threading.Lock()
        self.cache: dict[str, Optional[str]] = {}
        if self.cache_path.exists():
            try:
                self.cache = json.loads(self.cache_path.read_text(encoding="utf-8"))
            except ValueError:
                self.cache = {}
        self.failed = 0

    def save(self) -> None:
        with self._lock:
            data = json.dumps(self.cache, ensure_ascii=False, indent=0)
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.cache_path.with_suffix(".tmp")
        tmp.write_text(data, encoding="utf-8")
        tmp.replace(self.cache_path)

    def _ask(self, lines: list[str]) -> dict[int, str]:
        user = "\n".join(f"{i}. {t}" for i, t in enumerate(lines, 1))
        msgs = [{"role": "system", "content": SYSTEM}]
        for q, a in _SHOTS:
            msgs += [{"role": "user", "content": q}, {"role": "assistant", "content": a}]
        msgs.append({"role": "user", "content": user})
        text, _ = self.llm.chat(msgs, max_tokens=80 + 70 * len(lines), temperature=0.0, timeout=120)
        out = {}
        for ln in (text or "").splitlines():
            m = _LINE.match(ln)
            if m:
                out[int(m.group(1))] = m.group(2)
        return out

    def _run_batch(self, items: list[tuple[str, str]]) -> None:
        """items: (cache key, guarded English text)."""
        try:
            got = self._ask([t for _, t in items])
        except Exception as e:  # noqa: BLE001 - one batch failing must not stop the rest
            log.warning("translation batch failed: %s", e)
            got = {}
        retry = []
        for i, (key, src) in enumerate(items, 1):
            out = got.get(i, "")
            if out and guards_kept(src, out):
                with self._lock:
                    self.cache[key] = unguard(out)
            else:
                retry.append((key, src))
        for key, src in retry if len(items) > 1 else []:
            try:
                out = self._ask([src]).get(1, "")
            except Exception:  # noqa: BLE001
                out = ""
            with self._lock:
                if out and guards_kept(src, out):
                    self.cache[key] = unguard(out)
                else:
                    self.cache[key] = None  # keep the English text; do not try again on every rebuild
                    self.failed += 1
        if len(items) == 1 and retry:
            with self._lock:
                self.cache[items[0][0]] = None
                self.failed += 1

    def translate(self, texts: dict[str, str], progress: Optional[Callable[[int, int], None]] = None,
                  save_every: int = 20) -> dict[str, Optional[str]]:
        """texts: {cache key: guarded English text}. Returns {key: Polish or None} (cache included)."""
        todo = [(k, t) for k, t in texts.items() if k not in self.cache]
        batches = [todo[i:i + self.batch] for i in range(0, len(todo), self.batch)]
        done = 0
        with concurrent.futures.ThreadPoolExecutor(max_workers=self.workers) as pool:
            futures = [pool.submit(self._run_batch, b) for b in batches]
            for n, f in enumerate(concurrent.futures.as_completed(futures), 1):
                f.result()
                done += 1
                if progress:
                    progress(done, len(batches))
                if n % save_every == 0:
                    self.save()
        self.save()
        return {k: self.cache.get(k) for k in texts}


# --------------------------------------------------------------------------- what to translate

def quest_sources(world, guard: NameGuard) -> dict[str, str]:
    """Cache key -> guarded English text: each quest title and each quest's objective text."""
    out: dict[str, str] = {}
    for qid, q in world.quests.items():
        extra = [world.npcs[n]["n"] for n in q.get("sn", []) + q.get("en", []) + [k for k, _ in q.get("kill", [])]
                 if n in world.npcs]
        extra += [world.items[i]["n"] for i, _ in q.get("loot", []) if i in world.items]
        extra += [world.objects[o]["n"] for o in q.get("so", []) + q.get("eo", []) if o in world.objects]
        title = q["n"]
        out["t:" + sha(title)] = guard.protect(title, [n for n in extra if n != title])
        if q.get("txt"):
            text = " ".join(q["txt"])[:600]
            out["o:" + sha(text)] = guard.protect_caps(guard.protect(text, extra + [title]))
    return out


def world_names(world) -> list[str]:
    names = [n["n"] for n in world.npcs.values()] + [o["n"] for o in world.objects.values()]
    names += [i["n"] for i in world.items.values()] + list(world.names.values())
    names += [d["n"] for d in world.dungeons.values()]
    return names


def llm_for_build(cfg):
    """(llm, started_here): the running llama-server (the app's) or a new one for the build."""
    from ..llm import LocalLLM

    llm = LocalLLM({**cfg["llm"], "mode": "external"})
    try:
        if llm.client.get("/health", timeout=2).status_code == 200:
            return llm, False
    except Exception:  # noqa: BLE001 - nothing listening: start our own
        pass
    llm = LocalLLM(cfg["llm"])
    llm.start()
    return llm, True


def translate_world(world, llm, cache_path: Path, progress=None) -> dict[str, Optional[str]]:
    guard = NameGuard(world_names(world))
    tr = QuestTranslator(llm, cache_path)
    out = tr.translate(quest_sources(world, guard), progress=progress)
    log.info("quest translation: %d texts, %d kept in English", len(out), tr.failed)
    return out


def load_cache(cache_path: Path) -> dict[str, Optional[str]]:
    try:
        return json.loads(Path(cache_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
