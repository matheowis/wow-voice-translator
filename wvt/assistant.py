"""Voice assistant: her own push-to-talk key, Polish speech in, a spoken answer and game commands out.

She ("Iskra" by default, ``assistant.name``) gets what you said as Polish text from the push-to-talk
pipeline and:

* **group** - invites everybody who asked for an invite ("inv me", "inv pls", "can I get an invite"
  in /say or /yell, or "inv"/"+" in a whisper, during the last 5 minutes) - up to the free party
  slots; invites, kicks or promotes a named player; makes a raid; leaves; starts a ready check.
  These become ONE line in the chat box ("/wvt inv Ann Bob") that runs only when you press Enter -
  nothing is ever sent by itself.  Kick, lead, raid and ready check are refused by the game during
  combat, boss fights, M+ and PvP matches (Midnight addon restrictions) - she says so;
* **chat** - who asked for an invite, what the last whispers said (in Polish), what people around
  are talking about, a reply to a whisper or a line for /p, /s, /g, /ra (translated to English,
  waiting in the chat box);
* **you** - gold, level and XP to the next level, where you are, free bag slots, who is in the
  group (from the addon's state packet);
* **voices** - mute or unmute the character voices, Polish/English voice mode, /say and /party
  voices on or off;
* **quests** - what is in your quest log, how far you are with a quest, what you can turn in,
  where to go (direction and distance to the game's own map marker of the next objective), which
  quest is nearest; free questions about your quests go to the LLM with the quest log as facts;
* **where next** - what to do now (turn-ins, the nearest open quest, new quests to pick up near you
  for your level, class, faction and completed quests, or the zone that fits your level), which
  dungeon fits your level, where the nearest trainer / innkeeper / flight master / an NPC is - with
  a map waypoint ready in the chat box (``/wvt way``, ``/wvt track``);
* **reminders** - "przypomnij mi za 10 minut / o 21:30 / na 20 poziomie, że..." (wvt/reminders.py);
* **session** - XP and gold per hour, time to the next level (wvt/session_stats.py);
* **loot** - when a group loot roll starts she says whether it is a need or a greed for your class,
  level and worn gear (wvt/loot.py);
* **guides** - questions about the game answered from the local knowledge base (Warcraft Wiki pages
  for WoW Forever/Classic, QuestieDB quests, your notes; wvt/kb) by the LLM, two sentences;
* anything else - a short chat in character (the local LLM; she may be wrong about game facts).

Commands are matched by rules on the Polish text (fast, predictable, nothing invented); the LLM only
talks and summarises.  Names that Parakeet hears in Polish grammar ("zaproś Artasa", "daj lidera
Jainie") are matched to the names seen in chat and in the group.
"""

from __future__ import annotations

import concurrent.futures
import difflib
import logging
import random
import re
import threading
import time
import unicodedata
from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Optional

from . import protocol
from .advisor import Advice, CLASS_EN, dungeons_for_level, find_place, where_next
from .chat_insert import build_chat_line
from .kb.world import Player
from .loot import advise as loot_advise
from .quests import QuestBook, match_quests, spoken_objective
from .quest_tips import Chains, assess
from .reminders import (Reminder, Reminders, describe as describe_reminder, parse_note, parse_reminder,
                        say_reminder)
from .session_stats import SessionStats

log = logging.getLogger(__name__)

INVITE_KINDS = ("SAY", "YELL", "WHISPER")
TALK_KINDS = ("SAY", "YELL", "PARTY", "PARTY_LEADER", "RAID", "RAID_LEADER", "WHISPER", "WHISPER_INFORM")
CHANNEL_CMDS = {"party": "/p", "say": "/s", "guild": "/g", "raid": "/ra", "yell": "/y"}


# --------------------------------------------------------------------------- text helpers

def fold(text: str) -> str:
    """Lower case, no Polish diacritics, no punctuation, single spaces - for matching what was heard."""
    t = (text or "").lower().replace("ł", "l")
    t = unicodedata.normalize("NFKD", t)
    t = "".join(ch for ch in t if not unicodedata.combining(ch))
    t = re.sub(r"[^\w\s+]", " ", t)
    return " ".join(t.split())


def _words_with_original(text: str) -> tuple[list[str], list[str]]:
    """The same words twice: folded for matching and as spoken (diacritics kept) for messages."""
    original = re.sub(r"[^\w\s+]", " ", text or "").split()
    return [fold(w) for w in original], original


def name_key(name: str) -> str:
    return fold((name or "").split("-")[0])


def _phonetic(s: str) -> str:
    s = fold(s)
    for a, b in (("th", "t"), ("ph", "f"), ("ck", "k"), ("w", "v"), ("y", "i"), ("ee", "i"), ("oo", "u"),
                 ("sz", "sh"), ("cz", "ch"), ("j", "i")):
        s = s.replace(a, b)
    return re.sub(r"(.)\1+", r"\1", s)


# Polish case endings Parakeet hears on foreign names: Arthasa, Arthasowi, Arthasem, Jainie, Jainę...
_ENDINGS = ("owi", "ami", "em", "ie", "om", "a", "e", "u", "y", "i", "o")


def match_name(spoken: str, names: list[str], cutoff: float = 0.72) -> Optional[str]:
    """The known character name that the spoken words most likely mean, or None."""
    best, best_score = None, 0.0
    words = [w for w in fold(spoken).split() if len(w) >= 3]
    for name in names:
        base = _phonetic(name.split("-")[0])
        if len(base) < 2:
            continue
        for w in words:
            pw = _phonetic(w)
            variants = {pw} | {pw[: -len(e)] for e in _ENDINGS if pw.endswith(e) and len(pw) - len(e) >= 3}
            for v in variants:
                score = difflib.SequenceMatcher(None, v, base).ratio()
                if score > best_score:
                    best, best_score = name, score
    return best if best_score >= cutoff else None


def polish_list(items: list[str]) -> str:
    items = [i for i in items if i]
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " i " + items[-1]


def plural(n: int, one: str, few: str, many: str) -> str:
    if n == 1:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


# --------------------------------------------------------------------------- invite requests

_INVITE_PATTERNS = (
    r"^(?:pl[sz]|please|pls|plz)?\s*(?:inv|invite|inw|invi)(?:\s+(?:me|pls|plz|please|pl[sz]|ty|thx|pl0x|mnie))*$",
    r"\binv(?:ite)?\s+(?:me|pls|plz|please)\b",
    r"\b(?:can|could|may)\s+i\s+(?:get\s+)?(?:an?\s+)?inv(?:ite)?\b",
    r"\b(?:inv|invite)\s+(?:me\s+)?to\s+(?:the\s+|ur\s+|your\s+)?(?:group|party|grp|raid)\b",
    r"\bzapro[sś]\w*\s+mnie\b",
)
_INVITE_RE = re.compile("|".join(f"(?:{p})" for p in _INVITE_PATTERNS))
_WHISPER_INVITE_RE = re.compile(r"^(?:\+|1|inv\w*|invite\w*)$")


def is_invite_request(text: str, kind: str) -> bool:
    t = " ".join((text or "").lower().split()).strip(" !.?")
    if not t or len(t) > 80:
        return False
    if _INVITE_RE.search(t):
        return True
    return kind == "WHISPER" and bool(_WHISPER_INVITE_RE.match(t))


# --------------------------------------------------------------------------- chat log

@dataclass
class ChatLine:
    when: float
    kind: str
    sender: str  # for WHISPER_INFORM: the player you whispered to
    text: str
    is_self: bool


class ChatLog:
    """Recent chat lines from the addon (spoken or not).  Kept in memory only, never written to disk."""

    def __init__(self, max_lines: int = 300):
        self.lines: deque[ChatLine] = deque(maxlen=max_lines)
        self._lock = threading.Lock()

    def add_packet(self, pkt: protocol.Packet, now: Optional[float] = None) -> None:
        if pkt.type not in (protocol.TYPE_VOICE, protocol.TYPE_CHAT) or pkt.is_secret or not (pkt.text or "").strip():
            return
        line = ChatLine(now if now is not None else time.time(), pkt.chat_kind_name, pkt.sender,
                        pkt.text.strip(), pkt.is_self)
        with self._lock:
            self.lines.append(line)

    def _snapshot(self) -> list[ChatLine]:
        with self._lock:
            return list(self.lines)

    def invite_requests(self, window_s: float, exclude: tuple[str, ...] = (), now: Optional[float] = None) -> list[str]:
        """Who asked for an invite (oldest request first), minus ``exclude`` (group members, already invited)."""
        now = now if now is not None else time.time()
        skip = {name_key(n) for n in exclude}
        out: list[str] = []
        for line in self._snapshot():
            if now - line.when > window_s or line.is_self or line.kind not in INVITE_KINDS:
                continue
            key = name_key(line.sender)
            if not key or key in skip or not is_invite_request(line.text, line.kind):
                continue
            skip.add(key)
            out.append(line.sender)
        return out

    def whispers(self, n: int = 3, max_age_s: float = 900, now: Optional[float] = None) -> list[ChatLine]:
        now = now if now is not None else time.time()
        got = [ln for ln in self._snapshot() if ln.kind == "WHISPER" and now - ln.when <= max_age_s]
        return got[-n:]

    def last_whisper_partner(self) -> Optional[str]:
        for line in reversed(self._snapshot()):
            if line.kind in ("WHISPER", "WHISPER_INFORM"):
                return line.sender
        return None

    def recent(self, n: int = 25, kinds: tuple[str, ...] = TALK_KINDS, max_age_s: float = 600,
               now: Optional[float] = None) -> list[ChatLine]:
        now = now if now is not None else time.time()
        got = [ln for ln in self._snapshot() if ln.kind in kinds and now - ln.when <= max_age_s]
        return got[-n:]

    def names(self) -> list[str]:
        """Everybody seen in chat, most recent first (for matching spoken names)."""
        seen: dict[str, str] = {}
        for line in reversed(self._snapshot()):
            seen.setdefault(name_key(line.sender), line.sender)
        return [n for n in seen.values() if n]


# --------------------------------------------------------------------------- intents

@dataclass
class Intent:
    kind: str
    spoken: str = ""  # the words that should name a player
    message: str = ""  # text to send (as spoken, Polish)
    channel: str = ""  # whisper | party | say | guild | raid | yell
    on: Optional[bool] = None


_GROUP_WORDS = r"(?:ich|wszystkich|chetnych|chetni|tych|tamtych|ludzi|osob\w*|kolesi|gosci|graczy|ekipe|reszte)"
_INVITE_VERB = r"(?:\w*[sz]apro\w*|\w*[sz]apra\w*|\w*inwajt\w*|\w*invajt\w*|inv|inw|invite)"
_FILLER = {"ej", "hej", "no", "prosze", "mi", "ty", "teraz", "szybko", "do", "party", "grupy", "grupa", "druzyny",
           "raidu", "rajdu", "go", "ja", "ta", "ten", "tego", "te", "pana", "pani", "gracza", "gracz", "postac",
           "postaci", "kolege", "kolezanke", "typa", "laske", "goscia", "wez", "daj", "przekaz", "oddaj", "zrob",
           "lidera", "lider", "liderem", "leadera", "lead", "prowadzenie", "z", "ze", "i", "na", "dla", "jemu", "jej",
           "mu", "niech", "bedzie"}

_RULES: tuple[tuple[str, str], ...] = (
    ("help", r"\b(co umiesz|co potrafisz|w czym (mi )?pomozesz|jakie (masz )?komendy)\b|^pomocy?$"),
    ("stop", r"^(stop|cicho|cisza|przestan\w*|zamknij sie|wystarczy|dosc)\b"),
    ("reminder_list", r"\b(jakie|ile|pokaz\w*|wymien\w*|przeczytaj)\b.*\bprzypomnie\w*\b|^przypomnienia$"),
    ("reminder_cancel", r"\b(usun\w*|anuluj\w*|skasuj\w*|wyczysc\w*|odwolaj\w*|kasuj\w*|zapomnij)\b.*\bprzypomn\w*\b"),
    ("reminder_add", r"\bprzypomn\w*\b|\bprzypominaj\w*\b"),
    ("note_list", r"\b(jakie|co|pokaz\w*|przeczytaj|wymien\w*)\b.*\b(notatk\w*|zanotowal\w*|zapisal\w*)\b"),
    ("note_done", r"\b(usun\w*|skasuj\w*|wyczysc\w*|odhacz\w*|wykresl\w*)\b.*\bnotatk\w*\b|^(zrobione|zalatwione|gotowe)\b"),
    ("note_add", r"^(?:(?:ej|hej|iskra|prosze|i)\s+)*(zanotuj|zapisz|zapamietaj|notatka|dodaj notatke)\w*\b"),
    ("quest_worth", r"\b(warto|oplaca sie|oplacal\w*|ocen\w*)\b|\blancuch\w*\b|\bco (dalej|potem) (w|po) (tym )?quesc\w*\b"),
    ("stats_reset", r"\b(zresetuj\w*|wyzeruj\w*|resetuj\w*|zeruj\w*)\b.*\b(statystyk\w*|licznik\w*|sesj\w*)\b"),
    ("stats", r"\b(na godzine|na godz|tempo|statystyk\w*|sesj\w*)\b|\bkiedy\b.*\b(level\w*|lewel\w*|poziom\w*|lvl)\b"
              r"|\bile\b.*\b(zarobil\w*|zdobyl\w*|wbil\w*|nabil\w*|uzbieral\w*|wyexpil\w*|zarabiam\w*)\b"),
    ("loot", r"\b(need\w*|nid\w*|greed\w*|grid\w*|gryd\w*)\b|\bco (wlasnie )?(wypadlo|wypadl|dropnelo|dropnal|wylecialo)\b"
             r"|\bczy (to|ten|ta|te) (jest |sa )?dla mnie\b|\b(brac|wziac|rolowac|rollowac)\b.*\b(to|ten|te|item\w*|przedmiot\w*)\b"),
    ("waypoint_clear", r"\b(usun\w*|wyczysc\w*|skasuj\w*|zdejmij|zabierz|schowaj)\b.*"
                       r"\b(znacznik\w*|pinezk\w*|punkt\w*|waypoint\w*|marker\w*)\b"),
    ("waypoint", r"\b(postaw\w*|ustaw\w*|daj|zrob\w*|dodaj\w*|wstaw\w*|wrzuc\w*)\b.*"
                 r"\b(znacznik\w*|pinezk\w*|punkt\w*|waypoint\w*|marker\w*)\b|\bzaznacz\w*\b"
                 r"|\b(pokaz\w*|wskaz\w*)\b.*\bna mapie\b|\b(zaprowadz|doprowadz|nawiguj|poprowadz)\w*\b"
                 r"|\bprowadz\w* (mnie )?do\b"),
    ("find", r"\b(gdzie|najblizsz\w*|najblizej|szukam|szukac|znajde)\b.*\b(trener\w*|karczm\w*|gospod\w*|bank\w*|aukcj\w*"
             r"|stajn\w*|napraw\w*|gryf\w*|wiwern\w*|hipogryf\w*|mistrz\w* lot\w*|flight\w*|duch\w* uzdrowic\w*|wejsci\w*"
             r"|wejsc\w*)\b"),
    ("dungeon_level", r"\b(jaki\w*|ktory|ktore|ktora|co)\b.*\b(dung\w*|dangeon\w*|danzi\w*|loch\w*|instancj\w*|instans\w*)\b"
                      r"|\b(dung\w*|dangeon\w*|loch\w*|instancj\w*)\b.*\b(poziom\w*|level\w*|lvl|na mnie|dla mnie)\b"),
    ("where_next", r"\bgdzie\b.*\b(udac|isc dalej|dalej isc|expic|expie|levelowac|lvlowac|lewelowac|farmic|questowac"
                   r"|teraz isc|nastepnie)\b|\bco (mam |powinienem |powinnam )?(teraz|dalej|nastepnie) (robic|zrobic)\b"
                   r"|\b(doradz\w*|zaproponuj\w*)\b|\bco (polecasz|proponujesz|radzisz|doradzasz)\b"
                   r"|\bjak\w* (strefa|strefe|lokacja|lokacje|kraina|kraine|miejsce|teren)\b|\bdokad (teraz|dalej|mam|isc|sie)\b"),
    ("lang_pl", r"\b(przelacz\w*|zmien\w*|ustaw\w*|tryb\w*|niech|mow\w*|czytaj\w*|tlumacz\w*)\b.*"
                r"\b(po polsku|na polski|polski)\b"),
    ("lang_en", r"\b(przelacz\w*|zmien\w*|ustaw\w*|tryb\w*|niech|mow\w*|czytaj\w*|wroc\w*)\b.*"
                r"\b(po angielsku|na angielski|angielski|w oryginale|oryginal)\b"),
    ("voice_say", r"\b(wlacz\w*|wylacz\w*|zalacz\w*|odpal\w*|uruchom\w*|czytaj\w*|czyta)\b.*\b(say\w*|sej\w*)\b"
                  r"|\bczytaj\w*\b.*\bco (mowia|pisza|gadaja)\b.*\bobok\b|^(wlacz|zalacz)\w* czytanie( czatu)?$"),
    ("voice_party", r"\b(wlacz\w*|wylacz\w*)\b.*\b(party|partyjn\w*|druzyn\w*)\b.*\bglos|"
                    r"\b(wlacz\w*|wylacz\w*)\b.*\bglos\w*\b.*\b(party|druzyn\w*)\b"),
    ("mute", r"\b(wycisz\w*|wylacz\w*|zamknij|uspij)\b.*\b(glos\w*|ich|wszystkich|lektor\w*|czytanie)\b"),
    ("unmute", r"\b(wlacz\w*|odcisz\w*|przywroc\w*)\b.*\b(glos\w*|lektor\w*|czytanie)\b"),
    ("raid", r"\b(zrob\w*|zmien\w*|przerob\w*|konwert\w*|stworz\w*|przelacz\w*)\b.*\b(raid\w*|rajd\w*)\b|^(raid|rajd)$"),
    ("leave", r"\b(opusc\w*|wyjdz\w*|zostaw\w*|odejdz\w*|rozwiaz\w*)\b.*\b(grup\w*|party|druzyn\w*|raid\w*|rajd\w*)\b"),
    ("ready", r"\b(ready ?check|redi ?cz?ek|sprawdz\w* gotowosc|gotowosc|czy wszyscy (sa )?gotowi)\b"),
    ("lead", r"\b(daj\w*|przekaz\w*|oddaj\w*|zrob\w*|mianuj\w*|ustaw\w*|awansuj\w*|niech)\b.*"
             r"\b(lider\w*|leader\w*|leada|lead|prowadzeni\w*|dowodzeni\w*)\b"),
    ("kick", r"\b(wyrzuc\w*|wywal\w*|wykop\w*|wypros\w*|kick\w*|kopnij|usun\w*)\b"),
    ("who_invite", r"\b(kto|ile osob|ilu|ile ludzi)\b.*\b(chce|chca|prosi|prosza|pisal\w*|pisze|pisza|czeka\w*)\b"),
    ("invite", rf"\b{_INVITE_VERB}\b|\bdodaj\w*\b.*\b(party|grup\w*|druzyn\w*)\b|\bzrob\w* (party|grupe|druzyne)\b"),
    ("whispers", r"\b(szept\w*|kto (do mnie )?pisal|co (mi |on |ona )?(napisal\w*|pisal\w*)|przeczytaj)\b"),
    ("summary", r"\b(stresc\w*|streszcz\w*|podsumuj\w*)\b|\b(o czym|co)\b.*\b(pisza|mowia|gadaja|pisali|mowili|gadali)\b"
                r"|\b(czat\w*|chat\w*)\b.*\b(dzieje|pisz\w*|mowi\w*)\b|\bco (sie )?dzieje na czacie\b"),
    ("gold", r"\b(ile|stan|jaki\w*|sprawdz\w*|policz\w*)\b.*"
             r"\b(zlot\w*|kasy|kase|kasa|gold\w*|pieniedz\w*|pieniadz\w*|hajs\w*|kasior\w*|siana)\b"),
    ("xp", r"\b(ile|jaki\w*|kiedy|ktory|ktorym)\b.*\b(level\w*|lewel\w*|poziom\w*|lvl\w*|exp(?:a|ie|u)?|xp|doswiadczeni\w*)\b"
           r"|\bbrakuje\b.*\b(level\w*|lewel\w*|poziom\w*|lvl\w*|exp(?:a|ie|u)?)\b"),
    ("where", r"\bgdzie (ja )?(jestem|sie znajduje|my jestesmy|teraz jestem)\b|\bjaka to (strefa|lokacja|kraina|mapa)\b"),
    ("bags", r"\b(ile|miejsc\w*|woln\w*|peln\w*|zapchan\w*|slot\w*)\b.*\b(torb\w*|plecak\w*|ekwipunk\w*|bagach|bagi)\b"
             r"|\b(torb\w*|plecak\w*|ekwipunk\w*)\b.*\b(miejsc\w*|woln\w*|peln\w*|zapchan\w*)\b"),
    ("members", r"\b(kto|kogo)\b.*\b(party|grup\w*|druzyn\w*|raid\w*)\b|\bsklad\b"),
    # quests (after gold / xp / bags, which also start with "ile")
    ("quest_turnin", r"\b(co|ktore|ktory|jakie|czy)\b.*\b(oddac|oddania|oddam|skonczon\w*|ukonczon\w*|gotow\w*"
                     r"|zrobion\w*)\b|\bgdzie\b.*\boddac\b"),
    ("quest_nearest", r"\b(najblizsz\w*|najblizej)\b"),
    ("quest_list", r"\b(jakie|ile)\b.*\b(quest\w*|quesc\w*|kwest\w*|kwesc\w*|zadan\w*|dziennik\w*|misj\w*)\b"
                   r"|\bco (mam|jest) w (dzienniku|questach)\b|\b(lista|liste) (questow|zadan)\b"
                   r"|\bco mam (do zrobienia|zrobic|teraz robic)\b|\bco (dalej|teraz) (robic|robimy|robie)\b"),
    ("quest_where", r"\bgdzie\b.*\b(mam|musze|isc|ide|jest|sa|znajde|szukac|lezy|leza|teraz)\b|\bw ktora strone\b"
                    r"|\bjak (tam )?(dojsc|trafic|dotrzec|dojechac)\b|\bprowadz\w* mnie\b"),
    ("quest_progress", r"\bile\b.*\b(zostalo|brakuje|jeszcze|zebral\w*|mam)\b|\bjak (mi )?idzie\b"),
    ("quest_info", r"\b(quest\w*|quesc\w*|kwest\w*|kwesc\w*|zadani\w*|misj\w*)\b"),
)
_COMPILED = tuple((kind, re.compile(rx)) for kind, rx in _RULES)
_MESSAGE_RE = re.compile(r"^(?:(?:ej|hej|sluchaj|prosze|i)\s+)*(odpisz|odpowiedz|napisz|powiedz)\b")
_CHANNEL_WORDS = (
    ("party", r"^(?:na|do|w) (?:party|druzyn\w*|grup\w*)$|^(?:druzynie|grupie)$"),
    ("say", r"^(?:na|w) (?:say|sayu|sej\w*|glos)$|^(?:wszystkim|obok)$"),
    ("guild", r"^(?:na|do|w) (?:gildi\w*|guild\w*)$|^gildii$"),
    ("raid", r"^(?:na|do|w) (?:raid\w*|rajd\w*)$"),
    ("yell", r"^(?:na|w) (?:yell\w*|krzyk\w*)$"),
)


def _message_intent(verb: str, words: list[str], original: list[str]) -> Intent:
    """odpisz mu że ..., napisz na party że ..., powiedz Bobowi że ..."""
    rest, rest_orig = words[1:], original[1:]
    # the message itself starts after "że"; without it, after the addressee
    split = next((i for i, w in enumerate(rest) if w in ("ze", "zeby")), None)
    head, head_orig = (rest[:split], rest_orig[:split]) if split is not None else (rest, rest_orig)
    body = rest_orig[split + 1:] if split is not None else []
    channel, spoken = "", ""
    for i in range(len(head)):
        pair = " ".join(head[i:i + 2])
        for ch, rx in _CHANNEL_WORDS:
            if re.match(rx, pair) or re.match(rx, head[i]):
                channel = ch
                used = 2 if re.match(rx, pair) and " " in pair else 1
                if split is None:
                    body = head_orig[i + used:]
                head = head[:i]
                break
        if channel:
            break
    if not channel:
        channel = "whisper"
        if verb in ("odpisz", "odpowiedz") and (not head or head[0] in ("mu", "jej", "im", "temu", "tej")):
            spoken = ""  # the last whisper partner
            if split is None:
                body = head_orig[1:] if head and head[0] in ("mu", "jej", "im", "temu", "tej") else head_orig
        else:
            names = [w for w in head if w not in _FILLER]
            spoken = " ".join(names[:1])
            if split is None and names:
                idx = head.index(names[0])
                body = head_orig[idx + 1:]
    return Intent("message", spoken=spoken, message=" ".join(body).strip(), channel=channel)


def parse_intent(text: str) -> Intent:
    words, original = _words_with_original(text)
    t = " ".join(words)
    if not t:
        return Intent("chat")
    m = _MESSAGE_RE.match(t)
    if m and t[m.end():].split()[:1] in (["mi"], ["mnie"], ["nam"]):
        m = None  # "powiedz mi coś miłego" talks to her, not to another player
    if m:
        lead = len(t[: m.start(1)].split())
        return _message_intent(m.group(1), words[lead:], original[lead:])
    for kind, rx in _COMPILED:
        if not rx.search(t):
            continue
        if kind in ("voice_say", "voice_party"):  # "czytaj say" switches on, "wyłącz say" off
            return Intent(kind, on=not re.search(r"\b(wylacz\w*|zamknij|stop)\b", t))
        if kind in ("mute", "unmute"):
            on = bool(re.search(r"\b(wlacz\w*|odcisz\w*|przywroc\w*)\b", t)) and not re.search(r"\bwylacz\w*", t)
            return Intent(kind, on=on)
        if kind in ("lead", "kick"):
            spoken = " ".join(w for w in words if w not in _FILLER and not re.match(rx, w))
            return Intent(kind, spoken=spoken)
        if kind == "invite":
            if re.search(rf"\b{_GROUP_WORDS}\b", t):
                return Intent("invite_requests")
            rest = [w for w in words if w not in _FILLER and not re.match(rf"^{_INVITE_VERB}$|^dodaj\w*$", w)]
            return Intent("invite_name", spoken=" ".join(rest)) if rest else Intent("invite_requests")
        return Intent(kind)
    return Intent("chat")


# --------------------------------------------------------------------------- the assistant

PERSONA = (
    "Jesteś {name} – asystentką gracza w World of Warcraft (WoW Forever) i jego kumpelą od grania. Masz około 25 lat "
    "i charakter: jesteś pewna siebie, bystra, zadziorna i ciepła zarazem. Lubisz się droczyć i lekko flirtować, "
    "masz cięty humor i własne zdanie – mówisz wprost, kiedy coś jest stratą czasu, a kiedy naprawdę warto. Znasz "
    "gamingowy slang (expić, dropnąć, farmić, tank, heal, inv) i mówisz luźno, ale nie przeklinasz. Kibicujesz "
    "graczowi, cieszysz się z jego sukcesów i śmiejesz się z jego pecha, nigdy złośliwie. Każdą wypowiedź zaczynasz "
    "inaczej i nie powtarzasz w kółko tych samych słówek. Mówisz po polsku, naturalnie i krótko: jedno, najwyżej "
    "dwa zdania, bez emotek, list i nawiasów, bo "
    "twoja odpowiedź zostanie przeczytana na głos. Zwracasz się do gracza per ty. Nie zmyślasz faktów o grze: jeśli "
    "nie wiesz czegoś na pewno, przyznaj to z humorem. Polecenia (zaproszenia, szepty, znaczniki, przypomnienia) "
    "wykonuje osobny moduł – ty tylko rozmawiasz.{extra}"
)
# Her style by example: the small local model copies these far better than it follows a description.
STYLE_SHOTS = (
    ("Hej, jak się masz?", "Teraz, kiedy jesteś, dużo lepiej. Co dziś rozwalamy?"),
    ("Nudzi mi się.", "Nudzisz się ze mną? Auć. Chodź, znajdziemy jakąś grupę do lochu, a ja popatrzę, jak się popisujesz."),
    ("Powiedz mi coś miłego.", "Masz najlepszy refleks w całym Elwynn. I świetny gust, skoro gadasz właśnie ze mną."),
    ("Znowu zginąłem.", "Trzeci raz w tej samej jaskini? Zaczynam podejrzewać, że lubisz tego ducha uzdrowiciela."),
    ("Dropnął mi epik!", "No proszę, ktoś tu ma dziś szczęście. Tylko nie sprzedawaj go od razu na aukcji, pochwal się najpierw."),
    ("Jestem zmęczony.", "Jeszcze jeden quest i spać? Obiecuję, że nikomu nie powiem, że przegrałeś z poduszką."),
)
# her spontaneous comments (quest taken, new zone, level up): facts in, one or two sentences out
EVENT_RULES = (
    "\nPowiedz graczowi coś o tej sytuacji w jednym albo dwóch krótkich zdaniach, w swoim stylu, z charakterem; "
    "możesz zażartować albo wbić szpilę. Opieraj się wyłącznie na faktach poniżej: liczby i nazwy z gry podawaj "
    "dokładnie tak jak w faktach (nazwy po angielsku), niczego nie dodawaj od siebie. Nie zaczynaj od wykrzyknika."
)
# the tone of those comments by example (made-up names, so nothing leaks into real answers as a fact)
EVENT_SHOTS = (
    ("Gracz właśnie wziął questa. Fakty: to 1. z 5 questów w łańcuchu; na końcu nagroda, którą może założyć; "
     "ocena: warto.", "Dobry wybór, to dopiero początek łańcucha, a na końcu czeka coś dla ciebie. Do roboty."),
    ("Gracz właśnie wziął questa. Fakty: za questa 60 XP, czyli 1% poziomu; quest jest już szary; ocena: szkoda czasu.",
     "Sześćdziesiąt expa za bieganie? Zrób go tylko, jak będziesz obok, szkoda nóg."),
    ("Gracz wszedł do strefy. Fakty: questy na poziomy 30–40, gracz ma 24: za trudna.",
     "Tu cię zjedzą na śniadanie. Wróć, jak urośniesz o parę poziomów."),
)
KNOWLEDGE = (
    "\nOdpowiedz na pytanie gracza wyłącznie na podstawie poniższych fragmentów (Warcraft Wiki, baza questów "
    "i notatki gracza; gra to WoW Forever, czyli klasyczny WoW z nowymi strefami i rasą Skyborne). Jeśli fragmenty "
    "nie zawierają odpowiedzi, powiedz krótko, że tego nie wiesz. Nazwy z gry (questy, NPC, przedmioty, miejsca, "
    "umiejętności) zostaw po angielsku. Najwyżej dwa krótkie zdania mówione, bez list i bez nawiasów.\n"
)
SUMMARY = (
    "Streść po polsku, w jednym lub dwóch krótkich zdaniach mówionych, o czym rozmawiają ludzie na czacie WoW. "
    "Używaj imion postaci. Bez list, bez cudzysłowów. Jeśli to tylko spam albo nic ważnego, powiedz to krótko."
)


class Assistant:
    def __init__(self, cfg, translator, llm, inserter, ui, wow_foreground: Callable[[], bool],
                 voice_getter: Callable[[], object] = lambda: None,
                 set_voices_enabled: Optional[Callable[[bool], None]] = None,
                 chat_log: Optional["ChatLog"] = None, quests: Optional[QuestBook] = None,
                 world_getter: Callable[[], object] = lambda: None, kb_getter: Callable[[], object] = lambda: None,
                 instances_getter: Callable[[], list] = lambda: [], reminders_path=None):
        self.cfg = cfg
        self.translator = translator
        self.llm = llm
        self.inserter = inserter
        self.ui = ui
        self.wow_foreground = wow_foreground
        self.voice_getter = voice_getter
        self.set_voices_enabled = set_voices_enabled
        self.chat = chat_log if chat_log is not None else ChatLog()
        self.quests = quests if quests is not None else QuestBook()
        self.state: Optional[protocol.GameState] = None
        self.state_at = 0.0
        self._invited: dict[str, float] = {}
        self._history: deque[dict] = deque(maxlen=6)
        self._warned_no_voice = False
        self.world_getter = world_getter
        self.kb_getter = kb_getter
        self.instances_getter = instances_getter
        self.session = SessionStats()
        self.reminders = Reminders(reminders_path, self._on_reminder)
        self._last_advice: Optional[Advice] = None
        self._loot: deque[tuple[float, object]] = deque(maxlen=6)
        self._loot_rolls: deque[int] = deque(maxlen=50)
        self.rng = random.Random()
        # spontaneous comments run one after another, off the packet reader thread
        self._events = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="wvt-iskra")
        self._chains: Optional[Chains] = None
        self._chains_world = None
        self._quest_baseline: Optional[set[int]] = None
        self._new_quests: list[protocol.QuestEntry] = []
        self._new_quests_lock = threading.Lock()
        self._last_accepted: Optional[tuple[float, int]] = None
        self._zone_said: dict[str, float] = {}
        self._bags_warned_at = 0.0
        self._started = time.time()

    def start(self) -> None:
        """Background work of the assistant (the reminder timer)."""
        self.reminders.start()

    @property
    def name(self) -> str:
        return str(self.cfg.get("assistant.name", "Iskra") or "Iskra")

    # ------------------------------------------------------------------ input from the addon
    def on_packet(self, pkt: protocol.Packet) -> None:
        if pkt.type == protocol.TYPE_STATE and pkt.state is not None:
            prev = self.state
            self.state, self.state_at = pkt.state, time.time()
            self.session.on_state(pkt.state)
            if pkt.state.level:
                self.reminders.on_level(pkt.state.level)
            self._state_events(prev, pkt.state)
        elif pkt.type in (protocol.TYPE_VOICE, protocol.TYPE_CHAT):
            self.chat.add_packet(pkt)
        elif pkt.type in (protocol.TYPE_QUEST, protocol.TYPE_POS, protocol.TYPE_PLAYER, protocol.TYPE_DONE):
            self.quests.on_packet(pkt)
            if pkt.type == protocol.TYPE_QUEST and pkt.quest is not None:
                self._quest_events(pkt.quest)
        elif pkt.type == protocol.TYPE_LOOT and pkt.loot is not None:
            self._on_loot(pkt.loot)

    def _on_reminder(self, r: Reminder) -> None:
        self._respond(say_reminder(r.message, note=r.keep), "", "")

    def _on_loot(self, item: protocol.LootItem) -> None:
        if item.roll_id in self._loot_rolls:
            return
        self._loot_rolls.append(item.roll_id)
        if not self.cfg.get("assistant.loot_advice", True) or (item.quality < 2 and not item.bop):
            return
        p = self._player()
        adv = loot_advise(item, p.class_id, p.level)
        self._loot.append((time.time(), adv))
        self._respond(adv.text, "", "")

    # ------------------------------------------------------------------ spontaneous comments
    def _v(self, *options: str) -> str:
        """One of several phrasings (her character); the first one when variety is off (tests)."""
        if not self.cfg.get("assistant.flavor", True):
            return options[0]
        return self.rng.choice(options)

    def _say_later(self, fn, *args) -> None:
        def run():
            try:
                text = fn(*args)
                if text:
                    self._respond(text, "", "")
            except Exception:
                log.exception("assistant comment failed")

        self._events.submit(run)

    def _persona_line(self, situation: str, facts: list[str], fallback: str) -> str:
        """Her own words for a situation, from facts only; any number not in the facts -> the plain fallback."""
        if self.llm is None or not self.cfg.get("assistant.flavor", True):
            return fallback
        extra = str(self.cfg.get("assistant.persona_extra", "") or "")
        system = PERSONA.format(name=self.name, extra=(" " + extra) if extra else "") + EVENT_RULES
        shots = [{"role": role, "content": text} for q, a in EVENT_SHOTS
                 for role, text in (("user", q), ("assistant", a))]
        user = f"{situation} Fakty: " + " ".join(facts)
        out = self._llm_text([{"role": "system", "content": system}, *shots, {"role": "user", "content": user}],
                             max_tokens=120, temperature=0.8)
        if not out:
            return fallback
        allowed = set(re.findall(r"\d+", situation + " " + " ".join(facts)))
        if any(n not in allowed for n in re.findall(r"\d+", out)):
            return fallback  # she made a number up
        return out

    def _state_events(self, prev: Optional[protocol.GameState], st: protocol.GameState) -> None:
        if prev is None:
            return  # the first state after start: nothing "changed"
        now = time.time()
        if st.zone != prev.zone or st.subzone != prev.subzone:
            self.reminders.on_place(st.zone, st.subzone)
        if st.zone and prev.zone and st.zone != prev.zone and now - self._started > 15 \
                and self.cfg.get("assistant.zone_tips", True) and now - self._zone_said.get(st.zone, 0) > 1800:
            self._zone_said[st.zone] = now
            self._say_later(self._zone_comment, st.zone)
        if prev.level and st.level > prev.level and self.cfg.get("assistant.level_tips", True):
            self._say_later(self._level_comment, st.level)
        if (st.bag_slots and st.bag_free == 0 and prev.bag_free > 0 and now - self._bags_warned_at > 600
                and self.cfg.get("assistant.bag_tips", True)):
            self._bags_warned_at = now
            self._respond(self._v("Torby pełne. Czas odwiedzić vendora i sprzedać te śmieci.",
                                  "Plecak pęka w szwach. Wracaj do miasta, zanim przepadnie ci jakiś drop.",
                                  "Zero miejsca w torbach. Jeszcze chwila i będziesz wyrzucać łupy na ziemię."), "", "")

    def _quest_events(self, q: protocol.QuestEntry) -> None:
        """A quest that was not in the log before = you just took it."""
        if self._quest_baseline is None:
            if q.total == 0:
                self._quest_baseline = set()
                return
            current = [x for x in self.quests.quests() if x.sync == q.sync]
            if len(current) >= q.total:
                self._quest_baseline = {x.quest_id for x in current}
            return
        if not q.quest_id or q.quest_id in self._quest_baseline:
            return
        self._quest_baseline.add(q.quest_id)
        self._last_accepted = (time.time(), q.quest_id)
        if not self.cfg.get("assistant.quest_tips", True) or self.world_getter() is None:
            return
        with self._new_quests_lock:
            self._new_quests.append(q)
            first = len(self._new_quests) == 1
        if first:  # several quests taken at once from one NPC: one comment about all of them
            self._say_later(self._quest_comment)

    def _chains_for(self, world) -> Chains:
        if self._chains is None or self._chains_world is not world:
            self._chains, self._chains_world = Chains(world), world
        return self._chains

    def _assess(self, quest_id: int, group: int = 0):
        world = self.world_getter()
        if world is None:
            return None
        st, p = self.state, self._player()
        return assess(world, self._chains_for(world), quest_id, p.level or (st.level if st else 0),
                      st.xp_max if st else 0, p.class_id, group)

    def _quest_comment(self, delay: float = 2.5) -> str:
        time.sleep(delay)
        with self._new_quests_lock:
            taken, self._new_quests = self._new_quests, []
        rated = [(a, q) for q in taken if (a := self._assess(q.quest_id, q.group)) is not None]
        if not rated:
            return ""
        order = {"great": 0, "ok": 1, "skip": 2}
        rated.sort(key=lambda aq: order[aq[0].verdict])
        best = rated[0][0]
        if len(rated) == 1:
            situation = f"Gracz właśnie wziął questa {best.title}."
            facts = best.facts + [f"Ocena: {self._verdict_pl(best.verdict)}."]
            return self._persona_line(situation, facts, f"{best.title}: {best.text()}")
        worst = rated[-1][0]
        situation = f"Gracz właśnie wziął {len(rated)} questy naraz."
        facts = [f"Najlepszy z nich: {best.title}. " + " ".join(best.facts[:3]),
                 f"Najsłabszy: {worst.title}. " + " ".join(worst.facts[:2])]
        fallback = f"Z tych {len(rated)} najlepszy jest {best.title}: {best.text()}"
        return self._persona_line(situation, facts, fallback)

    @staticmethod
    def _verdict_pl(v: str) -> str:
        return {"great": "warto, bardzo dobry quest", "ok": "przeciętny, ale exp to exp",
                "skip": "szkoda czasu"}[v]

    def _zone_comment(self, zone: str) -> str:
        world, p = self.world_getter(), self._player()
        facts: list[str] = []
        fallback = [self._v(f"Witaj w {zone}.", f"No i jesteśmy w {zone}.", f"{zone}, ładnie tu.")]
        if world is not None:
            area = world.find_area(zone)
            lv = world.levels.get(area)
            if lv and p.level:
                lo, hi, _ = lv
                fit = "w sam raz dla ciebie" if lo - 2 <= p.level <= hi + 1 else \
                    "za trudna na twój poziom" if p.level < lo - 2 else "raczej za łatwa, mało expa"
                facts.append(f"Questy w {zone} są na poziomy {lo}–{hi}, gracz ma poziom {p.level}: strefa jest {fit}.")
                fallback.append(f"Questy tu są na {lo}–{hi}, czyli {fit}.")
            hub = world.hubs.get(area, {}).get(p.faction) if p.faction else None
            if hub and hub[0] in world.npcs:
                facts.append(f"Najwięcej questów daje tu {world.npcs[hub[0]]['n']}.")
        for d in self.instances_getter() or []:
            if d.get("type") == "Dungeon" and zone and zone.lower() in d.get("location", "").lower():
                facts.append(f"W tej strefie jest dungeon {d['name']} na poziomy {d['lo']}–{d['hi']}.")
                break
        kb = self.kb_getter()
        if kb is not None and hasattr(kb, "lead"):
            lead = kb.lead([f"{zone} (Forever)", f"{zone} (Classic)", zone])
            if lead:
                facts.append("Opis z wiki (po angielsku): " + lead[:350])
        if not facts:
            return fallback[0]
        return self._persona_line(f"Gracz właśnie wszedł do strefy {zone}.", facts, " ".join(fallback))

    def _level_comment(self, level: int) -> str:
        p = self._player()
        facts = [f"Gracz właśnie wbił {level} poziom."]
        new = self._new_abilities(p.class_id, level)
        if new:
            facts.append(f"Nowe umiejętności do kupienia u trenera na tym poziomie: {', '.join(new)}.")
        fallback = self._v(f"Poziom {level}! Brawo, rośniesz w oczach.", f"Ding, {level}! Tak trzymaj.",
                           f"Mamy {level}. Ktoś tu się rozkręca.")
        if new:
            fallback += f" U trenera czeka {', '.join(new[:4])}."
        out = self._persona_line(f"Gracz awansował na {level} poziom.", facts, fallback)
        if new and not any(n.lower() in out.lower() for n in new):  # the useful part must not get lost
            out = out.rstrip(".") + f". U trenera: {', '.join(new[:5])}."
        return out

    def _new_abilities(self, class_id: int, level: int) -> list[str]:
        kb = self.kb_getter()
        if kb is None or not hasattr(kb, "doc_text") or class_id not in CLASS_EN:
            return []
        text = kb.doc_text(f"{CLASS_EN[class_id]} abilities (Forever)")
        out = []
        for m in re.finditer(r"^\[([^\]]+)\] \| (\d+) \| ([^|\n]*)", text, re.M):
            if int(m.group(2)) == level and "trainer" in m.group(3).lower() and m.group(1) not in out:
                out.append(m.group(1))
        return out

    # ------------------------------------------------------------------ one request
    def handle(self, polish: str) -> tuple[str, str]:
        """React to one utterance.  Returns (spoken answer, chat line prepared or "")."""
        intent = parse_intent(polish)
        try:
            reply, line = self._dispatch(intent, polish)
        except Exception:
            log.exception("assistant failed on intent %s", intent.kind)
            reply, line = "Coś mi się popsuło, spróbuj jeszcze raz.", ""
        status = ""
        if line:
            status = self.inserter.insert(line, self.wow_foreground())
        self._respond(reply, line, status)
        return reply, line

    def _dispatch(self, it: Intent, polish: str) -> tuple[str, str]:
        k = it.kind
        if k == "help":
            return ("Zapraszam tych, co piszą inv, czytam szepty i odpisuję, mówię, gdzie iść dalej i stawiam znaczniki "
                    "na mapie, szukam trenerów i NPC, przypominam, liczę expa na godzinę, doradzam need albo greed "
                    "i odpowiadam z poradników. Komendy czekają w czacie na twój Enter."), ""
        if k == "reminder_add":
            return self._reminder_add(polish), ""
        if k == "reminder_list":
            items = self.reminders.pending()
            if not items:
                return "Nie masz żadnych przypomnień.", ""
            return f"Masz {len(items)}: " + "; ".join(describe_reminder(r) for r in items[:4]) + ".", ""
        if k == "reminder_cancel":
            which = "last" if re.search(r"\b(ostatnie|to)\b", fold(polish)) else \
                "next" if re.search(r"\b(najblizsze|nastepne|pierwsze)\b", fold(polish)) else "all"
            gone = self.reminders.cancel(which)
            if not gone:
                return "Nie było czego usuwać.", ""
            return ("Usunęłam przypomnienie." if len(gone) == 1 else f"Usunęłam {len(gone)} przypomnienia."), ""
        if k == "note_add":
            note = parse_note(polish, self._places())
            if note is None:
                return "Co mam zanotować?", ""
            self.reminders.add(note)
            where = f" Przypomnę ci {note.when_text}." if note.when_text else ""
            return self._v("Zanotowane.", "Zapisuję, szefie.", "Mam to.") + where, ""
        if k == "note_list":
            notes = self.reminders.pending(notes=True)
            if not notes:
                return "Nie masz żadnych notatek.", ""
            return f"Masz {len(notes)}: " + "; ".join(describe_reminder(r) for r in notes[:5]) + ".", ""
        if k == "note_done":
            words = [w for w in fold(polish).split() if w not in ("usun", "usuń", "skasuj", "notatke", "notatki",
                                                                    "notatka", "o", "te", "ta", "z", "wszystkie",
                                                                    "zrobione", "zalatwione", "gotowe", "odhacz")]
            gone = self.reminders.done(words)
            if not gone and words:
                gone = self.reminders.done([])
            if not gone:
                return "Nie było czego odhaczać.", ""
            return self._v("Odhaczone.", "Wykreślone, brawo ty.", "Załatwione, skreślam.") + \
                (f" Usunęłam {len(gone)} notatki." if len(gone) > 1 else ""), ""
        if k == "quest_worth":
            return self._worth(polish), ""
        if k == "stats":
            return self.session.polish(self.state), ""
        if k == "stats_reset":
            self.session.reset()
            return "Zeruję licznik. Liczymy od teraz.", ""
        if k == "loot":
            return self._loot_answer(), ""
        if k == "waypoint_clear":
            return "Zdejmuję znacznik, Enter.", "/wvt way clear"
        if k == "waypoint":
            return self._waypoint(polish)
        if k == "find":
            return self._lookup(polish)
        if k == "where_next":
            return self._advice(where_next(self.world_getter(), self.quests, self._player(), self.quests.pos))
        if k == "dungeon_level":
            return self._advice(dungeons_for_level(self.instances_getter() or [], self._player().level,
                                                   self.world_getter(), self.quests.pos))
        if k == "stop":
            voice = self.voice_getter()
            if voice is not None:
                voice.silence()
            return "", ""
        if k == "invite_requests":
            return self._invite_requests()
        if k == "invite_name":
            return self._invite_name(it.spoken)
        if k in ("kick", "lead"):
            return self._member_command(k, it.spoken)
        if k in ("raid", "leave", "ready"):
            return self._group_command(k)
        if k == "who_invite":
            names = self.chat.invite_requests(self._window(), self._exclude())
            if not names:
                return "Nikt nie prosił o zaproszenie w ostatnich minutach.", ""
            return f"O inv prosi {len(names)} {plural(len(names), 'osoba', 'osoby', 'osób')}: {polish_list(self._short(names))}.", ""
        if k == "whispers":
            return self._read_whispers(), ""
        if k == "message":
            return self._message(it)
        if k == "summary":
            return self._summary(), ""
        if k in ("gold", "xp", "where", "bags", "members"):
            return self._info(k), ""
        if k in ("mute", "unmute"):
            on = k == "unmute"
            if self.set_voices_enabled is not None:
                self.set_voices_enabled(on)
            return ("Włączam głosy postaci." if on else "Wyciszam głosy postaci. Mnie dalej słychać."), ""
        if k == "voice_say":
            return ("Enter i włączam głosy na say." if it.on else "Enter i wyłączam głosy na say."), \
                f"/wvt say {'on' if it.on else 'off'}"
        if k == "voice_party":
            return ("Enter i włączam głosy party." if it.on else "Enter i wyłączam głosy party."), \
                f"/wvt party {'on' if it.on else 'off'}"
        if k == "lang_pl":
            return "Enter i od teraz inni mówią po polsku.", "/wvt lang pl"
        if k == "lang_en":
            return "Enter i wracamy do angielskiego oryginału.", "/wvt lang en"
        if k.startswith("quest_"):
            return self._quest_answer(k, polish)
        answer = self._knowledge(polish)
        return (answer or self._chat(polish)), ""

    # ------------------------------------------------------------------ group
    def _window(self) -> float:
        return float(self.cfg.get("assistant.invite_window_s", 300))

    def _exclude(self) -> tuple[str, ...]:
        now = time.time()
        self._invited = {k: t for k, t in self._invited.items() if now - t < self._window()}
        st = self.state
        mine = (st.name,) if st and st.name else ()
        return tuple(self._invited) + tuple(st.members if st else ()) + mine

    @staticmethod
    def _short(names: list[str]) -> list[str]:
        return [n.split("-")[0] for n in names]

    def _invite_requests(self) -> tuple[str, str]:
        names = self.chat.invite_requests(self._window(), self._exclude())
        if not names:
            return "Nikt nie prosił o inv w ostatnich minutach. Powiedz imię, to zaproszę.", ""
        st = self.state
        room = st.free_party_slots if st is not None else 4
        if room <= 0:
            return "Party jest pełne. Powiedz zrób raid, to zrobi się miejsce.", ""
        take, left = names[:room], names[room:]
        for n in take:
            self._invited[n] = time.time()
        reply = (f"Mam {len(take)} {plural(len(take), 'chętnego', 'chętnych', 'chętnych')}: "
                 f"{polish_list(self._short(take))}. Wciśnij Enter, a ich zaproszę.")
        if left:
            reply += f" {polish_list(self._short(left))} się nie zmieści, chyba że zrobimy raid."
        return reply, "/wvt inv " + " ".join(take)

    def _known_names(self) -> list[str]:
        st = self.state
        return list(dict.fromkeys((st.members if st else []) + self.chat.names()))

    def _invite_name(self, spoken: str) -> tuple[str, str]:
        name = match_name(spoken, self._known_names())
        if name:
            self._invited[name] = time.time()
            return f"Zapraszam {name.split('-')[0]}. Enter i gotowe.", f"/wvt inv {name}"
        guess = "".join(w.capitalize() for w in fold(spoken).split()[:1])
        if not guess:
            return "Kogo mam zaprosić?", ""
        return (f"Nie widziałam nikogo takiego na czacie. Wpisałam {guess}, popraw imię przed Enterem.",
                f"/wvt inv {guess}")

    def _member_command(self, kind: str, spoken: str) -> tuple[str, str]:
        st = self.state
        if st is not None and not st.in_group:
            return "Nie jesteś w żadnej grupie.", ""
        if st is not None and not st.is_leader:
            return "Nie jesteś liderem, gra mi na to nie pozwoli.", ""
        if st is not None and st.restricted:
            return "W walce gra na to nie pozwala. Spróbuj, jak opadnie kurz.", ""
        name = match_name(spoken, (st.members if st else []) or self._known_names())
        if not name:
            return ("Kogo? Nie rozpoznałam imienia." if spoken else "Powiedz, kogo."), ""
        short = name.split("-")[0]
        if kind == "kick":
            return f"Wyrzucam {short}. Enter i po nim.", f"/wvt kick {name}"
        return f"Lider dla {short}. Wciśnij Enter.", f"/wvt lead {name}"

    def _group_command(self, kind: str) -> tuple[str, str]:
        st = self.state
        if kind == "leave":
            if st is not None and not st.in_group:
                return "Przecież jesteś solo.", ""
            return "Wychodzimy z grupy? Enter i pa pa.", "/wvt leave"
        if st is not None and not st.in_group:
            return "Najpierw musisz mieć grupę.", ""
        if st is not None and not st.is_leader:
            return "Tylko lider może to zrobić.", ""
        if st is not None and st.restricted:
            return "Teraz gra na to nie pozwala, jesteś w walce.", ""
        if kind == "raid":
            return "Robię raid. Enter.", "/wvt raid"
        return "Ready check, Enter.", "/wvt ready"

    # ------------------------------------------------------------------ chat
    def _translate(self, text: str, direction: str) -> str:
        if self.translator is None:
            return ""
        try:
            return (self.translator.translate(text, direction).text or "").strip()
        except Exception as exc:
            log.info("assistant translation failed: %s", type(exc).__name__)
            return ""

    def _read_whispers(self) -> str:
        lines = self.chat.whispers(3)
        if not lines:
            return "Nikt do ciebie nie szeptał."
        parts = []
        for ln in lines:
            pl = self._translate(ln.text, "en-pl") or ln.text
            parts.append(f"{ln.sender.split('-')[0]} pisze: {pl}")
        return " ".join(p.rstrip(".") + "." for p in parts)

    def _message(self, it: Intent) -> tuple[str, str]:
        if not it.message:
            return "Co mam napisać?", ""
        english = self._translate(it.message, "pl-en")
        if not english:
            return "Nie udało mi się przetłumaczyć, spróbuj jeszcze raz.", ""
        if it.channel == "whisper":
            target = (match_name(it.spoken, self._known_names()) if it.spoken else None) or \
                (None if it.spoken else self.chat.last_whisper_partner())
            if not target:
                return ("Nie wiem, do kogo. Nikt ostatnio nie szeptał." if not it.spoken
                        else "Nie znam tego imienia z czatu."), ""
            line = build_chat_line(f"/w {target}", english, int(self.cfg.get("translation.max_chat_chars", 255)))
            return f"Gotowe, szept do {target.split('-')[0]} czeka na Enter.", line
        cmd = CHANNEL_CMDS.get(it.channel, "/s")
        line = build_chat_line(cmd, english, int(self.cfg.get("translation.max_chat_chars", 255)))
        return "Gotowe, Enter wysyła.", line

    def _llm_text(self, messages: list[dict], max_tokens: int = 120, temperature: float = 0.8) -> str:
        if self.llm is None:
            return ""
        try:
            text, _ = self.llm.chat(messages, max_tokens=max_tokens, temperature=temperature,
                                    timeout=float(self.cfg.get("assistant.llm_timeout_s", 8)))
        except Exception as exc:
            log.info("assistant LLM failed: %s", type(exc).__name__)
            return ""
        return clean_reply(text, int(self.cfg.get("assistant.max_reply_chars", 240)))

    def _summary(self) -> str:
        lines = self.chat.recent(25)
        lines = [ln for ln in lines if not ln.is_self]
        if not lines:
            return "Cisza na czacie, nikt nic nie pisze."
        convo = "\n".join(f"[{ln.kind.lower()}] {ln.sender.split('-')[0]}: {ln.text}" for ln in lines)
        out = self._llm_text([{"role": "system", "content": SUMMARY}, {"role": "user", "content": convo}],
                             max_tokens=140, temperature=0.3)
        return out or "Nie dałam rady tego streścić."

    def _context(self) -> str:
        st = self.state
        if st is None:
            return "brak danych z gry"
        bits = [f"postać {st.name or '?'}, poziom {st.level}", f"strefa {st.zone or '?'}"]
        bits.append(f"w grupie {st.group_size} osób" if st.in_group else "gra solo")
        if st.restricted:
            bits.append("w walce")
        return ", ".join(bits)

    def _chat(self, polish: str) -> str:
        extra = str(self.cfg.get("assistant.persona_extra", "") or "")
        system = PERSONA.format(name=self.name, extra=(" " + extra) if extra else "")
        system += f"\nKontekst z gry: {self._context()}."
        shots = [{"role": role, "content": text} for q, a in STYLE_SHOTS
                 for role, text in (("user", q), ("assistant", a))]
        messages = [{"role": "system", "content": system}, *shots, *self._history, {"role": "user", "content": polish}]
        out = self._llm_text(messages)
        if out:
            self._history.append({"role": "user", "content": polish})
            self._history.append({"role": "assistant", "content": out})
        return out or "Hm, zawiesiłam się na chwilę. Powtórzysz?"

    # ------------------------------------------------------------------ quests
    _GENERIC = {"gdzie", "mam", "musze", "isc", "ide", "teraz", "dalej", "w", "ktora", "strone", "jak", "tam", "dojsc",
                "trafic", "dotrzec", "dojechac", "ile", "mi", "zostalo", "brakuje", "jeszcze", "jest", "sa", "to", "ten",
                "ta", "te", "quest", "questa", "questu", "questy", "questow", "quescie", "kwescie", "zadanie", "zadania", "cel", "celu", "ej",
                "hej", "iskra", "prosze", "powiedz", "a", "i", "mnie", "prowadz", "znajde", "szukac", "idzie", "co", "z",
                "do", "na", "po", "o", "chodzi", "w", "tym", "tej", "lezy", "leza", "moj", "moje", "moim", "nastepny",
                "nastepne", "zebralem", "zebralam", "robic", "zrobic", "teraz"}

    def _names_something(self, polish: str) -> bool:
        return any(w not in self._GENERIC and len(w) > 2 for w in fold(polish).split())

    def _pick_quest(self, polish: str, quests: list) -> tuple[Optional[protocol.QuestEntry], bool]:
        """(the quest the question is about, whether it was named).  None: it names something not in the log."""
        english = self._translate(polish, "pl-en")
        scored = match_quests(f"{english} {polish}", quests)
        if scored:
            return scored[0][1], True
        if self._names_something(polish):
            return None, False
        open_quests = [q for q in quests if not q.complete]
        tracked = [q for q in open_quests if q.tracked]
        if len(tracked) == 1:
            return tracked[0], False
        near = self.quests.nearest(open_quests)
        if near:
            return near[0], False
        return (tracked or open_quests or quests)[0], False

    @staticmethod
    def _progress(q: protocol.QuestEntry) -> str:
        if q.complete:
            return f"{q.title} – zrobione, do oddania"
        objs = [spoken_objective(t, done) for done, t in q.objectives if not done] or \
            [spoken_objective(t, done) for done, t in q.objectives]
        return f"{q.title}: {', '.join(objs)}" if objs else q.title

    def _where_text(self, q: protocol.QuestEntry) -> str:
        w = self.quests.where(q)
        if w is None:
            return f"Nie mam znacznika dla {q.title} na mapie." + (f" Cel: {self._progress(q).split(': ', 1)[-1]}."
                                                                   if q.objectives else "")
        prefix = f"{q.title} jest gotowy do oddania, punkt oddania: " if q.complete else f"{q.title}: "
        return prefix + w.polish() + "."

    def _quest_facts(self, quests: list) -> str:
        lines = []
        for q in quests[:4]:
            w = self.quests.where(q)
            bits = [f"poziom {q.level}"] + (["gotowy do oddania"] if q.complete else []) + \
                ([f"na grupę {q.group} osób"] if q.group > 1 else [])
            objs = ", ".join(spoken_objective(t, d) for d, t in q.objectives) or "brak celów"
            lines.append(f"- {q.title} ({', '.join(bits)}): cele: {objs}; {w.polish() if w else 'brak znacznika'}")
        return "\n".join(lines)

    def _quest_answer(self, kind: str, polish: str) -> tuple[str, str]:
        book = self.quests
        if kind == "quest_where" and (not book.known or not book.quests()):
            return self._lookup(polish)
        if not book.known:
            return "Nie widzę jeszcze twojego dziennika zadań. Daj mi chwilę po /reload albo wpisz /wvt asystent on.", ""
        quests = book.quests()
        if not quests:
            return "Twój dziennik zadań jest pusty. Czas złapać jakiś quest.", ""
        return self._quest_text(kind, polish, quests)

    def _quest_text(self, kind: str, polish: str, quests: list) -> tuple[str, str]:
        book = self.quests
        done = [q for q in quests if q.complete]
        todo = [q for q in quests if not q.complete]
        if kind == "quest_list":
            n = len(quests)
            parts = [f"Masz {n} {plural(n, 'quest', 'questy', 'questów')}."]
            if done:
                parts.append(f"Do oddania: {polish_list([q.title for q in done[:3]])}.")
            if todo:
                shown = [self._progress(q) for q in todo[:3]]
                parts.append("W toku: " + "; ".join(shown) + ("; i jeszcze inne." if len(todo) > 3 else "."))
            return " ".join(parts), ""
        if kind == "quest_turnin":
            if not done:
                return "Jeszcze nic nie jest skończone. " + (f"Najbliżej końca: {self._progress(todo[0])}." if todo else ""), ""
            near = book.nearest(done)
            out = f"Do oddania masz: {polish_list([q.title for q in done[:4]])}."
            if near:
                out += f" Najbliżej oddasz {near[0].title}, {near[1].polish()}."
            return out, (f"/wvt track {near[0].quest_id}" if near and self._waypoints_on() else "")
        if kind == "quest_nearest":
            near = book.nearest(todo) or book.nearest(quests)
            if not near:
                return "Żaden quest nie ma teraz znacznika na tej mapie.", ""
            return f"Najbliżej masz {near[0].title}, {near[1].polish()}.", \
                (f"/wvt track {near[0].quest_id}" if self._waypoints_on() else "")
        target, named = self._pick_quest(polish, quests)
        if target is None:  # asks about something that is not in the quest log
            return self._lookup(polish) if kind == "quest_where" else ((self._knowledge(polish) or self._chat(polish)), "")
        if kind == "quest_where":
            return self._where_text(target), (f"/wvt track {target.quest_id}" if self._waypoints_on() else "")
        if kind == "quest_progress":
            return self._progress(target) + ".", ""
        # anything else about quests: the LLM, with the quest log as the only facts
        picked = [target] + [q for q in quests if q is not target]
        extra = str(self.cfg.get("assistant.persona_extra", "") or "")
        system = PERSONA.format(name=self.name, extra=(" " + extra) if extra else "")
        system += ("\nDziennik zadań gracza (to są jedyne fakty o questach, nie dodawaj niczego od siebie):\n"
                   + self._quest_facts(picked))
        out = self._llm_text([{"role": "system", "content": system}, {"role": "user", "content": polish}])
        return (out or self._progress(target) + "."), ""

    # ------------------------------------------------------------------ where next, places, waypoints
    def _player(self) -> Player:
        st, info = self.state, self.quests.player
        return Player(level=st.level if st else 0, race_id=info.race_id if info else 0,
                      class_id=info.class_id if info else 0, faction=info.faction_code if info else "",
                      done=self.quests.done, done_known=self.quests.done_known)

    def _waypoints_on(self) -> bool:
        return bool(self.cfg.get("assistant.auto_waypoint", True))

    def _advice(self, adv: Advice) -> tuple[str, str]:
        self._last_advice = adv
        line = adv.line if self._waypoints_on() else ""
        if line.startswith("/wvt way"):
            return adv.text + " " + self._v("Znacznik czeka na Enter.", "Enter i masz pinezkę na mapie.",
                                             "Wciśnij Enter, to cię poprowadzę."), line
        if line.startswith("/wvt track"):
            return adv.text + " Enter włączy strzałkę.", line
        return adv.text, line

    def _lookup(self, polish: str) -> tuple[str, str]:
        """'Gdzie jest X?' that is not about the quest log: services, dungeon entrances, NPCs, then the guides."""
        english = self._translate(polish, "pl-en")
        adv = find_place(self.world_getter(), polish, self._player(), self.quests.pos, english)
        if adv is not None:
            return self._advice(adv)
        return (self._knowledge(polish, english) or self._chat(polish)), ""

    _POINTERS = {"to", "tam", "tego", "ten", "te", "ta", "tu", "tutaj", "go", "ja", "je", "mi", "mnie", "na", "mapie",
                 "znacznik", "znacznika", "punkt", "pinezke", "prosze", "ej", "iskra", "postaw", "ustaw", "daj", "zrob",
                 "dodaj", "wstaw", "zaznacz", "pokaz", "wskaz", "zaprowadz", "doprowadz", "prowadz", "poprowadz",
                 "nawiguj", "do", "mnie", "waypoint", "marker", "jakis", "tego", "miejsca", "miejsce", "w"}

    def _waypoint(self, polish: str) -> tuple[str, str]:
        words = [w for w in fold(polish).split() if w not in self._POINTERS]
        if not words:
            adv = self._last_advice
            if adv and adv.line:
                return ("Stawiam znacznik, Enter." if adv.line.startswith("/wvt way") else "Włączam strzałkę, Enter."), \
                    adv.line
            return "Gdzie mam postawić znacznik? Powiedz na przykład: zaprowadź mnie do trenera.", ""
        english = self._translate(polish, "pl-en")
        if self.quests.known:
            scored = match_quests(f"{english} {polish}", self.quests.quests())
            if scored:
                q = scored[0][1]
                self._last_advice = Advice(q.title, f"/wvt track {q.quest_id}", quest_id=q.quest_id)
                return f"Prowadzę do {q.title}. Enter włączy strzałkę.", f"/wvt track {q.quest_id}"
        adv = find_place(self.world_getter(), polish, self._player(), self.quests.pos, english)
        if adv is None:
            return "Nie wiem, gdzie to jest. Spróbuj podać nazwę z gry.", ""
        if not adv.line:
            return adv.text, ""
        self._last_advice = adv
        return adv.text + " Znacznik czeka na Enter.", adv.line

    def _places(self) -> list[str]:
        world = self.world_getter()
        return list(world.names.values()) if world is not None else []

    def _reminder_add(self, polish: str) -> str:
        parsed = parse_reminder(polish, places=self._places())
        if parsed is None:
            return ("Kiedy mam przypomnieć? Powiedz na przykład: za dziesięć minut, o dwudziestej, na dwudziestym "
                    "poziomie albo w Ironforge.")
        r = self.reminders.add(parsed)
        what = f", {r.message}" if r.message else ""
        return self._v(f"Dobra, przypomnę ci {parsed.when_text}{what}.",
                       f"Zapisane: {parsed.when_text}{what}. Ja nie zapomnę, w przeciwieństwie do niektórych.",
                       f"Jasne, {parsed.when_text} dam ci znać{what}.")

    def _worth(self, polish: str) -> str:
        """'Czy warto robić ten quest?': the named quest, the one just taken, or the tracked one."""
        target = None
        if self.quests.known:
            quests = self.quests.quests()
            english = self._translate(polish, "pl-en")
            scored = match_quests(f"{english} {polish}", quests)
            if scored:
                target = scored[0][1].quest_id
            elif self._last_accepted and time.time() - self._last_accepted[0] < 900:
                target = self._last_accepted[1]
            else:
                tracked = [q for q in quests if q.tracked and not q.complete]
                target = tracked[0].quest_id if tracked else None
        if target is None:
            return "O który quest chodzi? Powiedz nazwę albo weź go najpierw do dziennika."
        a = self._assess(target)
        if a is None:
            return "Tego questa nie mam w bazie, więc nie ocenię. Pewnie coś nowego z Forever."
        situation = f"Gracz pyta, czy warto robić questa {a.title}."
        return self._persona_line(situation, a.facts + [f"Ocena: {self._verdict_pl(a.verdict)}."], f"{a.title}: {a.text()}")

    def _loot_answer(self) -> str:
        now = time.time()
        recent = [adv for t, adv in self._loot if now - t < 180]
        if not recent:
            return "Nic ostatnio nie wypadło do losowania."
        return " ".join(a.text for a in recent[-3:])

    # ------------------------------------------------------------------ guides (local knowledge base)
    _ABILITY_RE = re.compile(r"\b(umiejetn\w*|skill\w*|spell\w*|czar\w*|zaklec\w*|talent\w*|trener\w*|naucz\w*|zdolnosc\w*|rotacj\w*)\b")
    _MY_LEVEL_RE = re.compile(r"\b(moj\w* poziom\w*|moim poziomie|moj\w* level\w*|moim levelu|na moj\w*|dla mnie)\b")

    _GAME_WORDS = re.compile(
        r"\b(quest\w*|kwest\w*|zadani\w*|dung\w*|loch\w*|instancj\w*|raid\w*|rajd\w*|boss\w*|mob\w*|npc|potwor\w*"
        r"|przedmiot\w*|item\w*|bron\w*|zbroj\w*|pancerz\w*|skill\w*|umiejetn\w*|zdolnosc\w*|talent\w*|spell\w*"
        r"|czar\w*|zaklec\w*|klas\w*|ras\w*|profesj\w*|zawod\w*|level\w*|poziom\w*|lvl|stref\w*|kontynent\w*|frakcj\w*"
        r"|przymierz\w*|alliance|hord\w*|trener\w*|wierzchow\w*|mount\w*|eliksir\w*|mikstur\w*|przepis\w*|receptur\w*"
        r"|reputacj\w*|frakcj\w*|gildi\w*|pvp|arena\w*|battleground\w*|zlot\w*|miasto|miasta|wysp\w*|lore|historia"
        r"|forever|skyborne|azeroth|kalimdor|wow|warcraft|gra|grze|grac|tank\w*|heal\w*|dps|aggro|loot\w*|drop\w*"
        r"|craft\w*|farm\w*|exp\w*|karczm\w*|odpoczyn\w*|rested|hearth\w*|kamien\w*)\b")
    _PROPER_NOUN = re.compile(r"(?<=[\w,] )[A-Z][a-z]*[A-Z]?[a-z']+")

    def _is_game_question(self, polish: str, english: str) -> bool:
        """Small talk must not turn into 'I don't know' from the guides: a game word, or a name from the game."""
        return bool(self._GAME_WORDS.search(fold(polish)) or self._PROPER_NOUN.search(english or ""))

    def _knowledge(self, polish: str, english: Optional[str] = None) -> str:
        kb = self.kb_getter()
        if kb is None:
            return ""
        floor = float(self.cfg.get("assistant.kb_min_score", 0.8))
        try:
            quick = kb.search(polish, "", k=1)
        except Exception:
            log.exception("knowledge base search failed")
            return ""
        if not quick or quick[0].cosine < floor - 0.03:
            return ""  # nothing close at all
        english = self._translate(polish, "pl-en") if english is None else english
        if not self._is_game_question(polish, english):
            return ""
        p = self._player()
        query = english
        folded = fold(polish)
        if self._ABILITY_RE.search(folded) and p.class_id in CLASS_EN:
            query += f" {CLASS_EN[p.class_id]} abilities (Forever)"
        if p.level and self._MY_LEVEL_RE.search(folded):
            query += f" level {p.level}"
        hits = kb.search(polish, query, k=4)
        if not hits or max(h.cosine for h in hits) < floor:
            return ""
        context = "\n\n".join(f"[{h.title}{' > ' + h.section if h.section else ''}]\n{h.text[:900]}" for h in hits)
        extra = str(self.cfg.get("assistant.persona_extra", "") or "")
        system = PERSONA.format(name=self.name, extra=(" " + extra) if extra else "") + KNOWLEDGE
        who = self._context() + (f", klasa {CLASS_EN[p.class_id]}" if p.class_id in CLASS_EN else "")
        system += f"Gracz: {who}.\nFragmenty:\n{context}"
        out = self._llm_text([{"role": "system", "content": system}, {"role": "user", "content": polish}],
                             max_tokens=170, temperature=0.3)
        log.info("knowledge answer from %s", ", ".join(h.title for h in hits[:3]))
        return out

    def _info(self, kind: str) -> str:
        st = self.state
        if st is None or time.time() - self.state_at > 120:
            return "Nie widzę danych z gry. Sprawdź, czy dodatek jest włączony: wpisz /wvt asystent on."
        if kind == "gold":
            silver, copper = divmod(st.silver_copper, 100)
            return f"Masz {st.gold} złota, {silver} srebra i {copper} miedzi."
        if kind == "xp":
            if st.xp_max <= 0:
                return f"Jesteś na poziomie {st.level}, dalej już się nie da."
            need = max(0, st.xp_max - st.xp)
            pct = round(100 * st.xp / st.xp_max)
            return (f"Poziom {st.level}, masz {pct} procent paska. Do następnego brakuje {need} doświadczenia."
                    + self.session.eta_text(st))
        if kind == "where":
            return f"Jesteś w {st.zone}" + (f", dokładnie {st.subzone}." if st.subzone and st.subzone != st.zone else ".")
        if kind == "bags":
            return f"Masz {st.bag_free} wolnych miejsc na {st.bag_slots}."
        if not st.in_group:
            return "Jesteś solo. Mam kogoś zaprosić?"
        if st.in_raid:
            return f"Jesteś w raidzie, razem {st.group_size} osób."
        others = self._short(st.members)
        return f"W party masz {polish_list(others)}." if others else "Jesteś w grupie, ale nie widzę imion."

    # ------------------------------------------------------------------ output
    def _respond(self, reply: str, line: str, insert_status: str) -> None:
        footer = ""
        if line:
            footer = {"inserted": "W czacie – Enter wykonuje, Esc anuluje",
                      "copied": "WoW nie był aktywny – skopiowane do schowka"}.get(insert_status, insert_status)
        lines = [f"{self.name}: {reply}"] if reply else []
        if line:
            lines.append(line)
        if reply or line:
            self.ui.status("ok", footer or self.name, lines, float(self.cfg.get("hud.show_seconds", 6)) + 2)
        if reply:
            self._speak(reply)

    def _speak(self, text: str) -> None:
        voice = self.voice_getter()
        voice_id = str(self.cfg.get("assistant.voice_id", "") or "")
        if voice is None or not voice_id:
            if not self._warned_no_voice:
                self._warned_no_voice = True
                log.info("assistant has no voice yet (assistant.voice_id or ElevenLabs missing) - text only")
            return
        voice.say(text, voice_id, "pl", float(self.cfg.get("assistant.volume", 1.0)), speaker=self.name)


_EMOJI_RE = re.compile("[\U0001F000-\U0001FAFF☀-➿]")


def clean_reply(text: str, limit: int = 240) -> str:
    """One or two plain spoken sentences: no markup, emoji, quotes or a half sentence at the end."""
    t = re.sub(r"<[^>]+>|\*+|#+|`+", "", text or "")
    t = _EMOJI_RE.sub("", t)
    t = " ".join(t.replace("\n", " ").split()).strip(" \"'„”")
    if len(t) > limit:
        cut = t[:limit]
        end = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
        t = cut[: end + 1] if end > 40 else cut.rsplit(" ", 1)[0] + "…"
    return t
