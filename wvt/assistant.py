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
* anything else - a short chat in character (the local LLM; she may be wrong about game facts).

Commands are matched by rules on the Polish text (fast, predictable, nothing invented); the LLM only
talks and summarises.  Names that Parakeet hears in Polish grammar ("zaproś Artasa", "daj lidera
Jainie") are matched to the names seen in chat and in the group.
"""

from __future__ import annotations

import difflib
import logging
import re
import threading
import time
import unicodedata
from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Optional

from . import protocol
from .chat_insert import build_chat_line

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
    ("help", r"\b(co umiesz|co potrafisz|w czym (mi )?pomozesz|jakie (masz )?komendy|pomocy?)\b"),
    ("stop", r"^(stop|cicho|cisza|przestan\w*|zamknij sie|wystarczy|dosc)\b"),
    ("lang_pl", r"\b(przelacz\w*|zmien\w*|ustaw\w*|tryb\w*|niech|mow\w*|czytaj\w*|tlumacz\w*)\b.*"
                r"\b(po polsku|na polski|polski)\b"),
    ("lang_en", r"\b(przelacz\w*|zmien\w*|ustaw\w*|tryb\w*|niech|mow\w*|czytaj\w*|wroc\w*)\b.*"
                r"\b(po angielsku|na angielski|angielski|w oryginale|oryginal)\b"),
    ("voice_say", r"\b(wlacz\w*|wylacz\w*)\b.*\b(say|sej\w*|sayu)\b"),
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
    if m:
        lead = len(t[: m.start(1)].split())
        return _message_intent(m.group(1), words[lead:], original[lead:])
    for kind, rx in _COMPILED:
        if not rx.search(t):
            continue
        if kind in ("voice_say", "voice_party", "mute", "unmute"):
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
    "Jesteś {name} – asystentką gracza w World of Warcraft (WoW Forever). Masz około 25 lat, jesteś pewna siebie, "
    "charyzmatyczna i zadziorna, lubisz się droczyć i lekko flirtować, ale zawsze pomagasz. Mówisz po polsku, "
    "naturalnie i krótko: najwyżej dwa zdania, bez emotek, bez list i bez nawiasów, bo twoja odpowiedź zostanie "
    "przeczytana na głos. Zwracasz się do gracza per ty. Nie zmyślasz faktów o grze: jeśli nie wiesz czegoś na "
    "pewno, przyznaj to z humorem. Polecenia (zaproszenia, szepty, przełączanie głosów) wykonuje osobny moduł – "
    "ty tylko rozmawiasz.{extra}"
)
# Her style by example: the small local model copies these far better than it follows a description.
STYLE_SHOTS = (
    ("Hej, jak się masz?", "Teraz, kiedy jesteś, dużo lepiej. Co dziś rozwalamy?"),
    ("Nudzi mi się.", "Nudzisz się ze mną? Auć. Chodź, znajdziemy jakąś grupę do lochu, a ja popatrzę, jak się popisujesz."),
    ("Powiedz mi coś miłego.", "Masz najlepszy refleks w całym Elwynn. I świetny gust, skoro gadasz właśnie ze mną."),
    ("Gdzie kupię lepszy miecz?", "U kowali w mieście, tylko dokładnej mapy nie mam w głowie. Zapytaj strażnika, oni wiedzą wszystko."),
)
SUMMARY = (
    "Streść po polsku, w jednym lub dwóch krótkich zdaniach mówionych, o czym rozmawiają ludzie na czacie WoW. "
    "Używaj imion postaci. Bez list, bez cudzysłowów. Jeśli to tylko spam albo nic ważnego, powiedz to krótko."
)


class Assistant:
    def __init__(self, cfg, translator, llm, inserter, ui, wow_foreground: Callable[[], bool],
                 voice_getter: Callable[[], object] = lambda: None,
                 set_voices_enabled: Optional[Callable[[bool], None]] = None):
        self.cfg = cfg
        self.translator = translator
        self.llm = llm
        self.inserter = inserter
        self.ui = ui
        self.wow_foreground = wow_foreground
        self.voice_getter = voice_getter
        self.set_voices_enabled = set_voices_enabled
        self.chat = ChatLog()
        self.state: Optional[protocol.GameState] = None
        self.state_at = 0.0
        self._invited: dict[str, float] = {}
        self._history: deque[dict] = deque(maxlen=6)
        self._warned_no_voice = False

    @property
    def name(self) -> str:
        return str(self.cfg.get("assistant.name", "Iskra") or "Iskra")

    # ------------------------------------------------------------------ input from the addon
    def on_packet(self, pkt: protocol.Packet) -> None:
        if pkt.type == protocol.TYPE_STATE and pkt.state is not None:
            self.state, self.state_at = pkt.state, time.time()
        elif pkt.type in (protocol.TYPE_VOICE, protocol.TYPE_CHAT):
            self.chat.add_packet(pkt)

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
            return ("Zapraszam tych, co piszą inv, wyrzucam, daję lidera, robię raid i ready check, czytam szepty, "
                    "odpisuję, streszczam czat i mówię, ile masz złota. Wszystko czeka w czacie na twój Enter."), ""
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
        return self._chat(polish), ""

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
            return f"Poziom {st.level}, masz {pct} procent paska. Do następnego brakuje {need} doświadczenia."
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
