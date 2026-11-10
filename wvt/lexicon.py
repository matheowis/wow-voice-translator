"""The names of the moment: what speech recognition and the LLMs should know right now.

Built on demand from what the addon already sends and from the world data (nothing to set up):

* **quests** in your log, the NPCs that give and take them, the items and creatures they ask for;
* **where you are**: zone, sub-zone, the NPCs and creatures nearest to you;
* **people**: your group, whoever whispered or wrote near you, NPCs that just spoke to you;
* **items**: what you looted (with its stats when you can wear it), what is in your bags, links in chat;
* **your own words** from ``lexicon.user_file`` (one per line: friends, your guild, nicknames).

It is used four ways (measured 2026-11-09 on 56 Polish lines with WoW names read by two ElevenLabs voices, and on
the user's 96 recorded Gothic notes for ordinary speech):

1. **Whisper's prompt** - the most relevant names, the most important last (nearest to the speech), so Whisper
   spells "Beer Basted Boar Ribs" and "Steelgrill" the way the game does: 52 -> 64 of 82 names right, ordinary
   speech 11.1 -> 10.4 % wrong words, +5 ms;
2. **correction** - a name Whisper still gets wrong ("Targasa", "gnome Regan", "Kinaj") is matched by sound
   (Polish and English spelling folded into one key, Polish case endings ignored) and replaced before Iskra looks
   it up or the line is translated: 81-82 of 82 names right, no false fix in the 113 notes (4697 words), ~11 ms;
3. **a small vector index** of the entries - Google's ``gemini-embedding-2`` when Iskra talks through Gemini
   (:class:`wvt.gemini.GeminiEmbedder`), the local multilingual-e5-small otherwise and whenever Google fails.  On
   76 entries of a Dun Morogh game: Google put the right item first for 11 of 12 Polish descriptions ("co to za
   ząb?") and the right quest for 10 of 10 ("quest z trollami"), and one threshold told them from small talk; e5
   managed 8/12 and 5/10 and scored small talk as high as real questions.  So only Google's scores are acted on
   (:meth:`Lexicon.trusted`); with e5 the vectors just break ties after the kind of item and the newest loot;
4. **the LLMs** - the translator gets the names of the moment; Iskra's LLM gets a short picture of your game
   (quests with their goals, NPCs near you, loot, bags, people) and works out "ten quest z trollami" itself.
"""

from __future__ import annotations

import difflib
import logging
import math
import re
import threading
import time
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Optional

import numpy as np

log = logging.getLogger(__name__)

# --------------------------------------------------------------------------- sound keys

_LETTERS = (("ą", "on"), ("ę", "en"), ("ó", "u"), ("ś", "S"), ("ć", "C"), ("ź", "Z"), ("ż", "Z"), ("ń", "n"),
            ("ł", "v"))
_DIGRAPHS = (("dZ", "J"), ("rz", "Z"), ("sch", "S"), ("tch", "C"), ("sz", "S"), ("cz", "C"), ("sh", "S"), ("kh", "h"),
             ("ch", "h"), ("eye", "ai"), ("igh", "ai"), ("ph", "f"), ("th", "t"), ("gh", ""), ("ck", "k"),
             ("qu", "kv"), ("wh", "v"), ("ee", "i"), ("ea", "i"), ("oo", "u"), ("ou", "u"), ("ew", "u"), ("ay", "ei"),
             ("ai", "ei"), ("ey", "ei"), ("aj", "ei"), ("ej", "ei"))
_SINGLE = (("q", "k"), ("x", "ks"), ("c", "k"), ("y", "i"), ("j", "i"), ("w", "v"))
_DEVOICE = {"d": "t", "b": "p", "g": "k", "z": "s", "Z": "S", "v": "f", "J": "C"}
# Polish case endings Whisper writes on foreign names: Thargasa, Morgrimowi, Grelina Whitebearda, Elandrę...
_ENDINGS = ("owie", "owi", "ami", "ach", "ów", "om", "em", "ie", "ę", "ą", "a", "u", "y", "i", "e", "o")


def sound_key(word: str, english: bool = False) -> str:
    """One word as an approximate sound, the same for the English name and the way Whisper writes it in Polish:
    "Whitebeard" and "Łajtbird", "Thargas" and "Targas", "Keeneye" and "Kinaj", "Vagash" and "Wagasz".

    ``english``: read the spelling the English way where Polish reads it otherwise - "kh" as k (Kharanos ->
    Karanos), "ch" as cz (Chillwind -> Czilłind), "j" as dż (Jarven -> Dżarwen); names get both keys."""
    w = (word or "").lower()
    for a, b in _LETTERS:
        w = w.replace(a, b)
    w = "".join(ch for ch in unicodedata.normalize("NFKD", w) if not unicodedata.combining(ch))
    w = re.sub(r"[^a-zA-Z]", "", w)
    if w[:2] in ("gn", "kn", "pn", "wr"):
        w = w[1:]
    if english:
        w = w.replace("kh", "k").replace("tch", "C").replace("ch", "C").replace("j", "J")
    w = re.sub(r"cu(?=[aeio])", "kv", w)  # Whisper's "Cuesta" is "questa"
    for a, b in _DIGRAPHS:
        w = w.replace(a, b)
    w = re.sub(r"c(?=[eiy])", "s", w)
    for a, b in _SINGLE:
        w = w.replace(a, b)
    w = re.sub(r"(.)\1+", r"\1", w)
    if len(w) > 2 and w[-1] in _DEVOICE:
        w = w[:-1] + _DEVOICE[w[-1]]
    return w


def strip_ending(word: str) -> str:
    low = word.lower()
    for e in _ENDINGS:
        if low.endswith(e) and len(low) - len(e) >= 3:
            return word[: len(word) - len(e)]
    return word


def skeleton(key: str) -> str:
    return key[:1] + re.sub(r"[aeiou]", "", key[1:])


def similarity(a: str, b: str) -> float:
    """0..1: how alike two sound keys are, vowels counting half (Polish spellings of English vowels vary most)."""
    if a == b:
        return 1.0
    if not a or not b:
        return 0.0
    r1 = difflib.SequenceMatcher(None, a, b).ratio()
    sa, sb = skeleton(a), skeleton(b)
    r2 = difflib.SequenceMatcher(None, sa, sb).ratio() if sa and sb else r1
    return 0.5 * r1 + 0.5 * r2


def _grams(key: str) -> set[str]:
    return {key[i:i + 2] for i in range(len(key) - 1)} or {key}


def fold(text: str) -> str:
    t = (text or "").lower().replace("ł", "l")
    t = "".join(ch for ch in unicodedata.normalize("NFKD", t) if not unicodedata.combining(ch))
    return " ".join(re.sub(r"[^\w\s]", " ", t).split())


# Polish words that are never a name, however close they sound ("tarasie" is not Thargas)
COMMON = set("""
a aby ach albo ale ani az bardzo bedzie bede bo by byc byl byla bylo byli chce chcesz chodz ci cie co czy czym czyli
dla do dobra dobrze dobry dzieki dzis gdzie go ich ide idz ile im ja jak jaki jakie jakis jest jestem jeszcze jej juz
kiedy kto ktory ktora ktore ktorego mam mamy masz me mi mie mna mnie moge moj moja moje mozna moze musze my na nad nam
nas nasz nawet nic nie niech niz no o od ok oraz po pod potem prosze przez przy sa sie sobie tak tam tego tej ten
teraz to toba tobie trzeba tu tutaj ty tylko tym u w we wiec wszystko z za ze zeby zrob daj wez powiedz zapros odpisz
napisz zaprowadz pokaz znajde znalezc kupie kupic sprzedam szukam gdzies czegos kogos ktos jakas jakies wiem wiesz
lecimy idziemy chodzmy czekam czekaj uwazaj uwazajcie bije mocno zaraz jednego jedna jeden dwa trzy cztery piec
questa quest questy questow questu heal heala tank tanka dps party inv priv drop loot mob moba boss dung dungeon
lepszy lepsze gorszy warto robic zrobic oddac oddam zostalo brakuje mojego moim twoj twoja swoj swoja ktorym
kwestia kwestii kwestie kwestie kwestiach kwestiami
""".split())


GAME_WORDS = ("quest", "questa", "questy", "questów", "heal", "heala", "healer", "tank", "tanka", "priv", "inv",
              "dungeon", "loot", "drop", "boss", "raid")

# --------------------------------------------------------------------------- entries

KIND_PL = {"quest": "quest", "npc": "NPC", "mob": "potwór", "item": "przedmiot", "place": "miejsce",
           "player": "gracz", "word": "słowo"}

WEAPON_WORDS = {0: "topór, siekiera", 1: "topór dwuręczny, siekiera", 2: "łuk", 3: "strzelba, rusznica, broń palna",
                4: "buzdygan, maczuga, buława, młot", 5: "młot dwuręczny, maczuga, buława", 6: "halabarda, włócznia",
                7: "miecz", 8: "miecz dwuręczny", 10: "kostur, kij, laska", 13: "kastet, broń pięściowa",
                15: "sztylet, nóż", 16: "nóż do rzucania, broń miotana", 18: "kusza", 19: "różdżka", 20: "wędka"}
SLOT_WORDS = {"INVTYPE_HEAD": "hełm, czapka, kaptur", "INVTYPE_NECK": "naszyjnik, amulet",
              "INVTYPE_SHOULDER": "naramienniki", "INVTYPE_CHEST": "napierśnik, zbroja, kurtka, tunika",
              "INVTYPE_ROBE": "szata, zbroja", "INVTYPE_WAIST": "pas", "INVTYPE_LEGS": "spodnie, nogawice",
              "INVTYPE_FEET": "buty", "INVTYPE_WRIST": "karwasze, bransolety", "INVTYPE_HAND": "rękawice",
              "INVTYPE_FINGER": "pierścień", "INVTYPE_TRINKET": "błyskotka, talizman",
              "INVTYPE_CLOAK": "płaszcz, peleryna", "INVTYPE_SHIELD": "tarcza",
              "INVTYPE_HOLDABLE": "przedmiot do drugiej ręki", "INVTYPE_BAG": "torba", "INVTYPE_TABARD": "tabard",
              "INVTYPE_BODY": "koszula"}
ARMOR_WORDS = {1: "materiał", 2: "skóra", 3: "kolczuga", 4: "płyta"}
CLASS_WORDS = {0: "jedzenie, picie, mikstura, eliksir", 1: "torba", 5: "odczynnik", 7: "materiał do rzemiosła",
               9: "przepis", 12: "przedmiot do questa", 15: "różności"}
QUALITY_WORDS = {0: "szary śmieć do sprzedania", 1: "zwykły, biały", 2: "zielony, niezwykły", 3: "niebieski, rzadki",
                 4: "fioletowy, epicki", 5: "pomarańczowy, legendarny"}


def item_words(class_id: int, subclass_id: int, equip_loc: str = "", quality: Optional[int] = None) -> str:
    """Polish words for an item's kind ("kostur, kij, laska"), for the vector index and the LLM."""
    bits = []
    if class_id == 2:
        bits.append(WEAPON_WORDS.get(subclass_id, "broń"))
    elif class_id == 4:
        if equip_loc in SLOT_WORDS:
            bits.append(SLOT_WORDS[equip_loc])
        if subclass_id in ARMOR_WORDS and equip_loc not in ("INVTYPE_CLOAK", "INVTYPE_FINGER", "INVTYPE_NECK",
                                                             "INVTYPE_TRINKET"):
            bits.append(ARMOR_WORDS[subclass_id])
        if subclass_id == 6 and "tarcza" not in bits:
            bits.append("tarcza")
    elif class_id in CLASS_WORDS:
        bits.append(CLASS_WORDS[class_id])
    if quality is not None and quality in QUALITY_WORDS:
        bits.append(QUALITY_WORDS[quality])
    return "; ".join(b for b in bits if b)


@dataclass
class Entry:
    name: str  # as the game writes it: "Rhapsody Malt"
    kind: str  # quest | npc | mob | item | place | player | word
    weight: float  # how much it matters right now (0..1)
    source: str  # questlog, objective, quest_npc, zone, nearby, party, whisper, chat, dialog, loot, bag, user...
    info: str = ""  # a few Polish words about it ("przedmiot do questa Beer Basted Boar Ribs")
    ref: int = 0  # quest / npc / item id when known
    data: object = None  # LootItem with stats, BagItem, QuestEntry...
    seen: float = 0.0
    also: set = field(default_factory=set)  # every source it came from ("Crag Boar Rib": objective and bag)

    def has_source(self, sources) -> bool:
        return self.source in sources or bool(self.also & set(sources))

    def body(self) -> str:
        return KIND_PL.get(self.kind, self.kind) + (f": {self.info}" if self.info else "")

    def doc(self) -> str:
        """The text the local vector index embeds."""
        return f"{self.name}. {self.body()}"


@dataclass
class Fix:
    heard: str  # what Whisper wrote ("Targasa")
    name: str  # what it was ("Thargas")
    kind: str
    score: float
    start: int = 0
    end: int = 0


@dataclass
class Correction:
    text: str  # with the names fixed
    fixes: list[Fix] = field(default_factory=list)  # replaced spans
    found: list[Entry] = field(default_factory=list)  # every entry named in the text (also already spelled right)

    @property
    def changed(self) -> bool:
        return bool(self.fixes)


# --------------------------------------------------------------------------- the lexicon

class Lexicon:
    """Thread-safe; the entries are rebuilt at most every ``ttl`` seconds from the getters."""

    def __init__(self, cfg, *, chat_log=None, state_getter: Callable[[], object] = lambda: None,
                 quests_getter: Callable[[], object] = lambda: None, world_getter: Callable[[], object] = lambda: None,
                 bags_getter: Callable[[], object] = lambda: None,
                 picked_getter: Callable[[], list] = lambda: [],
                 embedder_getter: Callable[[], object] = lambda: None, user_file: Optional[Path] = None,
                 ttl: float = 2.0, remote=None):
        self.cfg = cfg
        self.chat_log = chat_log
        self.state_getter = state_getter
        self.quests_getter = quests_getter
        self.world_getter = world_getter
        self.bags_getter = bags_getter
        self.picked_getter = picked_getter
        self.embedder_getter = embedder_getter  # the local e5 of the guides (wvt/kb/embedder.py)
        self.remote = remote  # Google's vectors (wvt.gemini.GeminiEmbedder), first when it is there and not resting
        self.last_backend = ""  # which model the last search used: decides whether its scores can be trusted
        self.user_file = Path(user_file) if user_file else None
        self.ttl = ttl
        self._lock = threading.Lock()
        self._entries: list[Entry] = []
        self._built_at = -1e9
        self._keys: list[list[tuple[str, int]]] = []  # per entry: (sound key, words) variants
        self._index: dict[str, set[int]] = {}
        self._vec_cache: dict[str, np.ndarray] = {}
        self._user_terms: list[tuple[str, str]] = []
        self._user_mtime = None
        self._by_map = None
        self._by_map_world = None

    # ------------------------------------------------------------------ config
    def _c(self, key: str, default):
        try:
            v = self.cfg.get(f"lexicon.{key}", default)
        except Exception:
            v = default
        return default if v is None else v

    @property
    def enabled(self) -> bool:
        return bool(self._c("enabled", True))

    # ------------------------------------------------------------------ building
    def invalidate(self) -> None:
        with self._lock:
            self._built_at = -1e9

    def _current(self) -> tuple[list[Entry], list, dict]:
        """The entries and their sound index, from one build (a rebuild makes new objects, never edits them)."""
        with self._lock:
            if time.monotonic() - self._built_at > self.ttl:
                try:
                    self._entries = self._build()
                except Exception:
                    log.exception("lexicon: building the names of the moment failed")
                    self._entries = []
                self._index_entries()
                self._built_at = time.monotonic()
            return self._entries, self._keys, self._index

    def entries(self) -> list[Entry]:
        return list(self._current()[0])

    def _index_entries(self) -> None:
        self._keys, self._index = [], defaultdict(set)
        for i, e in enumerate(self._entries):
            variants: list[tuple[str, int]] = []
            words = _name_words(e.name)
            forms = [words] + ([words[1:]] if len(words) > 1 and words[0].lower() == "the" else [])
            for ws in forms:
                for english in (False, True):
                    v = ("".join(sound_key(w, english) for w in ws), len(ws))
                    if v[0] and v not in variants:
                        variants.append(v)
            self._keys.append(variants)
            for key, _ in variants:
                for g in _grams(key):
                    self._index[g].add(i)

    def _user(self) -> list[tuple[str, str]]:
        p = self.user_file
        if p is None or not p.exists():
            return []
        try:
            mtime = p.stat().st_mtime
            if mtime != self._user_mtime:
                terms = []
                for line in p.read_text(encoding="utf-8-sig").splitlines():
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    name, _, info = line.partition("=")
                    if name.strip():
                        terms.append((name.strip(), info.strip()))
                self._user_terms, self._user_mtime = terms, mtime
        except OSError as exc:
            log.info("lexicon: %s not read: %s", p, exc)
        return self._user_terms

    def _build(self) -> list[Entry]:
        now = time.time()
        out: dict[str, Entry] = {}

        def add(name: str, kind: str, weight: float, source: str, info: str = "", ref: int = 0, data=None,
                seen: float = 0.0) -> None:
            name = " ".join((name or "").split()).strip(" .,:;!?\"'[]()")
            if len(name) < 3 or weight <= 0.05:
                return
            k = fold(name)
            if not k:
                return
            old = out.get(k)
            if old is None:
                out[k] = Entry(name, kind, weight, source, info, ref, data, seen or now, {source})
                return
            if weight > old.weight:  # the stronger source wins, its facts are added to the other's
                info = "; ".join(x for x in (info, old.info) if x)
                out[k] = Entry(name, kind if old.kind in ("word", "player") or kind != "word" else old.kind, weight,
                               source, info[:300], ref or old.ref, data if data is not None else old.data,
                               max(seen or now, old.seen), old.also | {source})
            else:
                old.also.add(source)
                if info and info not in old.info:
                    old.info = "; ".join(x for x in (old.info, info) if x)[:300]
                if data is not None and old.data is None:
                    old.data = data

        for name, info in self._user():
            add(name, "word", 1.0, "user", info)
        for word in GAME_WORDS:  # "Cuesta" is "questa": the words gamers use, spelled the gamers' way
            add(word, "word", 0.3, "game")
        world = self.world_getter()
        st = self.state_getter()
        book = self.quests_getter()
        player_faction = getattr(getattr(book, "player", None), "faction_code", "") if book is not None else ""

        # where you are
        if st is not None:
            if getattr(st, "zone", ""):
                add(st.zone, "place", 0.9, "zone", "strefa, w której jesteś")
            if getattr(st, "subzone", "") and st.subzone != st.zone:
                add(st.subzone, "place", 0.9, "zone", "miejsce, w którym jesteś")
            for m in getattr(st, "members", []) or []:
                add(m.split("-")[0], "player", 0.95, "party", "gracz z twojej grupy")

        # quests in your log, with their NPCs, items and creatures
        if book is not None and getattr(book, "known", False):
            for q in book.quests():
                rec = world.quests.get(q.quest_id) if world is not None else None
                objs = ", ".join(t for _, t in q.objectives)
                state = "gotowy do oddania" if q.complete else (f"cele: {objs}" if objs else "")
                story = " ".join((rec or {}).get("txt") or [])[:240]
                add(q.title, "quest", 1.0, "questlog", "quest w twoim dzienniku" + (f", {state}" if state else "")
                    + (f"; {story}" if story else ""), q.quest_id, q)
                for _, t in q.objectives:
                    m = re.match(r"^\s*\d+\s*/\s*\d+\s+(.+)$", t) or re.match(r"^\s*(.+?):\s*\d+\s*/\s*\d+\s*$", t)
                    if m:
                        add(m.group(1), "item", 0.85, "objective", f"cel questa {q.title}")
                if rec:
                    self._quest_names(add, world, rec, q.title)

        # people and NPCs from chat, NPC speech, loot lines
        if self.chat_log is not None:
            self._from_chat(add, now, world)

        # what you looted with its stats (wearables), and your bags
        for when, it in list(self.picked_getter() or [])[-20:]:
            age = now - when
            w = 0.9 * 0.5 ** (age / 1800)
            words = item_words(it.class_id, it.subclass_id, it.equip_loc, it.quality)
            add(it.name, "item", max(w, 0.3), "loot", f"twój łup sprzed {_ago(age)}" + (f"; {words}" if words else ""),
                data=it, seen=when)
        bags = self.bags_getter()
        for b in (getattr(bags, "items", None) or []) if bags is not None else []:
            words = item_words(b.class_id, b.subclass_id, b.equip_loc, b.quality)
            add(b.name, "item", 0.4 if b.quality == 0 else 0.7, "bag",
                f"masz w torbach {b.count} szt." + (f"; {words}" if words else ""), b.item_id, b)

        # NPCs and creatures around you
        pos = getattr(book, "pos", None) if book is not None else None
        if world is not None and pos is not None and getattr(pos, "width", 0):
            self._nearby(add, world, pos, player_faction)

        return sorted(out.values(), key=lambda e: -e.weight)

    def _quest_names(self, add, world, rec: dict, title: str) -> None:
        for nid in list(dict.fromkeys((rec.get("sn") or []) + (rec.get("en") or [])))[:4]:
            n = world.npcs.get(nid)
            if n:
                role = "daje" if nid in (rec.get("sn") or []) else "przyjmuje"
                sub = f", {n['sub']}" if n.get("sub") else ""
                add(n["n"], "npc", 0.8, "quest_npc", f"{role} quest {title}{sub}", nid)
        for row in rec.get("loot") or []:
            it = world.items.get(row[0]) if row else None
            if it:
                add(it["n"], "item", 0.85, "objective", f"przedmiot do questa {title}", row[0])
        for row in rec.get("kill") or []:
            n = world.npcs.get(row[0]) if row else None
            if n:
                add(n["n"], "mob", 0.85, "objective", f"do zabicia w queście {title}", row[0])

    _LINK_RE = re.compile(r"\[([^\]]{3,60})\]")

    def _from_chat(self, add, now: float, world) -> None:
        log_ = self.chat_log
        snapshot = getattr(log_, "_snapshot", None)
        if snapshot is None:
            return
        weights = {"WHISPER": (0.85, "gracz, który do ciebie szeptał"), "WHISPER_INFORM": (0.8, "gracz, do którego pisałeś"),
                   "PARTY": (0.8, "gracz z party"), "PARTY_LEADER": (0.8, "lider twojej grupy"),
                   "RAID": (0.7, "gracz z raidu"), "RAID_LEADER": (0.7, "lider raidu"),
                   "GUILD": (0.7, "gracz z twojej gildii"), "OFFICER": (0.7, "oficer gildii"),
                   "SAY": (0.65, "gracz obok ciebie"), "YELL": (0.55, "gracz, który krzyczał"),
                   "CHANNEL": (0.35, "gracz z kanału"), "INSTANCE_CHAT": (0.75, "gracz z instancji"),
                   "MONSTER_SAY": (0.75, "NPC, który do ciebie mówił"), "MONSTER_YELL": (0.6, "NPC, który krzyczał"),
                   "MONSTER_WHISPER": (0.8, "NPC, który do ciebie szepnął")}
        try:
            lines = snapshot(("CHANNEL", "LOOT", "MONSTER_SAY"))
        except TypeError:
            lines = snapshot()
        for ln in lines[-200:]:
            age = now - ln.when
            if age > 1800:
                continue
            decay = 0.5 ** (age / 600)
            if ln.kind == "LOOT":
                for name in self._LINK_RE.findall(ln.text):
                    add(name, "item", 0.85 * 0.5 ** (age / 1800), "loot", f"wpadło ci {_ago(age)} temu",
                        seen=ln.when)
                continue
            base, info = weights.get(ln.kind, (0.0, ""))
            if not base:
                continue
            if ln.sender and not ln.is_self:
                kind = "npc" if ln.kind.startswith("MONSTER") else "player"
                where = f" ({ln.channel})" if ln.channel else ""
                add(ln.sender.split("-")[0], kind, base * decay, "dialog" if kind == "npc" else "chat",
                    info + where, seen=ln.when)
            for name in self._LINK_RE.findall(ln.text):
                add(name, "item", 0.5 * decay, "chat", f"link na czacie od {ln.sender.split('-')[0]}", seen=ln.when)
            if world is not None and ln.kind.startswith("MONSTER"):
                for name in _known_names(world, ln.text):
                    key = fold(name)
                    kind = "npc" if key in getattr(world, "_npc_names", {}) else \
                        "item" if key in getattr(world, "_item_names", {}) else "place"
                    add(name, kind, 0.55 * decay, "dialog", f"wspomniane przez {ln.sender.split('-')[0]}",
                        seen=ln.when)

    def _nearby(self, add, world, pos, faction: str) -> None:
        if self._by_map is None or self._by_map_world is not world:
            by_map: dict[int, list] = defaultdict(list)
            for nid, n in world.npcs.items():
                for area_s, pts in (n.get("sp") or {}).items():
                    ui = world.map_of(int(area_s))
                    if ui:
                        for x, y in pts[:40]:
                            by_map[ui].append((nid, x, y))
            self._by_map = {m: (np.array([r[0] for r in rows]), np.array([r[1] for r in rows], dtype=np.float32),
                                np.array([r[2] for r in rows], dtype=np.float32)) for m, rows in by_map.items()}
            self._by_map_world = world
        rows = self._by_map.get(getattr(pos, "map_id", 0))
        if rows is None:
            return
        ids, xs, ys = rows
        d = np.hypot((xs / 100 - pos.x) * pos.width, (ys / 100 - pos.y) * pos.height)
        radius = float(self._c("nearby_yards", 300))
        order = np.argsort(d)
        seen, taken = set(), 0
        for i in order:
            if d[i] > radius or taken >= int(self._c("nearby_max", 30)):
                break
            nid = int(ids[i])
            if nid in seen:
                continue
            seen.add(nid)
            n = world.npcs.get(nid)
            if not n:
                continue
            taken += 1
            friendly = bool(faction) and faction in (n.get("f") or "")
            closeness = 1.0 - float(d[i]) / (radius * 1.5)
            sub = f", {n['sub']}" if n.get("sub") else ""
            if friendly or n.get("sub"):
                add(n["n"], "npc", 0.6 * closeness, "nearby", f"NPC w pobliżu, {int(d[i])} jardów{sub}", nid)
            else:
                add(n["n"], "mob", 0.45 * closeness, "nearby", f"potwór w pobliżu, {int(d[i])} jardów", nid)

    # ------------------------------------------------------------------ 1. Whisper's prompt
    def whisper_prompt(self, max_terms: Optional[int] = None) -> str:
        """The names of the moment for Whisper, the most important last; "" when off or empty."""
        if not self.enabled or not self._c("whisper_prompt", True):
            return ""
        n = int(max_terms or self._c("prompt_terms", 60))
        top = [e for e in self.entries() if e.kind != "word" or e.source == "user"][:n]
        if not top:
            return ""
        names = [e.name for e in reversed(top)]
        lead = str(self._c("prompt_lead", "Rozmowa w World of Warcraft.") or "")
        tail = str(self._c("prompt_words", "") or "")
        return " ".join(x for x in (lead, ", ".join(names) + ".", tail) if x)

    # ------------------------------------------------------------------ 2. correction
    def correct(self, text: str, strict: bool = False) -> Correction:
        """Names in ``text`` that sound like an entry, replaced by the game's spelling.  ``strict`` (chat lines that
        go out translated) asks for a closer match than Iskra's questions."""
        if not text or not self.enabled or not self._c("correct", True):
            return Correction(text or "")
        entries, keys, index = self._current()
        if not entries:
            return Correction(text)
        words = [m for m in re.finditer(r"[^\W\d_]+(?:['’-][^\W\d_]+)*", text)]
        # a capital letter means a name only inside a sentence ("Gdzie..." starts every question)
        initial = {i for i, m in enumerate(words) if not text[:m.start()].strip()
                   or re.search(r"[.!?]\s*$", text[:m.start()])}
        cands = []
        for i in range(len(words)):
            for j in range(i, min(len(words), i + 5)):
                if j > i and re.search(r"[.!?,;:()\"]", text[words[j - 1].end():words[j].start()]):
                    break  # a name never runs across punctuation
                span = words[i:j + 1]
                folded = [fold(m.group(0)) for m in span]
                if all(f in COMMON for f in folded):
                    continue
                capital = any(m.group(0)[:1].isupper() and k not in initial for k, m in enumerate(span, i))
                raw = [m.group(0) for m in span]
                plain = "".join(sound_key(w) for w in raw)
                variants = {(plain, False), ("".join(sound_key(strip_ending(w)) for w in raw), True)}
                variants = {v for v in variants if v[0]}
                if not variants:
                    continue
                pool: Counter = Counter()
                for v, _ in variants:
                    for g in _grams(v):
                        for idx in index.get(g, ()):
                            pool[idx] += 1
                for idx, hits in pool.items():
                    game = entries[idx].source == "game"
                    for key, nwords in keys[idx]:
                        if abs(nwords - len(span)) > 1:
                            continue
                        best = 0.0
                        for v, stripped in variants:
                            if (stripped and game) or abs(len(v) - len(key)) > max(3, 0.4 * len(key)):
                                continue  # gamer words have their own endings: "questa" is not "quest" + a
                            best = max(best, similarity(v, key))
                        need = self._threshold(len(key), capital, strict)
                        if best >= need:
                            cands.append((best, j - i, entries[idx].weight, i, j, idx))
        cands.sort(key=lambda c: (-c[0], -c[1], -c[2]))
        taken: list[tuple[int, int, int, float]] = []
        for score, _, _, i, j, idx in cands:
            if any(not (j < a or i > b) for a, b, _, _ in taken):
                continue
            taken.append((i, j, idx, score))
        taken.sort()
        out, pos, fixes, found = [], 0, [], []
        for i, j, idx, score in taken:
            e = entries[idx]
            start, end = words[i].start(), words[j].end()
            heard = text[start:end]
            found.append(e)
            if fold(heard) == fold(e.name):
                continue
            out.append(text[pos:start])
            out.append(e.name)
            pos = end
            fixes.append(Fix(heard, e.name, e.kind, round(score, 3), start, end))
        out.append(text[pos:])
        return Correction("".join(out), fixes, list({id(e): e for e in found}.values()))

    @staticmethod
    def _threshold(key_len: int, capital: bool, strict: bool) -> float:
        if key_len <= 4:
            need = 1.0
        elif key_len <= 7:
            need = 0.86
        elif key_len <= 11:
            need = 0.8
        else:
            need = 0.76
        if not capital:  # Whisper capitalises names; a lower-case span must be very close
            need = max(need, 0.92)
        if strict:
            need = min(1.0, need + 0.04)
        return need

    # ------------------------------------------------------------------ 3. the vector index
    def search(self, query: str, k: int = 5, kinds: Optional[Iterable[str]] = None,
               sources: Optional[Iterable[str]] = None, min_score: float = 0.0) -> list[tuple[Entry, float]]:
        """Entries that fit a (Polish) question best, with their cosine; ordered with a little extra for how much
        they matter now.  Google's vectors first (``remote``), the local e5 when Google is missing, resting or
        fails; :meth:`trusted` says whether a score of the model that answered means anything."""
        self.last_backend = ""
        if not self.enabled or not self._c("vector", True) or not query.strip():
            return []
        pool = [e for e in self.entries() if (kinds is None or e.kind in kinds) and (sources is None or e.has_source(sources))]
        if not pool:
            return []
        backends = []
        if self.remote is not None and self.remote.available():
            backends.append(self.remote)
        local = self.embedder_getter()
        if local is not None:
            backends.append(local)
        for emb in backends:
            try:
                scored = self._search_with(emb, query, pool)
            except Exception as exc:
                log.info("lexicon: %s vectors failed (%s), next one", getattr(emb, "model_name", "local"), exc)
                continue
            self.last_backend = "remote" if emb is self.remote else "local"
            return [s for s in scored[:k] if s[1] >= min_score]
        return []

    def _search_with(self, emb, query: str, pool: list[Entry]) -> list[tuple[Entry, float]]:
        fmt = getattr(emb, "doc_text", None)
        docs = [fmt(e.name, e.body()) if fmt else e.doc() for e in pool]
        model = getattr(emb, "model_name", "e5")
        missing = [d for d in dict.fromkeys(docs) if (model, d) not in self._vec_cache]
        if missing and hasattr(emb, "embed_with_query"):
            vecs, q = emb.embed_with_query(missing, query)  # one round trip for the question and new entries
        else:
            vecs = emb.embed(missing) if missing else []
            q = emb.query(query)
        for d, v in zip(missing, vecs):
            self._vec_cache[(model, d)] = v
        if len(self._vec_cache) > 4000:  # entries come and go: keep the cache small
            keep = {(model, d) for d in docs}
            self._vec_cache = {key: v for key, v in self._vec_cache.items() if key in keep}
        cos = np.stack([self._vec_cache[(model, d)] for d in docs]) @ q
        scored = sorted(zip(pool, (float(c) for c in cos)), key=lambda ec: -(ec[1] + 0.02 * ec[0].weight))
        return list(scored)

    def trusted(self, score: float, what: str = "item") -> bool:
        """Whether a score of the last search is good enough to act on: only Google's vectors separate questions
        from small talk (``lexicon.item_min`` 0.65, ``lexicon.quest_min`` 0.69); the local e5 never is."""
        if self.last_backend != "remote":
            return False
        return score >= float(self._c("quest_min" if what == "quest" else "item_min", 0.69 if what == "quest" else 0.65))

    # ------------------------------------------------------------------ for the LLMs
    def translation_names(self, corr: Optional[Correction] = None, n: Optional[int] = None) -> list[str]:
        """Names for the translator's prompt: those named in the line first, then the most important now."""
        n = int(n or self._c("translate_names", 20))
        if n <= 0 or not self.enabled:
            return []
        names = [e.name for e in (corr.found if corr else [])]
        names += [e.name for e in self.entries() if e.kind in ("player", "place", "npc", "item", "quest", "word")
                  and e.weight >= 0.5]
        return list(dict.fromkeys(names))[:n]

    def facts(self, query: str = "", limit: int = 900) -> str:
        """A short Polish picture of the game right now for Iskra's LLM: your quests, what is around you, your
        loot and the entries that fit the question best."""
        if not self.enabled:
            return ""
        ents = self.entries()
        parts = []
        quests = [e for e in ents if e.source == "questlog"]
        if quests:
            def quest(e: Entry) -> str:
                q = e.data
                if getattr(q, "complete", False):
                    return f"{e.name} (do oddania)"
                objs = ", ".join(t for done, t in getattr(q, "objectives", []) or [] if not done)
                return f"{e.name} ({objs})" if objs else e.name
            parts.append("Questy w dzienniku: " + "; ".join(quest(e) for e in quests[:8]))
        near = [e for e in ents if e.source == "nearby" and e.kind == "npc"][:6]
        if near:
            parts.append("NPC w pobliżu: " + ", ".join(e.name for e in near))
        loot = [e for e in ents if e.source == "loot"][:6]
        if loot:
            parts.append("Ostatnie łupy: " + ", ".join(e.name for e in loot))
        bags = self.bags_getter()
        carried = (getattr(bags, "items", None) or []) if bags is not None else []
        if carried:
            good = sorted((b for b in carried if b.quality > 0), key=lambda b: (-b.quality, -b.count))[:10]
            junk = sum(1 for b in carried if b.quality == 0)
            parts.append("W torbach: " + ", ".join(f"{b.count} {b.name}" if b.count > 1 else b.name for b in good)
                         + (f" i {junk} szarych śmieci" if junk else ""))
        people = [e for e in ents if e.kind == "player"][:6]
        if people:
            parts.append("Ludzie: " + ", ".join(e.name for e in people))
        if query:  # only Google's vectors are good enough to say what the question is about
            hits = [(e, s) for e, s in self.search(query, k=4) if self.trusted(s, "quest" if e.kind == "quest" else "item")]
            if hits:
                parts.append("Pytanie pasuje do: " + "; ".join(f"{e.name} ({e.info})" if e.info else e.name
                                                               for e, _ in hits[:3]))
        text = ". ".join(parts)
        return text[:limit].rsplit(" ", 1)[0] + "…" if len(text) > limit else text


def _name_words(name: str) -> list[str]:
    return re.findall(r"[^\W\d_]+(?:['’-][^\W\d_]+)*", name or "")


def _ago(seconds: float) -> str:
    m = int(seconds // 60)
    if m < 1:
        return "chwili"
    if m < 60:
        return f"{m} min"
    return f"{m // 60} godz."


_CAP_RE = re.compile(r"\b([A-Z][a-z'’]+(?:\s+(?:of|the|de)?\s*[A-Z][a-z'’]+){0,3})")


def _known_names(world, text: str) -> list[str]:
    """Capitalised phrases in an English line that are names in the world data (NPCs, places, items)."""
    out = []
    npc = getattr(world, "_npc_names", {})
    items = getattr(world, "_item_names", {})
    areas = {fold(n) for n in getattr(world, "names", {}).values()}
    for m in _CAP_RE.finditer(text or ""):
        words = m.group(1).split()
        for n in range(len(words), 0, -1):
            for i in range(len(words) - n + 1):
                cand = " ".join(words[i:i + n])
                k = fold(cand)
                if len(k) >= 4 and (k in npc or k in items or k in areas):
                    out.append(cand)
    return list(dict.fromkeys(out))
