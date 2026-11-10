"""What to do now ("co teraz robić?"): a short route through your quests, the order that walks least, what each
quest is worth and what to let go.

From what the addon reports (quest log with the game's own markers, position, level, class, group) and the
QuestieDB world data:

* **steps** - quests to turn in (the marker of a finished quest is where you hand it in; otherwise the NPC who
  takes it), objectives of open quests (the marker of the next objective), and quest givers near you with quests
  you can take (level, faction, class, completed quests - as :func:`wvt.advisor.where_next`);
* **worth** - every quest is assessed by :func:`wvt.quest_tips.assess`: its XP as a share of your level bar, its
  place in a chain and the XP to the chain's end, a reward at the end you can wear, a dungeon on the way, elites;
* **route** - greedy, nearest first from where you stand, a turn-in counting as nearer (it pays at once and may
  open the next quest of a chain) and a great quest a little nearer than an ordinary one;
* **let go** - quests the assessment calls a waste of time (grey, a few % of a level, nothing at the end), group
  quests while you are solo, and quests on other maps go to "later".

:class:`Plan` gives the facts for Iskra's LLM (Gemini plans it in words) and a plain answer when the LLM fails.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Optional

from . import protocol
from .advisor import spot_where, waypoint_line
from .kb.world import Player, World
from .quest_tips import Chains, assess
from .quests import QuestBook, Where, compass, spoken_objective

VERDICT_PL = {"great": "warto", "ok": "przeciętny, ale exp to exp", "skip": "szkoda czasu"}
# a turn-in pays at once and may open the next quest of a chain; a great quest is worth a few more steps
ROUTE_FACTOR = {"turnin": 0.6, "great": 0.8, "ok": 1.0, "pickup": 0.9}


@dataclass
class Step:
    kind: str  # "turnin" | "objective" | "pickup"
    title: str  # the quest, or the titles a giver has for you
    quest_ids: list[int] = field(default_factory=list)
    where: str = ""  # from where you stand: "około 120 jardów na wschód", "cel jest w innej strefie: Westfall"
    xy: Optional[tuple[float, float]] = None  # yards on your map, for the route
    line: str = ""  # /wvt track <id> | /wvt way <map> <x> <y> <label>
    verdict: str = ""  # great | ok | skip
    facts: list[str] = field(default_factory=list)
    left: str = ""  # what is left of the objectives
    who: str = ""  # who takes it / gives them
    group: int = 0
    hop: str = ""  # from the previous step of the route

    def label(self) -> str:
        if self.kind == "turnin":
            return f"oddaj {self.title}" + (f" u {self.who}" if self.who else "")
        if self.kind == "pickup":
            return f"weź u {self.who}: {self.title}"
        return self.title + (f" ({self.left})" if self.left else "")


@dataclass
class Plan:
    route: list[Step] = field(default_factory=list)
    later: list[Step] = field(default_factory=list)  # on other maps
    skip: list[tuple[Step, str]] = field(default_factory=list)  # let go, and why
    level: int = 0
    solo: bool = True
    at: float = field(default_factory=time.time)

    @property
    def empty(self) -> bool:
        return not (self.route or self.later)

    def text(self) -> str:
        """The plan in plain Polish (when the LLM is not there)."""
        parts = []
        for i, s in enumerate(self.route):
            lead = "najpierw" if i == 0 else "na koniec" if i == len(self.route) - 1 else "potem"
            where = s.where if i == 0 else (s.hop or s.where)
            parts.append(f"{lead} {s.label()}, {where}")
        out = (", ".join(parts) + ".") if parts else ""
        out = out[:1].upper() + out[1:]
        if self.skip:
            out += " " + " ".join(skip_sentence(s, why) for s, why in self.skip[:2])
        if self.later:
            out += " Na później: " + ", ".join(f"{s.title} ({s.where.replace('cel jest w innej strefie: ', '')})"
                                               for s in self.later[:2]) + "."
        return out.strip()

    def facts(self) -> str:
        """For the LLM: the route in order, what each step is worth, what to let go."""
        lines = [f"Gracz: poziom {self.level}, " + ("gra solo." if self.solo else "jest w grupie.")]
        if self.route:
            lines.append("Trasa (kolejność najkrótszego chodzenia, od miejsca, w którym stoi gracz):")
        for i, s in enumerate(self.route, 1):
            bit = f"{i}. {s.label()}; od gracza {s.where}" + (f", od poprzedniego punktu {s.hop}" if i > 1 and s.hop else "")
            if s.verdict:
                bit += f". Ocena: {VERDICT_PL[s.verdict]}"
            if s.facts:
                bit += ". " + " ".join(s.facts)
            lines.append(bit)
        for s, why in self.skip:
            lines.append(("Nie bierz" if s.kind == "pickup" else "Odpuść") + f": {s.title}"
                         + (f" od {s.who}" if s.kind == "pickup" and s.who else "") + f" – {why}.")
        for s in self.later:
            lines.append(f"Na później (inna mapa): {s.title} – {s.where}" + (f". Ocena: {VERDICT_PL[s.verdict]}"
                                                                            if s.verdict else "") + ".")
        return "\n".join(lines)


def skip_sentence(step: Step, why: str) -> str:
    """"Odpuść Old Errand: jest już szary." / "Nie bierz Mage Lesson od Gryan's Aide: mało expa…"."""
    if step.kind == "pickup":
        return f"Nie bierz {step.title}" + (f" od {step.who}" if step.who else "") + f": {why}."
    return f"Odpuść {step.title}: {why}."


def _here(pos) -> bool:
    return pos is not None and bool(getattr(pos, "width", 0)) and bool(getattr(pos, "height", 0))


def _quest_xy(q: protocol.QuestEntry, pos) -> Optional[tuple[float, float]]:
    if not _here(pos) or not q.has_poi or q.flags & protocol.QFLAG_POI_OTHER_MAP or q.poi_map != pos.map_id:
        return None
    return q.poi_x * pos.width, q.poi_y * pos.height


def _spot_xy(spot, pos) -> Optional[tuple[float, float]]:
    if spot is None or not _here(pos) or spot.ui_map != pos.map_id:
        return None
    return spot.x / 100 * pos.width, spot.y / 100 * pos.height


def _hop(a: tuple[float, float], b: tuple[float, float]) -> str:
    return Where(math.dist(a, b), compass(b[0] - a[0], b[1] - a[1])).polish()


def _share(share: float) -> str:
    return "poniżej 1% poziomu" if share * 100 < 0.5 else f"tylko {round(share * 100)}% poziomu"


def _why_skip(facts: list[str], share: float) -> str:
    if any("szary" in f for f in facts):
        return f"jest już szary, {_share(share)}" if share else "jest już szary"
    if share:
        return f"{_share(share)} i nic ciekawego na końcu"
    return "mało expa i nic ciekawego na końcu"


def make_plan(world: Optional[World], book: QuestBook, player: Player, pos=None, xp_max: int = 0,
              in_group: bool = False, chains: Optional[Chains] = None, max_steps: int = 5,
              pickup_radius: float = 900.0) -> Plan:
    plan = Plan(level=player.level, solo=not in_group)
    if world is not None and chains is None:
        chains = Chains(world)
    quests = book.quests() if book.known else []
    candidates: list[Step] = []
    for q in quests:
        if q.failed:
            continue
        a = assess(world, chains, q.quest_id, player.level, xp_max, player.class_id, q.group) \
            if world is not None else None
        w = book.where(q)
        step = Step("turnin" if q.complete else "objective", q.title, [q.quest_id], w.polish() if w else "",
                    _quest_xy(q, pos), f"/wvt track {q.quest_id}", a.verdict if a else "",
                    list(a.facts) if a else [], group=q.group)
        if q.complete:
            spot = world.finisher_spot(q.quest_id, pos) if world is not None else None
            if spot is not None:
                step.who = spot.name
                if step.xy is None:  # no marker: the NPC who takes it
                    step.xy, step.where = _spot_xy(spot, pos), spot_where(world, spot, pos)
                    step.line = waypoint_line(spot, f"{q.title}: {spot.name}")
        else:
            step.left = ", ".join(spoken_objective(t) for done, t in q.objectives if not done)
            if a is not None and a.verdict == "skip":
                plan.skip.append((step, _why_skip(a.facts, a.level_share)))
                continue
            if q.group > 1 and not in_group:
                plan.skip.append((step, f"to quest na grupę {q.group} osób, a grasz solo"))
                continue
        candidates.append(step)

    if world is not None and player.done_known and player.level and _here(pos):
        candidates += _pickups(world, chains, book, player, pos, xp_max, plan, pickup_radius)

    here = [s for s in candidates if s.xy is not None]
    plan.later = [s for s in candidates if s.xy is None][:3]
    cur = (pos.x * pos.width, pos.y * pos.height) if _here(pos) else None
    while here and cur is not None and len(plan.route) < max_steps:
        def cost(s: Step) -> float:
            factor = ROUTE_FACTOR["turnin" if s.kind == "turnin" else "pickup" if s.kind == "pickup" else
                                  "great" if s.verdict == "great" else "ok"]
            return math.dist(cur, s.xy) * factor
        nxt = min(here, key=cost)
        here.remove(nxt)
        if plan.route:
            nxt.hop = _hop(cur, nxt.xy)
        plan.route.append(nxt)
        cur = nxt.xy
    plan.skip = plan.skip[:3]
    return plan


def _pickups(world: World, chains: Optional[Chains], book: QuestBook, player: Player, pos, xp_max: int, plan: Plan,
             radius: float) -> list[Step]:
    """Quest givers near you with quests worth taking (the others go to "let go")."""
    active = {q.quest_id for q in book.quests()} if book.known else set()
    groups: dict[tuple[str, int], list] = {}
    for qid, q in world.quests.items():
        lvl = q.get("lvl") or q.get("min") or 0
        if lvl and not (player.level - 4 <= lvl <= player.level + 2):
            continue
        if not world.available(q, qid, player, active):
            continue
        spot = world.starter_spot(qid, pos, player.faction)
        if spot is None or spot.distance is None or spot.distance > radius:
            continue
        groups.setdefault((spot.kind, spot.eid), []).append((qid, q, spot))
    out = []
    for group in sorted(groups.values(), key=lambda g: g[0][2].distance / (1 + 0.4 * (len(g) - 1)))[:3]:
        spot = group[0][2]
        good, facts = [], []
        for qid, q, _ in group:
            a = assess(world, chains, qid, player.level, xp_max, player.class_id) if chains is not None else None
            if a is not None and a.verdict == "skip":
                plan.skip.append((Step("pickup", q["n"], [qid], who=spot.name), _why_skip(a.facts, a.level_share)))
                continue
            good.append((qid, q["n"], a))
            if a is not None and a.facts:
                facts.append(f"{q['n']}: " + " ".join(a.facts[:2]))
        if not good:
            continue
        verdict = "great" if any(a and a.verdict == "great" for _, _, a in good) else "ok"
        out.append(Step("pickup", ", ".join(t for _, t, _ in good[:3]), [qid for qid, _, _ in good],
                        spot_where(world, spot, pos), _spot_xy(spot, pos), waypoint_line(spot, spot.name), verdict,
                        facts[:3], who=spot.name))
    return out
