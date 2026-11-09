"""Documents for the knowledge base and how they are cut into chunks.

A document is ``{"source", "title", "url", "license", "kind", "sections": [(path, text)]}``.
Sources: Warcraft Wiki pages (:mod:`wvt.kb.wiki`), one text per quest and per-zone service lists
generated from :mod:`wvt.kb.world`, and your own notes in ``data/kb/guides`` (.md / .txt).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable, Iterator

from .wiki import LICENSE as WIKI_LICENSE, html_to_sections
from .world import NPC_FLAGS, World, faction_of_mask

TARGET_CHARS = 700
MAX_CHARS = 1000
MIN_SECTION_CHARS = 40
MAX_CHUNKS_PER_DOC = 80

_SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(])")


def _split_long(line: str) -> list[str]:
    if len(line) <= MAX_CHARS:
        return [line]
    parts, cur = [], ""
    for s in _SENTENCE.split(line):
        if cur and len(cur) + len(s) + 1 > TARGET_CHARS:
            parts.append(cur)
            cur = s
        else:
            cur = f"{cur} {s}".strip()
    if cur:
        parts.append(cur)
    out = []
    for p in parts:  # a "sentence" without full stops (a huge table row) is cut hard
        out += [p[i:i + MAX_CHARS] for i in range(0, len(p), MAX_CHARS)]
    return out


def chunk_sections(sections: list[tuple[str, str]]) -> Iterator[tuple[str, str]]:
    """(section path, chunk text) pieces of about TARGET_CHARS, never across a section boundary."""
    for path, text in sections:
        if len(text) < MIN_SECTION_CHARS and path:
            continue
        cur: list[str] = []
        size = 0
        for raw in text.split("\n"):
            for line in _split_long(raw.strip()):
                if not line:
                    continue
                if cur and size + len(line) + 1 > TARGET_CHARS:
                    yield path, "\n".join(cur)
                    cur, size = [], 0
                cur.append(line)
                size += len(line) + 1
        if cur:
            yield path, "\n".join(cur)


# --------------------------------------------------------------------------- sources

# quest dialogue and reward lists: no help for questions, and they attract small talk ("how are you")
DROP_SECTIONS = {"progress", "completion", "gains", "rewards", "quotes", "dialogue", "gallery", "videos", "images"}


def wiki_docs(pages: Iterable[dict]) -> Iterator[dict]:
    for p in pages:
        sections = [(path, text) for path, text in html_to_sections(p.get("html", ""))
                    if path.split(" > ")[0].strip().lower() not in DROP_SECTIONS]
        if not sections:
            continue
        cats = [c.replace("_", " ") for c in p.get("categories", [])]
        kind = "forever" if any("Forever" in c for c in cats) or "(Forever)" in p["title"] else "wiki"
        yield {"source": "wiki", "title": p["title"], "url": p.get("url", ""), "license": WIKI_LICENSE,
               "kind": kind, "sections": sections}


_LEVELS = re.compile(r"^(Classic lvl|Level) \| (\d{1,2})\s*(?:-|–| - )\s*(\d{1,2})\s*$", re.M)
_ONE_LEVEL = re.compile(r"^Level \| (\d{1,2})\s*$", re.M)


def extract_instances(pages: Iterable[dict]) -> list[dict]:
    """Dungeons and raids with their level range from the wiki infoboxes ("Classic lvl" wins over the
    retail "Level"; plain retail pages without a Classic level are skipped, their levels are scaled)."""
    out: dict[str, dict] = {}
    for p in pages:
        sections = html_to_sections(p.get("html", ""))
        if not sections or sections[0][0]:
            continue
        lead = sections[0][1]
        m = re.search(r"^Type \| (Dungeon|Raid)\s*$", lead, re.M)
        if not m:
            continue
        title = p["title"]
        cats = " ".join(p.get("categories", []))
        game_page = bool(re.search(r"\((Classic|Forever|instance)\)$", title)) or "Forever" in cats or "1.60" in cats
        ranges = {k: (int(a), int(b)) for k, a, b in _LEVELS.findall(lead)}
        lo_hi = ranges.get("Classic lvl") or (ranges.get("Level") if game_page else None)
        if lo_hi is None and game_page:
            one = _ONE_LEVEL.search(lead)
            if one:
                lo_hi = (int(one.group(1)), int(one.group(1)))
        if lo_hi is None or "Location |" not in lead:
            continue
        loc = re.search(r"^Location \| (.+)$", lead, re.M)
        name = re.sub(r"\s*\((Classic|instance|Forever)\)$", "", title)
        rec = {"name": name, "type": m.group(1), "lo": lo_hi[0], "hi": lo_hi[1],
               "location": loc.group(1).strip() if loc else "", "page": title}
        if name not in out or title.endswith("(Classic)") or title.endswith("(instance)"):
            out[name] = rec
    return sorted(out.values(), key=lambda d: (d["lo"], d["name"]))


def _where(world: World, spot) -> str:
    if spot is None:
        return ""
    zone = world.area_name(spot.area)
    return f"{zone} ({spot.x:.0f}, {spot.y:.0f})" if zone else f"({spot.x:.0f}, {spot.y:.0f})"


def quest_text(world: World, qid: int) -> str:
    q = world.quests[qid]
    lines = []
    lvl = f"level {q['lvl']}" if q.get("lvl") else "level ?"
    if q.get("min"):
        lvl += f", available from level {q['min']}"
    side = faction_of_mask(q.get("races", 0))
    side_txt = {"A": "Alliance", "H": "Horde"}.get(side, "both factions")
    zone = world.area_name(q["zone"]) if q.get("zone", 0) > 0 else ""
    lines.append(f"Quest \"{q['n']}\" ({lvl}; {side_txt}{'; ' + zone if zone else ''}).")
    starters = [world.npcs[n]["n"] for n in q.get("sn", []) if n in world.npcs]
    starters += [world.objects[o]["n"] for o in q.get("so", []) if o in world.objects]
    starters += [world.items[i]["n"] + " (item)" for i in q.get("si", []) if i in world.items]
    if starters:
        lines.append(f"Starts at: {', '.join(starters[:3])} - {_where(world, world.starter_spot(qid))}.")
    enders = [world.npcs[n]["n"] for n in q.get("en", []) if n in world.npcs]
    enders += [world.objects[o]["n"] for o in q.get("eo", []) if o in world.objects]
    if enders:
        lines.append(f"Turn in to: {', '.join(enders[:3])} - {_where(world, world.finisher_spot(qid))}.")
    if q.get("txt"):
        lines.append("Objectives: " + " ".join(q["txt"])[:400])
    kills = [world.npcs[n]["n"] for n, _ in q.get("kill", []) if n in world.npcs]
    if kills:
        where = _where(world, world.nearest("npc", q["kill"][0][0]))
        lines.append(f"Kill: {', '.join(kills[:4])}" + (f" - {where}" if where else "") + ".")
    for iid, _ in q.get("loot", [])[:3]:
        it = world.items.get(iid)
        if not it:
            continue
        srcs = [world.npcs[n]["n"] for n in it.get("nd", [])[:3] if n in world.npcs]
        srcs += [world.objects[o]["n"] for o in it.get("od", [])[:3] if o in world.objects]
        lines.append(f"Collect {it['n']}" + (f", from: {', '.join(srcs)}" if srcs else "") + ".")
    pre = [world.quests[p]["n"] for p in q.get("pg", []) + q.get("ps", []) if p in world.quests]
    if pre:
        lines.append(f"Requires first: {', '.join(pre[:4])}.")
    if q.get("next") in world.quests:
        lines.append(f"Followed by: {world.quests[q['next']]['n']}.")
    if q.get("xp"):
        lines.append(f"Reward: {q['xp']} XP.")
    return "\n".join(lines)


def _where_pl(world: World, spot) -> str:
    if spot is None:
        return ""
    zone = world.area_name(spot.area)
    return f"{zone} ({spot.x:.0f}, {spot.y:.0f})" if zone else f"({spot.x:.0f}, {spot.y:.0f})"


def quest_text_pl(world: World, qid: int, pl: dict) -> str:
    """The same facts in Polish; WoW names stay English, the title gets a Polish gloss for searching."""
    from .translate_pl import sha

    q = world.quests[qid]
    gloss = pl.get("t:" + sha(q["n"])) or ""
    head = f"Quest „{q['n']}”" + (f" (po polsku: {gloss})" if gloss and gloss.lower() != q["n"].lower() else "")
    lvl = f"poziom {q['lvl']}" if q.get("lvl") else "poziom ?"
    if q.get("min"):
        lvl += f", dostępny od {q['min']}"
    side = {"A": "Przymierze", "H": "Horda"}.get(faction_of_mask(q.get("races", 0)), "obie frakcje")
    zone = world.area_name(q["zone"]) if q.get("zone", 0) > 0 else ""
    lines = [f"{head} – {lvl}; {side}{'; ' + zone if zone else ''}."]
    starters = [world.npcs[n]["n"] for n in q.get("sn", []) if n in world.npcs]
    starters += [world.objects[o]["n"] for o in q.get("so", []) if o in world.objects]
    starters += [world.items[i]["n"] + " (przedmiot)" for i in q.get("si", []) if i in world.items]
    if starters:
        lines.append(f"Zaczyna się u: {', '.join(starters[:3])} – {_where_pl(world, world.starter_spot(qid))}.")
    enders = [world.npcs[n]["n"] for n in q.get("en", []) if n in world.npcs]
    enders += [world.objects[o]["n"] for o in q.get("eo", []) if o in world.objects]
    if enders:
        lines.append(f"Oddajesz u: {', '.join(enders[:3])} – {_where_pl(world, world.finisher_spot(qid))}.")
    if q.get("txt"):
        text = " ".join(q["txt"])[:600]
        lines.append("Cel: " + (pl.get("o:" + sha(text)) or text))
    kills = [world.npcs[n]["n"] for n, _ in q.get("kill", []) if n in world.npcs]
    if kills:
        where = _where_pl(world, world.nearest("npc", q["kill"][0][0]))
        lines.append(f"Zabij: {', '.join(kills[:4])}" + (f" – {where}" if where else "") + ".")
    for iid, _ in q.get("loot", [])[:3]:
        it = world.items.get(iid)
        if not it:
            continue
        srcs = [world.npcs[n]["n"] for n in it.get("nd", [])[:3] if n in world.npcs]
        srcs += [world.objects[o]["n"] for o in it.get("od", [])[:3] if o in world.objects]
        lines.append(f"Zbierz {it['n']}" + (f", wypada z: {', '.join(srcs)}" if srcs else "") + ".")
    pre = [world.quests[p]["n"] for p in q.get("pg", []) + q.get("ps", []) if p in world.quests]
    if pre:
        lines.append(f"Najpierw zrób: {', '.join(pre[:4])}.")
    if q.get("next") in world.quests:
        lines.append(f"Dalej w łańcuchu: {world.quests[q['next']]['n']}.")
    rewards = [world.items[i]["n"] for i in q.get("rw", []) if i in world.items]
    if rewards:
        lines.append(f"Nagrody do wyboru: {', '.join(rewards[:4])}.")
    if q.get("xp"):
        lines.append(f"Doświadczenie: {q['xp']} XP.")
    return "\n".join(lines)


def quest_docs(world: World, pl: dict | None = None) -> Iterator[dict]:
    for qid in sorted(world.quests):
        sections = [("", quest_text(world, qid))]
        if pl:
            sections.append(("po polsku", quest_text_pl(world, qid, pl)))
        yield {"source": "questie", "title": world.quests[qid]["n"], "url": "", "kind": "quest",
               "license": "QuestieDB (personal use)", "ref": qid, "sections": sections}


_SERVICES = [("FLIGHT_MASTER", "Flight master"), ("INNKEEPER", "Innkeeper"), ("BANKER", "Banker"),
             ("AUCTIONEER", "Auctioneer"), ("STABLEMASTER", "Stable master"), ("SPIRIT_HEALER", "Spirit healer"),
             ("TRAINER", "Trainer")]


def service_docs(world: World) -> Iterator[dict]:
    by_zone: dict[int, list[str]] = {}
    for nid, n in world.npcs.items():
        flags = n.get("fl", 0)
        label = next((lbl for key, lbl in _SERVICES if flags & NPC_FLAGS[key]), None)
        if not label:
            continue
        sub = n.get("sub", "")
        if label == "Trainer" and sub:
            label = sub
        side = {"A": "Alliance", "H": "Horde", "AH": "both factions"}.get(n.get("f", ""), "")
        for area_s, pts in n.get("sp", {}).items():
            x, y = pts[0]
            by_zone.setdefault(int(area_s), []).append(
                f"{label}: {n['n']} ({x:.0f}, {y:.0f}){' - ' + side if side else ''}")
    for area, lines in sorted(by_zone.items()):
        name = world.area_name(area)
        if not name:
            continue
        yield {"source": "questie", "title": f"{name} services", "url": "", "kind": "services",
               "license": "QuestieDB (personal use)", "sections": [("", "\n".join(sorted(set(lines))))]}


def guide_docs(folder: Path) -> Iterator[dict]:
    if not folder.exists():
        return
    for path in sorted(folder.rglob("*")):
        if path.suffix.lower() not in (".md", ".txt") or not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        sections: list[tuple[str, str]] = []
        head, buf = "", []
        for line in text.splitlines():
            m = re.match(r"^\s{0,3}#{1,6}\s+(.*)$", line)
            if m:
                if buf:
                    sections.append((head, "\n".join(buf)))
                head, buf = m.group(1).strip(), []
            else:
                buf.append(line)
        if buf:
            sections.append((head, "\n".join(buf)))
        yield {"source": "guide", "title": path.stem.replace("_", " "), "url": str(path), "kind": "guide",
               "license": "own", "sections": [(h, t.strip()) for h, t in sections if t.strip()]}
