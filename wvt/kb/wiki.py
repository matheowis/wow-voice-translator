"""Warcraft Wiki (warcraft.wiki.gg, CC BY-SA 4.0) pages for the knowledge base.

Pages come from the MediaWiki API (``action=parse``: rendered HTML, one page per request, one
request per second, generic User-Agent) and are cached in ``data/kb/raw/wiki`` so a rebuild does
not download them again. :func:`html_to_sections` turns the HTML into plain text split by
headings (tables become ``cell | cell`` rows, so infoboxes and boss lists survive).

What is fetched (:func:`plan_titles`): the WoW Forever hub pages and what they link to (new
zones, dungeons, races, classes, professions, systems), the "(Classic)" / "(Forever)" versions of
the classic zones, dungeons and raids (the plain titles describe today's retail game), class and
profession pages, and one more hop from the Forever dungeon/raid guides to their bosses.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import quote

from . import RAW_DIR
from .net import PoliteClient

log = logging.getLogger(__name__)

API = "https://warcraft.wiki.gg/api.php"
PAGE_URL = "https://warcraft.wiki.gg/wiki/"
LICENSE = "CC BY-SA 4.0 (Warcraft Wiki)"

HUBS = [
    "Forever", "World of Warcraft: Forever", "Adventure Guide Forever dungeons", "Adventure Guide Forever raids",
    "Forever map comparison", "Zephras Isle", "Riverglades", "Shen'dralas", "Mount Hyjal (Forever)",
    "Skyborne", "Skyborne (playable)", "Barrens (Forever)", "Redridge Mountains (Forever)", "Zephras Isle storyline",
    "Mulgore (Forever)", "Alterac Mountains (Forever)", "Adventure Guide Classic dungeons", "Adventure Guide Classic raids",
]
# hubs whose links are followed (Forever-specific pages; "Skyborne" or the map comparison would pull in all lore)
LINK_HUBS = [
    "Forever", "World of Warcraft: Forever", "Adventure Guide Forever dungeons", "Adventure Guide Forever raids",
    "Zephras Isle", "Riverglades", "Shen'dralas", "Mount Hyjal (Forever)", "Skyborne (playable)",
    "Zephras Isle storyline", "Barrens (Forever)", "Redridge Mountains (Forever)", "Mulgore (Forever)",
    "Alterac Mountains (Forever)",
]
# second hop (bosses, NPCs of the new instances)
DEEP_HUBS = ["Adventure Guide Forever dungeons", "Adventure Guide Forever raids"]
# a followed link is kept only if its wiki categories mark it as Forever / Classic game content
KEEP_CATEGORY = re.compile(r"(Forever|\bClassic\b|Added in patch 1\.|Racial traits|Starting areas|instances|"
                           r"World bosses|Dungeon|Raid|Bosses)", re.I)
DROP_CATEGORY = re.compile(r"(Testing-only|Decor:|Store decor|Warcraft: Orcs|Warcraft II|Hearthstone)", re.I)

CLASSIC_ZONES = [
    "Elwynn Forest", "Westfall", "Redridge Mountains", "Duskwood", "Stranglethorn Vale", "Dun Morogh", "Loch Modan",
    "Wetlands", "Arathi Highlands", "Hillsbrad Foothills", "Alterac Mountains", "Silverpine Forest", "Tirisfal Glades",
    "The Hinterlands", "Western Plaguelands", "Eastern Plaguelands", "Searing Gorge", "Burning Steppes", "Badlands",
    "Swamp of Sorrows", "Blasted Lands", "Deadwind Pass", "Teldrassil", "Darkshore", "Ashenvale",
    "Stonetalon Mountains", "Durotar", "Mulgore", "Barrens", "Thousand Needles", "Desolace", "Dustwallow Marsh",
    "Feralas", "Tanaris", "Un'Goro Crater", "Azshara", "Felwood", "Winterspring", "Silithus", "Moonglade",
    "Stormwind City", "Ironforge", "Darnassus", "Orgrimmar", "Thunder Bluff", "Undercity", "Booty Bay", "Gadgetzan",
    "Ratchet", "Everlook", "Blackrock Mountain", "Alterac Valley", "Warsong Gulch", "Arathi Basin",
]
CLASSIC_INSTANCES = [
    "Ragefire Chasm", "Wailing Caverns", "Deadmines", "Shadowfang Keep", "Blackfathom Deeps", "Stormwind Stockade",
    "Gnomeregan", "Razorfen Kraul", "Scarlet Monastery", "Razorfen Downs", "Uldaman", "Zul'Farrak", "Maraudon",
    "Temple of Atal'Hakkar", "Blackrock Depths", "Lower Blackrock Spire", "Upper Blackrock Spire", "Dire Maul",
    "Stratholme", "Scholomance", "Molten Core", "Onyxia's Lair", "Blackwing Lair", "Zul'Gurub",
    "Ruins of Ahn'Qiraj", "Temple of Ahn'Qiraj", "Naxxramas",
]
GENERAL = [
    "Rested", "Hearthstone", "Flight path", "Elite", "Weapon skill", "Class trainer",
    "Alliance", "Horde", "Night elf", "Human", "Dwarf", "Gnome", "Orc", "Tauren", "Troll", "Forsaken",
]
# retail pages that would mislead (today's classes, talents and professions); the Forever pages
# ("Mage abilities (Forever)", "Forever alchemy recipes"...) cover the same ground for this game
RETAIL_PAGES = {
    "Warrior", "Paladin", "Hunter", "Rogue", "Priest", "Shaman", "Mage", "Warlock", "Druid", "Alchemy",
    "Blacksmithing", "Enchanting", "Engineering", "Herbalism", "Leatherworking", "Mining", "Skinning", "Tailoring",
    "Cooking", "First Aid", "Fishing", "Riding", "Reputation", "Talent", "Dungeon", "Raid", "Quest", "Mount",
}
VARIANTS = ("{t} (Forever)", "{t} (Classic)", "{t} (instance)", "{t}")

SKIP_TITLE = re.compile(
    r"^(World of Warcraft: (?!Forever$)|Warcraft|Hearthstone \(game\)|Heroes of the Storm|Diablo|Blizzard|BlizzCon|"
    r"Monopoly|Peggle|GameVaults|Small World|Escape from|Untitled|Avalon|Andromeda|Orbis|Neptune|MMORPG|Massively|"
    r"Timeline|Retail$|Classic$|ADP$|Editions$|Game time|Account$|Realm$|RP|PvE$|PvP$|Patch |Season of|"
    r"Housing|Pachimari|Zergling|Panda Collar|Infernal \(beta\)|The Next |W!Zone|10-day|14-day|Collections$|"
    r"Character$|Roadmap$|Team 2|Blizzard Anthology|BestSeller)"
    r"|(Collector's Edition|Edition$|Battle Chest|Pack$|Bundle$|Board Game|Trading Card|Trivial Pursuit|Free Trial|"
    r"Upgrade$|Ensemble:|Tabard|Figurine|Escape Room|Miniatures|\(film\)|\(cinematic\)|\(canceled game\)|Package$|"
    r"Starter Edition|Adventure Game|Shareware|Demo$|Sampler$)"
)
MAX_PAGES = 900


def _cache_path(raw_dir: Path, title: str) -> Path:
    h = hashlib.sha1(title.encode("utf-8")).hexdigest()[:16]
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", title)[:60]
    return raw_dir / "wiki" / f"{safe}.{h}.json"


class Wiki:
    def __init__(self, client: PoliteClient, raw_dir: Path = RAW_DIR):
        self.c = client
        self.raw_dir = raw_dir

    def _api(self, **params) -> dict:
        params.update(format="json", formatversion=2, maxlag=5)
        return self.c.get(API, params=params).json()

    def resolve(self, titles: list[str]) -> dict[str, str | None]:
        """title -> canonical existing title (redirects followed) or None, 50 per request."""
        out: dict[str, str | None] = {}
        for i in range(0, len(titles), 50):
            batch = titles[i:i + 50]
            d = self._api(action="query", titles="|".join(batch), redirects=1).get("query", {})
            norm = {x["from"]: x["to"] for x in d.get("normalized", [])}
            redir = {x["from"]: x["to"] for x in d.get("redirects", [])}
            exists = {p["title"] for p in d.get("pages", []) if not p.get("missing") and not p.get("invalid")}
            for t in batch:
                t2 = norm.get(t, t)
                t3 = redir.get(t2, t2)
                out[t] = t3 if t3 in exists else None
        return out

    def links(self, title: str) -> list[str]:
        d = self._api(action="parse", page=title, prop="links", redirects=1).get("parse", {})
        return [x["title"] for x in d.get("links", []) if x.get("ns") == 0 and x.get("exists")]

    def categories(self, titles: list[str]) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for i in range(0, len(titles), 50):
            batch = titles[i:i + 50]
            d = self._api(action="query", prop="categories", titles="|".join(batch), cllimit="max", redirects=1)
            for p in d.get("query", {}).get("pages", []):
                out[p["title"]] = [c["title"].split(":", 1)[-1] for c in p.get("categories", [])]
        return out

    def page(self, title: str, refresh: bool = False) -> dict | None:
        """Rendered page (cached): {title, revid, html, categories, url}."""
        path = _cache_path(self.raw_dir, title)
        if path.exists() and not refresh:
            return json.loads(path.read_text(encoding="utf-8"))
        d = self._api(action="parse", page=title, prop="text|categories|revid", redirects=1,
                      disableeditsection=1, disablelimitreport=1, disabletoc=1)
        p = d.get("parse")
        if not p:
            return None
        rec = {"title": p["title"], "revid": p.get("revid"), "html": p.get("text", ""),
               "categories": [c.get("category", "") for c in p.get("categories", []) if not c.get("hidden")],
               "url": PAGE_URL + quote(p["title"].replace(" ", "_")), "fetched": int(time.time())}
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(rec, ensure_ascii=False), encoding="utf-8")
        return rec

    def plan_titles(self, extra: list[str] | None = None) -> list[str]:
        """Every page title the knowledge base wants (existing, canonical, de-duplicated)."""
        wanted: list[str] = []
        seen: set[str] = set()

        def add(t: str | None) -> None:
            if t and t not in seen and t not in RETAIL_PAGES and not SKIP_TITLE.search(t):
                seen.add(t)
                wanted.append(t)

        for t, c in self.resolve(HUBS + list(extra or [])).items():
            add(c)
        # classic zones / instances: prefer the Forever, then the Classic version of a page
        variants = [v.format(t=t) for t in CLASSIC_ZONES + CLASSIC_INSTANCES for v in VARIANTS]
        resolved = self.resolve(variants)
        for t in CLASSIC_ZONES + CLASSIC_INSTANCES:
            for v in VARIANTS:
                c = resolved.get(v.format(t=t))
                if c:
                    add(c)
                    break
        for t, c in self.resolve(GENERAL).items():
            add(c)
        followed: list[str] = []
        for hub in LINK_HUBS:
            try:
                followed += self.links(hub)
            except Exception as e:  # noqa: BLE001 - one hub failing must not stop the build
                log.warning("linki %s: %s", hub, e)
        for hub in DEEP_HUBS:
            try:
                for t in self.links(hub):
                    if not SKIP_TITLE.search(t):
                        followed += self.links(t)[:80]
            except Exception as e:  # noqa: BLE001
                log.warning("linki %s: %s", hub, e)
        followed = [t for t in dict.fromkeys(followed) if t not in seen and not SKIP_TITLE.search(t)]
        for t, cats in self.categories(followed).items():
            if any(KEEP_CATEGORY.search(c) for c in cats) and not any(DROP_CATEGORY.search(c) for c in cats):
                add(t)
        return wanted[:MAX_PAGES]

    def fetch_all(self, titles: list[str], refresh: bool = False, progress=None) -> list[dict]:
        pages = []
        for i, t in enumerate(titles, 1):
            try:
                p = self.page(t, refresh=refresh)
            except Exception as e:  # noqa: BLE001
                log.warning("strona %s: %s", t, e)
                continue
            if p:
                pages.append(p)
            if progress:
                progress(i, len(titles), t)
        return pages


def cached_pages(raw_dir: Path = RAW_DIR) -> list[dict]:
    out = []
    for path in sorted((raw_dir / "wiki").glob("*.json")):
        try:
            out.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    return out


# --------------------------------------------------------------------------- HTML -> text

_SKIP_CLASS = {
    "navbox", "mw-editsection", "reference", "references", "mw-references-wrap", "reflist", "toc", "noprint",
    "mw-empty-elt", "navigation-not-searchable", "metadata", "ambox", "mbox", "hatnote", "dablink", "gallery",
    "thumb", "printfooter", "catlinks", "sister", "mw-cite-backlink", "audio", "nomobile", "error", "plainlinks-print",
    "nav", "navbox-styles", "tooltip-content", "screenshot", "mw-collapsible-toggle",
}
_SKIP_TAG = {"script", "style", "sup", "noscript", "img", "figure", "audio", "video", "svg", "math", "button", "map"}
_VOID = {"br", "img", "hr", "input", "meta", "link", "wbr", "area", "base", "col", "source", "track", "param"}
_BLOCK = {"p", "div", "ul", "ol", "dl", "table", "tbody", "thead", "blockquote", "section", "center", "pre", "caption"}
_HEAD = {"h1": 1, "h2": 2, "h3": 3, "h4": 4, "h5": 5, "h6": 6}
_STOP_SECTIONS = re.compile(r"^(references|external links|see also|notes and references|gallery|videos|images|"
                            r"patch changes|patches and hotfixes|media|navigation)$", re.I)


class _Html(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.sections: list[tuple[str, str]] = []
        self.path: list[tuple[int, str]] = []
        self.buf: list[str] = []
        self.skip_depth = 0
        self.stack: list[tuple[str, bool]] = []  # (tag, opened a skip)
        self.in_head: int | None = None
        self.head_text: list[str] = []
        self.cell_index = 0
        self.stopped = False

    def _flush(self) -> None:
        text = "".join(self.buf)
        self.buf = []
        lines = [re.sub(r"[ \t ]+", " ", ln).strip(" |") for ln in text.split("\n")]
        lines = [ln for ln in lines if ln and ln not in ("-", "(lore)", "Edit", "edit")]
        if lines and not self.stopped:
            self.sections.append((" > ".join(h for _, h in self.path), "\n".join(lines)))

    def handle_starttag(self, tag, attrs):
        if tag in _VOID:
            if tag == "br" and not self.skip_depth:
                self.buf.append("\n")
            return
        a = dict(attrs)
        classes = set((a.get("class") or "").split())
        skip = tag in _SKIP_TAG or bool(classes & _SKIP_CLASS) or a.get("role") == "navigation" \
            or "display:none" in (a.get("style") or "").replace(" ", "")
        self.stack.append((tag, skip))
        if skip:
            self.skip_depth += 1
            return
        if self.skip_depth:
            return
        if tag in _HEAD:
            self._flush()
            self.in_head = _HEAD[tag]
            self.head_text = []
        elif tag in _BLOCK:
            self.buf.append("\n")
        elif tag == "li":
            self.buf.append("\n- ")
        elif tag in ("dt", "dd"):
            self.buf.append("\n")
        elif tag == "tr":
            self.buf.append("\n")
            self.cell_index = 0
        elif tag in ("td", "th"):
            if self.cell_index:
                self.buf.append(" | ")
            self.cell_index += 1

    def handle_endtag(self, tag):
        if tag in _VOID:
            return
        # pop to the matching tag (HTML from MediaWiki is well formed, but be forgiving)
        while self.stack:
            t, skip = self.stack.pop()
            if skip:
                self.skip_depth -= 1
            if t == tag:
                break
        if self.skip_depth:
            return
        if tag in _HEAD and self.in_head is not None:
            level = self.in_head
            title = re.sub(r"\s+", " ", "".join(self.head_text)).strip()
            self.in_head = None
            if level >= 2 and title:
                self.path = [(lv, h) for lv, h in self.path if lv < level] + [(level, title)]
                self.stopped = bool(_STOP_SECTIONS.match(title)) or any(_STOP_SECTIONS.match(h) for _, h in self.path)
        elif tag in _BLOCK or tag in ("li", "tr", "dd", "dt"):
            self.buf.append("\n")

    def handle_data(self, data):
        if self.skip_depth:
            return
        data = data.replace("\r", " ").replace("\n", " ")  # source newlines are plain whitespace in HTML
        if self.in_head is not None:
            self.head_text.append(data)
        else:
            self.buf.append(data)

    def close(self):
        super().close()
        self._flush()


def html_to_sections(html: str) -> list[tuple[str, str]]:
    """[(section path, text)] in page order; the lead section has an empty path."""
    p = _Html()
    p.feed(html or "")
    p.close()
    merged: list[tuple[str, str]] = []
    for path, text in p.sections:
        if merged and merged[-1][0] == path:
            merged[-1] = (path, merged[-1][1] + "\n" + text)
        else:
            merged.append((path, text))
    return merged
