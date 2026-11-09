"""Reminders for the assistant: "przypomnij mi za 10 minut, że...", "o 21:30...", "na 20 poziomie...".

* :func:`parse_reminder` reads the Polish sentence as Parakeet writes it (digits or words: "za dziesięć
  minut", "o dwudziestej pierwszej trzydzieści", "za pół godziny", "za kwadrans", "jutro o 9",
  "na dwudziestym poziomie") and turns the message into the second person ("że mam" -> "że masz").
* :class:`Reminders` keeps them in ``data/reminders.json`` (they survive a restart), fires time
  reminders from a background thread and level reminders when the addon reports that level.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
import unicodedata
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Optional

log = logging.getLogger(__name__)


def _fold(word: str) -> str:
    w = word.lower().replace("ł", "l")
    w = unicodedata.normalize("NFKD", w)
    return "".join(c for c in w if not unicodedata.combining(c))


# ---------------------------------------------------------------- numbers spoken in Polish
_CARDINAL = {
    "zero": 0, "jeden": 1, "jedna": 1, "jedno": 1, "jednej": 1, "jednego": 1, "dwa": 2, "dwie": 2, "dwoch": 2,
    "trzy": 3, "trzech": 3, "cztery": 4, "czterech": 4, "piec": 5, "pieciu": 5, "szesc": 6, "szesciu": 6,
    "siedem": 7, "siedmiu": 7, "osiem": 8, "osmiu": 8, "dziewiec": 9, "dziewieciu": 9, "dziesiec": 10,
    "dziesieciu": 10, "jedenascie": 11, "jedenastu": 11, "dwanascie": 12, "dwunastu": 12, "trzynascie": 13,
    "trzynastu": 13, "czternascie": 14, "czternastu": 14, "pietnascie": 15, "pietnastu": 15, "szesnascie": 16,
    "szesnastu": 16, "siedemnascie": 17, "siedemnastu": 17, "osiemnascie": 18, "osiemnastu": 18,
    "dziewietnascie": 19, "dziewietnastu": 19, "dwadziescia": 20, "dwudziestu": 20, "trzydziesci": 30,
    "trzydziestu": 30, "czterdziesci": 40, "czterdziestu": 40, "piecdziesiat": 50, "piecdziesieciu": 50,
    "szescdziesiat": 60, "szescdziesieciu": 60,
}
_ORDINAL_STEMS = {
    "pierwsz": 1, "drug": 2, "trzec": 3, "czwart": 4, "piat": 5, "szost": 6, "siodm": 7, "osm": 8,
    "dziewiat": 9, "dziesiat": 10, "jedenast": 11, "dwunast": 12, "trzynast": 13, "czternast": 14,
    "pietnast": 15, "szesnast": 16, "siedemnast": 17, "osiemnast": 18, "dziewietnast": 19, "dwudziest": 20,
    "trzydziest": 30, "czterdziest": 40, "piecdziesiat": 50, "szescdziesiat": 60,
}
_ORD_ENDINGS = ("y", "i", "a", "e", "ej", "iej", "ego", "iego", "emu", "iemu", "ym", "im", "ymi", "ych", "ich")


def _ordinal(w: str) -> Optional[int]:
    for stem, v in sorted(_ORDINAL_STEMS.items(), key=lambda kv: -len(kv[0])):
        if w.startswith(stem) and w[len(stem):] in _ORD_ENDINGS:
            return v
    return None


def read_number(words: list[str], i: int, ordinal: bool = False) -> tuple[Optional[float], int]:
    """(value, words used) of the number starting at ``words[i]`` (folded words); (None, 0) if none."""
    if i >= len(words):
        return None, 0
    w = words[i]
    m = re.fullmatch(r"(\d{1,3})(?:[.,](\d))?", w)
    if m:
        return float(m.group(1) + ("." + m.group(2) if m.group(2) else "")), 1
    get = _ordinal if ordinal else _CARDINAL.get
    v = get(w)
    if v is None and not ordinal:
        v = _ordinal(w)  # "na dwudziestym" in a cardinal place still means 20
    if v is None:
        return None, 0
    if v >= 20 and i + 1 < len(words):  # dwadzieścia pięć, dwudziestej pierwszej
        u = get(words[i + 1])
        if u is not None and 1 <= u <= 9:
            return float(v + u), 2
    return float(v), 1


# ---------------------------------------------------------------- the sentence
_UNITS = (("sekund", 1), ("sek", 1), ("minut", 60), ("min", 60), ("godzin", 3600), ("godz", 3600), ("h", 3600))
_SECOND_PERSON = {
    "mam": "masz", "muszę": "musisz", "musze": "musisz", "chcę": "chcesz", "mogę": "możesz", "jestem": "jesteś",
    "będę": "będziesz", "idę": "idziesz", "zrobię": "zrobisz", "kupię": "kupisz", "sprzedam": "sprzedasz",
    "miałem": "miałeś", "miałam": "miałaś", "zrobiłem": "zrobiłeś", "zrobiłam": "zrobiłaś", "mój": "twój",
    "moja": "twoja", "moje": "twoje", "mojego": "twojego", "mojej": "twojej", "moich": "twoich", "moją": "twoją",
    "moim": "twoim", "mi": "ci", "mnie": "ciebie", "żebym": "żebyś", "abym": "abyś", "bym": "byś",
    "zebym": "żebyś", "powinienem": "powinieneś", "powinnam": "powinnaś", "wezmę": "weźmiesz", "zjem": "zjesz",
}
_CONJ = ("że", "żeby", "żebyś", "żebym", "aby", "abym", "by", "o", "ze", "zeby")


@dataclass
class ParsedReminder:
    message: str  # second person, ready to say after "Przypominam"
    due: float = 0.0  # epoch seconds (time reminder)
    level: int = 0  # level reminder
    when_text: str = ""  # "za 10 minut", "o 21:30", "na 20 poziomie", "w Ironforge"
    place: str = ""  # zone name, "city" (a capital) or "ah" (a place with an auction house)
    keep: bool = False  # a note: stays until done, reminded at most every NOTE_COOLDOWN_S


# capitals and auction houses of WoW Forever (zone / subzone names as GetZoneText / GetSubZoneText give them)
CITIES = ("Stormwind City", "Ironforge", "Darnassus", "Orgrimmar", "Undercity", "Thunder Bluff", "Dalaran")
AH_PLACES = CITIES[:6] + ("Booty Bay", "Gadgetzan", "Everlook")
NOTE_COOLDOWN_S = 1800
_PLACE_ENDINGS = ("ach", "ami", "owi", "zie", "ze", "em", "ie", "u", "a", "e", "y")


def _place_key(name: str) -> str:
    return _fold(name).replace(" city", "").strip()


def match_place(words: list[str], places: list[str]) -> str:
    """A zone named in the folded words ("orgrimmarze" -> "Orgrimmar", "stormwindzie" -> "Stormwind City")."""
    keys = {_place_key(p): p for p in places if p}
    for n in (3, 2, 1):
        for i in range(len(words) - n + 1):
            phrase = " ".join(words[i:i + n])
            cands = [phrase] + [phrase[: -len(e)] for e in _PLACE_ENDINGS
                                if phrase.endswith(e) and len(phrase) - len(e) >= 4]
            for c in cands:
                if c in keys:
                    return keys[c]
    return ""


def parse_place(text: str, places=()) -> str:
    """'ah', 'city' or a zone name the sentence ties the reminder to, or ''."""
    words = [_fold(w) for w in re.findall(r"[\w']+", text or "")]
    t = " ".join(words)
    named = match_place(words, list(places) + list(CITIES) + list(AH_PLACES))
    if named:  # "w Ironforge ... na AH": the named place wins
        return named
    if re.search(r"\b(ah|aukcj\w*|aukcyjn\w*|auction\w*)\b", t):
        return "ah"
    if re.search(r"\b(w|we|do|na)\s+(stolic\w*|miesc\w*|miast\w*)\b", t):
        return "city"
    return ""


def place_matches(place: str, zone: str, subzone: str = "") -> bool:
    if not place:
        return False
    if place == "city":
        return zone in CITIES
    if place == "ah":
        return zone in AH_PLACES or subzone in AH_PLACES
    k = _place_key(place)
    return k in (_place_key(zone), _place_key(subzone))


def _duration_text(seconds: float) -> str:
    s = int(round(seconds))
    if s < 60:
        return f"za {s} s"
    h, m = divmod(s // 60, 60)
    if h and m:
        return f"za {h} godz. {m} min"
    if h:
        return f"za {h} godz."
    return f"za {m} min"


def parse_reminder(text: str, now: Optional[float] = None, places=()) -> Optional[ParsedReminder]:
    """The reminder in a Polish sentence, or None when there is no time or level in it."""
    now = time.time() if now is None else now
    orig = re.findall(r"\d{1,2}[:.]\d{2}|[\w']+", text or "")
    words = [_fold(w) for w in orig]
    if not words:
        return None
    used = [False] * len(words)
    due, level, when = 0.0, 0, ""

    def take(a: int, b: int) -> None:
        for k in range(a, min(b, len(used))):
            used[k] = True

    # level: "na 20 poziomie", "na dwudziestym levelu", "jak wbiję 20 level", "przy poziomie 20"
    for i, w in enumerate(words):
        if re.match(r"^(poziom|level|lewel|lvl|lv)\w*$", w):
            v, n = read_number(words, i + 1)
            if v is not None and 1 <= v <= 80:
                level = int(v)
                start = i - 1 if i > 0 and words[i - 1] in ("na", "przy", "po", "od") else i
                take(start, i + 1 + n)
                break
            j = i - 1
            while j >= 0 and words[j] in ("ten", "nastepny"):
                j -= 1
            # number before the noun (one or two words back)
            for back in (2, 1):
                k = i - back
                if k >= 0:
                    v, n = read_number(words, k)
                    if v is not None and k + n == i and 1 <= v <= 80:
                        level = int(v)
                        start = k - 1 if k > 0 and words[k - 1] in ("na", "przy", "po", "od") else k
                        while start > 0 and words[start - 1] in ("wbije", "dobije", "osiagne", "bede", "mial",
                                                                  "miala", "miec", "zrobie", "dostane", "jak", "gdy",
                                                                  "kiedy", "wskocze"):
                            start -= 1
                        take(start, i + 1)
                        break
            if level:
                break

    # relative time: "za 10 minut", "za pół godziny", "za kwadrans", "za godzinę i 15 minut"
    if not level:
        for i, w in enumerate(words):
            if w != "za":
                continue
            total, j = 0.0, i + 1
            while j < len(words):
                if words[j] == "kwadrans":
                    total += 900
                    j += 1
                elif words[j] in ("chwile", "chwilke", "moment"):
                    total += 300
                    j += 1
                else:
                    if words[j] in ("pol",):
                        v, n = 0.5, 1
                    elif words[j] == "poltorej":
                        v, n = 1.5, 1
                    else:
                        v, n = read_number(words, j)
                    unit_at = j + n if v is not None else j
                    if unit_at >= len(words):
                        break
                    unit = next((sec for name, sec in _UNITS if words[unit_at].startswith(name)), None)
                    if unit is None:
                        break
                    total += (1.0 if v is None else v) * unit
                    j = unit_at + 1
                if j < len(words) and words[j] == "i" and j + 1 < len(words) and (
                        read_number(words, j + 1)[0] is not None or words[j + 1] in ("pol", "kwadrans")):
                    j += 1
                    continue
                break
            if total > 0:
                due = now + total
                when = _duration_text(total)
                take(i, j)
                break

    # clock time: "o 21:30", "o 21 30", "o dwudziestej pierwszej trzydzieści", "jutro o 9"
    if not level and not due:
        for i, w in enumerate(words):
            if w != "o" or i + 1 >= len(words):
                continue
            j = i + 1
            if words[j] in ("godzinie", "godz"):
                j += 1
            m = re.fullmatch(r"(\d{1,2})[:.](\d{2})", words[j]) if j < len(words) else None
            if m:
                hour, minute, n = int(m.group(1)), int(m.group(2)), 1
            else:
                hv, n = read_number(words, j, ordinal=not words[j].isdigit()) if j < len(words) else (None, 0)
                if hv is None:
                    continue
                hour, minute = int(hv), 0
                mv, mn = read_number(words, j + n)
                if mv is not None and 0 <= mv < 60 and (j + n + mn >= len(words)
                                                        or not words[j + n + mn].startswith(("minut", "godzin"))):
                    minute, n = int(mv), n + mn
            if not (0 <= hour <= 24 and 0 <= minute < 60):
                continue
            base = datetime.fromtimestamp(now)
            target = base.replace(hour=hour % 24, minute=minute, second=0, microsecond=0)
            if "jutro" in words:
                target += timedelta(days=1)
                take(words.index("jutro"), words.index("jutro") + 1)
            elif target.timestamp() <= now + 30:
                if hour <= 12 and (target + timedelta(hours=12)).timestamp() > now + 30:
                    target += timedelta(hours=12)  # "o 9" said in the evening means 21:00
                else:
                    target += timedelta(days=1)
            due = target.timestamp()
            when = f"o {target.hour}:{target.minute:02d}"
            take(i, j + n)
            break

    place = ""
    if not due and not level:
        place = parse_place(text, places)
        if not place:
            return None
        # drop "jak będę / gdy wejdę" and "w / do / przy <place>" from the message
        for i, w in enumerate(words):
            if w in ("jak", "gdy", "kiedy") and i + 1 < len(words) and words[i + 1] in (
                    "bede", "wejde", "dotre", "dojde", "polece", "wroce", "znajde", "zajrze"):
                take(i, i + 2)
        for i, w in enumerate(words):
            if w in ("w", "we", "do", "na", "przy") and i + 1 < len(words):
                nxt = words[i + 1]
                if place in ("ah", "city") and re.match(r"^(ah|aukcj|domu|stolic|miesc|miast)", nxt):
                    take(i, i + (3 if nxt == "domu" else 2))
                elif place not in ("ah", "city") and _place_key(place).startswith(nxt[:4]):
                    take(i, i + 1 + len(_place_key(place).split()))
        when = {"ah": "przy domu aukcyjnym", "city": "w mieście"}.get(place, f"w {place}")
    # the message: what is left after "przypomnij (mi)" and the time, from the conjunction on
    for i, w in enumerate(words):
        if w.startswith("przypomn") or w.startswith("przypomin"):
            take(i, i + 1)
            if i + 1 < len(words) and words[i + 1] in ("mi", "mnie", "nam"):
                take(i + 1, i + 2)
    rest = [orig[k] for k in range(len(orig)) if not used[k]]
    while rest and _fold(rest[0]) in ("prosze", "ej", "hej", "iskra", "i", "a", "no", "to"):
        rest = rest[1:]
    out = [_SECOND_PERSON.get(w.lower(), w) for w in rest]
    message = " ".join(out).strip()
    if message and _fold(out[0]) in ("ze", "zeby", "zebys", "aby", "abys", "by", "o"):
        first = {"ze": "że", "zeby": "żeby", "zebys": "żebyś", "abys": "abyś"}.get(_fold(out[0]), out[0].lower())
        message = " ".join([first] + out[1:])
    return ParsedReminder(message=message, due=due, level=level, when_text=when or f"na {level} poziomie", place=place)


_NOTE_TRIGGER = re.compile(r"^(?:(?:ej|hej|iskra|prosze|i)\s+)*(?:zanotuj|zapisz|zapamietaj|notatka|dodaj notatke)\w*"
                           r"(?:\s+(?:sobie|mi|to))*(?:\s+(?:ze|zeby|o))?\s*")


def parse_note(text: str, places=()) -> Optional[ParsedReminder]:
    """'zanotuj, że chcę wystawić miecz na AH' -> a note, tied to an auction house because it mentions one."""
    orig = re.findall(r"[\w']+", text or "")
    folded = " ".join(_fold(w) for w in orig)
    m = _NOTE_TRIGGER.match(folded)
    if not m:
        return None
    skip = len(folded[: m.end()].split())
    rest = orig[skip:]
    if not rest:
        return None
    message = " ".join(_SECOND_PERSON.get(w.lower(), w) for w in rest)
    place = parse_place(" ".join(rest), places)
    if not place and re.search(r"\b(wystaw\w*|sprzeda\w*)\b", " ".join(_fold(w) for w in rest)):
        place = "ah"
    when = {"ah": "przy domu aukcyjnym", "city": "w mieście"}.get(place, f"w {place}" if place else "")
    return ParsedReminder(message=message, when_text=when, place=place, keep=True)


def say_reminder(message: str, note: bool = False) -> str:
    """'Przypominam, żebyś...' / 'Przypominam: masz...' / 'Przypominam o...'; a note: 'Hej, pamiętasz?...'."""
    if note:
        return f"Hej, pamiętasz? Notowałeś: {message}." if message else "Masz tu coś zanotowane."
    if not message:
        return "Przypominam! Prosiłeś, żebym ci o czymś przypomniała."
    head = _fold(message.split()[0])
    if head in ("ze", "zeby", "zebys", "aby", "abys", "by"):
        return f"Przypominam, {message}."
    if head == "o":
        return f"Przypominam {message}."
    return f"Przypominam: {message}."


# ---------------------------------------------------------------- storage + timer
@dataclass
class Reminder:
    id: int
    message: str
    due: float = 0.0
    level: int = 0
    when_text: str = ""
    created: float = 0.0
    place: str = ""
    keep: bool = False  # notes stay until done
    last_fired: float = 0.0


class Reminders:
    def __init__(self, path: Optional[Path], on_fire: Callable[[Reminder], None], clock: Callable[[], float] = time.time):
        self.path = Path(path) if path else None
        self.on_fire = on_fire
        self.clock = clock
        self._lock = threading.Lock()
        self._items: list[Reminder] = []
        self._next_id = 1
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._load()

    def _load(self) -> None:
        if not self.path or not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self._items = [Reminder(**r) for r in data.get("reminders", [])]
            self._next_id = 1 + max((r.id for r in self._items), default=0)
        except (OSError, ValueError, TypeError) as e:
            log.warning("reminders: cannot read %s: %s", self.path, e)

    def _save(self) -> None:
        if not self.path:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"reminders": [asdict(r) for r in self._items]}, ensure_ascii=False, indent=1),
                           encoding="utf-8")
            tmp.replace(self.path)
        except OSError as e:
            log.warning("reminders: cannot write %s: %s", self.path, e)

    def add(self, parsed: ParsedReminder) -> Reminder:
        with self._lock:
            r = Reminder(self._next_id, parsed.message, parsed.due, parsed.level, parsed.when_text, self.clock(),
                         parsed.place, parsed.keep)
            self._next_id += 1
            self._items.append(r)
            self._save()
        return r

    def pending(self, notes: Optional[bool] = False) -> list[Reminder]:
        """Reminders (notes=False), notes (True) or everything (None)."""
        with self._lock:
            items = [r for r in self._items if notes is None or r.keep == notes]
            return sorted(items, key=lambda r: (r.level != 0, r.due or 0, r.level, r.created))

    def on_place(self, zone: str, subzone: str = "", now: Optional[float] = None) -> list[Reminder]:
        """Place reminders fire once; notes tied to a place at most every NOTE_COOLDOWN_S."""
        now = self.clock() if now is None else now
        with self._lock:
            due = [r for r in self._items if r.place and place_matches(r.place, zone, subzone)
                   and (not r.keep or not r.last_fired or now - r.last_fired >= NOTE_COOLDOWN_S)]
            for r in due:
                r.last_fired = now
            gone = [r for r in due if not r.keep]
            if due:
                self._items = [r for r in self._items if r not in gone]
                self._save()
        self._fire(due)
        return due

    def done(self, words: list[str]) -> list[Reminder]:
        """Remove notes mentioning any of the (folded) words; with no words, all notes."""
        stems = [w[:5] for w in words if len(w) >= 4]
        with self._lock:
            gone = [r for r in self._items if r.keep and (not stems or any(st in _fold(r.message) for st in stems))]
            if gone:
                self._items = [r for r in self._items if r not in gone]
                self._save()
        return gone

    def cancel(self, which: str = "all") -> list[Reminder]:
        """'all', 'last' (the newest) or 'next' (the one due first) - reminders only, notes stay."""
        with self._lock:
            mine = [r for r in self._items if not r.keep]
            if not mine:
                return []
            if which == "all":
                gone = mine
            else:
                pick = max(mine, key=lambda r: r.created) if which == "last" else \
                    min(mine, key=lambda r: (r.level != 0, r.due or 0, r.level))
                gone = [pick]
            self._items = [r for r in self._items if r not in gone]
            self._save()
        return gone

    def _fire(self, due: list[Reminder]) -> None:
        for r in due:
            try:
                self.on_fire(r)
            except Exception:
                log.exception("reminder callback failed")

    def tick(self, now: Optional[float] = None) -> list[Reminder]:
        now = self.clock() if now is None else now
        with self._lock:
            due = [r for r in self._items if r.due and r.due <= now]
            if due:
                self._items = [r for r in self._items if r not in due]
                self._save()
        self._fire(due)
        return due

    def on_level(self, level: int) -> list[Reminder]:
        with self._lock:
            due = [r for r in self._items if r.level and level >= r.level]
            if due:
                self._items = [r for r in self._items if r not in due]
                self._save()
        self._fire(due)
        return due

    def start(self) -> None:
        if self._thread is not None:
            return

        def run() -> None:
            while not self._stop.wait(1.0):
                self.tick()

        self._thread = threading.Thread(target=run, name="wvt-reminders", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()


def describe(r: Reminder, now: Optional[float] = None) -> str:
    now = time.time() if now is None else now
    if r.place or r.keep:
        return f"{r.when_text}: {r.message}" if r.when_text else r.message
    if r.level:
        when = f"na {r.level} poziomie"
    else:
        left = r.due - now
        when = _duration_text(left) if left < 3 * 3600 else datetime.fromtimestamp(r.due).strftime("o %H:%M")
    return f"{when}: {r.message}" if r.message else when
