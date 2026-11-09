"""Quest chains and whether a quest is worth it - what the assistant says when you take a quest.

From the QuestieDB world data: the chain the quest belongs to (``nextQuestInChain`` forward, the
quest whose "next" it is - or its single prerequisite - backward), XP of the quest and of the rest of
the chain (as a share of your current level bar, reduced like the game does for low quests), the
reward items at the end of the chain (and whether your class can use them), a dungeon on the way,
breadcrumbs, group quests and elite targets.  The verdict: "great", "ok" or "skip".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .kb.world import World
from .loot import ARMOR_CLASS, WEAPON_CLASS, usable
from .protocol import LootItem


@dataclass
class Chain:
    ids: list[int]
    pos: int  # index of the quest in ``ids``

    @property
    def length(self) -> int:
        return len(self.ids)

    @property
    def rest(self) -> list[int]:
        return self.ids[self.pos:]


@dataclass
class Assessment:
    quest_id: int
    title: str
    verdict: str  # "great" / "ok" / "skip"
    facts: list[str] = field(default_factory=list)  # Polish, for the LLM or the plain answer
    xp: int = 0
    level_share: float = 0.0  # this quest's XP as a fraction of the current level bar
    chain: Optional[Chain] = None

    def text(self) -> str:
        tail = {"great": "Warto, bierz się za to.", "ok": "Może nie hit, ale exp to exp.",
                "skip": "Szczerze? Szkoda czasu, chyba że jesteś obok."}[self.verdict]
        return " ".join(self.facts) + " " + tail


class Chains:
    """Chain lookups over the whole quest table (built once per World)."""

    def __init__(self, world: World):
        self.world = world
        self.prev: dict[int, int] = {}
        self.after: dict[int, list[int]] = {}
        for qid, q in world.quests.items():
            nxt = q.get("next")
            if nxt and nxt in world.quests:
                self.prev.setdefault(nxt, qid)
            for p in q.get("ps", []) + q.get("pg", []):
                if p in world.quests:
                    self.after.setdefault(p, []).append(qid)
        for qid, q in world.quests.items():  # a lone prerequisite links a chain without "next" fields
            pre = q.get("ps", []) + q.get("pg", [])
            if qid not in self.prev and len(pre) == 1 and pre[0] in world.quests:
                self.prev[qid] = pre[0]

    def _forward(self, qid: int) -> Optional[int]:
        q = self.world.quests.get(qid, {})
        nxt = q.get("next")
        if nxt in self.world.quests:
            return nxt
        follow = [f for f in self.after.get(qid, []) if self.prev.get(f) == qid]
        return min(follow) if len(follow) >= 1 else None

    def chain(self, qid: int, limit: int = 30) -> Chain:
        back: list[int] = []
        cur, seen = qid, {qid}
        while cur in self.prev and len(back) < limit:
            cur = self.prev[cur]
            if cur in seen:
                break
            seen.add(cur)
            back.append(cur)
        ids = list(reversed(back)) + [qid]
        cur = qid
        while len(ids) < limit:
            nxt = self._forward(cur)
            if nxt is None or nxt in seen:
                break
            seen.add(nxt)
            ids.append(nxt)
            cur = nxt
        return Chain(ids, len(back))


def effective_xp(xp: int, quest_level: int, player_level: int) -> int:
    """Classic-style reduction: full XP up to 5 levels above the quest, then 20% less per level, at least 10%."""
    if not quest_level or not player_level:
        return xp
    diff = player_level - quest_level
    if diff <= 5:
        return xp
    return int(round(xp * max(0.1, 1.0 - 0.2 * (diff - 5))))


def _reward_items(world: World, qid: int) -> list[dict]:
    return [world.items[i] for i in world.quests.get(qid, {}).get("rw", []) if i in world.items]


def _fits(item: dict, class_id: int, level: int) -> bool:
    if item.get("c") not in (WEAPON_CLASS, ARMOR_CLASS) or not class_id:
        return False
    li = LootItem(class_id=item["c"], subclass_id=item.get("sc", 0), equip_loc="INVTYPE_CHEST")
    return usable(li, class_id, max(level, item.get("rl", 0)))[0]


def assess(world: World, chains: Chains, qid: int, level: int, xp_max: int = 0, class_id: int = 0,
           group: int = 0) -> Optional[Assessment]:
    q = world.quests.get(qid)
    if q is None:
        return None
    title = q["n"]
    facts: list[str] = []
    xp = effective_xp(q.get("xp", 0), q.get("lvl", 0), level)
    share = xp / xp_max if xp_max else 0.0
    ch = chains.chain(qid)
    score = 0
    if xp:
        facts.append(f"Za samego questa {xp} XP" + (f", czyli {round(share * 100)}% poziomu." if xp_max else "."))
        score += 2 if share >= 0.08 else 1 if share >= 0.03 else 0
    if q.get("lvl") and level and level - q["lvl"] >= 6:
        facts.append("Quest jest już dla ciebie szary.")
        score -= 2
    if ch.length > 1:
        rest_xp = sum(effective_xp(world.quests[i].get("xp", 0), world.quests[i].get("lvl", 0), level)
                      for i in ch.rest)
        facts.append(f"To {ch.pos + 1}. z {ch.length} questów w łańcuchu"
                     + (f", do końca łącznie około {rest_xp} XP." if rest_xp > xp else "."))
        if ch.pos + 1 < ch.length:
            score += 1
        last = ch.ids[-1]
        rewards = _reward_items(world, last)
        mine = [r for r in rewards if _fits(r, class_id, level)]
        if mine:
            facts.append(f"Na końcu nagroda {mine[0]['n']}" + (f" (poziom przedmiotu {mine[0]['il']})" if mine[0].get('il') else "")
                         + ", którą możesz założyć.")
            score += 2
        elif rewards:
            facts.append(f"Na końcu do wyboru {', '.join(r['n'] for r in rewards[:2])}, ale raczej nie dla ciebie.")
        dungeon = next((world.dungeons[world.quests[i]["zone"]]["n"] for i in ch.rest
                        if world.quests[i].get("zone") in world.dungeons), "")
        if dungeon:
            facts.append(f"Łańcuch prowadzi do {dungeon}, więc zbierz grupę.")
            score += 1
    else:
        rewards = [r for r in _reward_items(world, qid) if _fits(r, class_id, level)]
        if rewards:
            facts.append(f"W nagrodę {rewards[0]['n']}, którą możesz założyć.")
            score += 2
    if q.get("bc") and q["bc"] in world.quests:
        facts.append(f"To tylko kurier do questa {world.quests[q['bc']]['n']}, szybki exp za bieganie.")
    elites = [world.npcs[n]["n"] for n, _ in q.get("kill", []) if world.npcs.get(n, {}).get("r")]
    if elites or group > 1:
        facts.append("Uwaga, elity: " + ", ".join(elites[:2]) + "." if elites else f"Na grupę {group} osób.")
        score -= 1 if not group else 0
    verdict = "great" if score >= 3 else "skip" if score <= 0 and share < 0.03 else "ok"
    return Assessment(qid, title, verdict, facts, xp, share, ch)
