"""More choices for the voice slots the user did not rate 5 (rating round 4 onwards).

    python -m wvt.voice.candidates apply --ratings DIR      # read the rating page's scores
    python -m wvt.voice.candidates make --samples-dir DIR   # candidates for every pool with open slots
    python -m wvt.voice.candidates status

The user's rule: "jak nie ma 5 to dawaj mi więcej wyborów, a jak już jest 5 na 5 to zamykamy postać".

* A voice rated 5 is locked: final, the design tools never touch it again.
* A pool (one race + gender, one "postać") with five locked voices is closed.
* A slot rated 1-4 is open.  Its pool gets candidate voices, one per character brief in
  ``briefs.CANDIDATE_BRIEFS``, and the user rates them on the same page.  A candidate is not
  heard in the game until it wins a slot.
* ``apply`` stores every score, locks the 5s and lets the best-rated candidates take the open
  slots of their pool: the lowest-rated slot first, and only when the candidate scored higher
  than the voice it replaces.  A candidate rated 5 locks its new slot.  Unrated slots and
  unrated candidates wait for the user.

Candidates are listed in data/voice_candidates.json (git-ignored).  The ElevenLabs voices of
rejected candidates and replaced slot voices stay in the account until the user agrees to
delete them (data/replaced_voices.json lists the replaced ones).
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from ..config import resolve_path
from .briefs import CANDIDATE_BRIEFS
from .races import GENDERS, POOL_RACES
from .store import JsonVoiceStore, VoiceProfile, open_store

QUALITY_TAIL = " High-quality, crisp audio with no background noise or music."
OPEN, CHOSEN, REJECTED, SPARE, REPLACED = "open", "chosen", "rejected", "spare", "replaced"
UNUSED = "unused"  # never rated: its character was closed with other voices
POOL_SIZE = 5


def _print(*a) -> None:
    print(*a, flush=True)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def score_of(ratings: dict[str, dict], voice_id: str) -> int:
    """The user's score 1-5 of a voice, 0 when it has none."""
    try:
        s = int((ratings.get(voice_id) or {}).get("score") or 0)
    except (TypeError, ValueError):
        return 0
    return s if 1 <= s <= 5 else 0


def load_ratings(path: Path) -> dict[str, dict]:
    """Rating documents saved by the artifact database export: a folder of ``<voiceId>.json``
    files (optionally inside ``ratings/``), each the document itself or ``{id, data}``."""
    path = Path(path)
    folder = path / "ratings" if (path / "ratings").is_dir() else path
    if not folder.is_dir():
        raise FileNotFoundError(f"no ratings folder at {path}")
    out: dict[str, dict] = {}
    for f in sorted(folder.glob("*.json")):
        d = json.loads(f.read_text(encoding="utf-8"))
        if isinstance(d, dict) and isinstance(d.get("data"), dict):
            out[str(d.get("id") or f.stem)] = d["data"]
        elif isinstance(d, dict):
            out[f.stem] = d
    return out


class CandidateFile:
    """data/voice_candidates.json: every candidate voice made so far, with its status."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.Lock()
        if self.path.exists():
            try:
                self.items: list[dict] = json.loads(self.path.read_text(encoding="utf-8"))
            except ValueError as exc:  # never start over silently: the file is the only list of these voices
                raise SystemExit(f"{self.path} is not valid JSON ({exc}); fix or move it first") from exc
        else:
            self.items = []

    def add(self, item: dict) -> None:
        with self._lock:
            self.items.append(item)
            self._save()

    def save(self) -> None:
        with self._lock:
            self._save()

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.items, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.path)

    def of_pool(self, race: str, gender: str, statuses: Optional[set[str]] = None) -> list[dict]:
        return [c for c in self.items if c["race"] == race and c["gender"] == gender
                and (statuses is None or c.get("status", OPEN) in statuses)]


# --------------------------------------------------------------------------- the round's decisions

@dataclass
class Promotion:
    candidate: dict
    old: VoiceProfile
    new: VoiceProfile


@dataclass
class RoundPlan:
    rated: list[VoiceProfile] = field(default_factory=list)  # slot voices whose score or lock changed
    promotions: list[Promotion] = field(default_factory=list)
    statuses: dict[str, str] = field(default_factory=dict)  # candidate voiceId -> new status


def _open_key(p: VoiceProfile) -> tuple[int, int]:
    return p.rating, p.slot


def plan_round(profiles: list[VoiceProfile], candidates: list[dict], ratings: dict[str, dict]) -> RoundPlan:
    """What the user's ratings decide (pure function, nothing is written)."""
    plan = RoundPlan()
    current: dict[tuple[str, str, int], VoiceProfile] = {}
    for p in profiles:
        s = score_of(ratings, p.voice_id)
        if s and (s != p.rating or p.locked != (s == 5)):
            p = replace(p, rating=s, locked=s == 5)
            plan.rated.append(p)
        current[(p.race, p.gender, p.slot)] = p

    pools: dict[tuple[str, str], list[dict]] = {}
    for c in candidates:  # rejected ones too: the user may have changed a score since
        if c.get("status", OPEN) in (OPEN, SPARE, REJECTED) and score_of(ratings, c["voiceId"]):
            pools.setdefault((c["race"], c["gender"]), []).append(c)
    for (race, gender), cands in pools.items():
        open_slots = sorted((p for (r, g, _s), p in current.items()
                             if (r, g) == (race, gender) and not p.locked and 1 <= p.rating <= 4), key=_open_key)
        cands.sort(key=lambda c: (-score_of(ratings, c["voiceId"]),
                                  str((ratings.get(c["voiceId"]) or {}).get("updatedAt", ""))))
        for c in cands:
            s = score_of(ratings, c["voiceId"])
            if open_slots and s > open_slots[0].rating:
                old = open_slots.pop(0)
                new = VoiceProfile(c["voiceId"], race, gender, old.slot, True, c.get("description", ""),
                                   f"WVT {race} {gender} {old.slot}", rating=s, locked=s == 5)
                plan.promotions.append(Promotion(c, old, new))
                plan.statuses[c["voiceId"]] = CHOSEN
                current[(race, gender, old.slot)] = new
                if not new.locked:  # better than before, but the slot stays open for a 5
                    open_slots.append(new)
                    open_slots.sort(key=_open_key)
            else:
                status = SPARE if s == 5 else REJECTED
                if status != c.get("status", OPEN):
                    plan.statuses[c["voiceId"]] = status
    return plan


def apply_plan(plan: RoundPlan, ratings: dict[str, dict], store, backup, cands: CandidateFile,
               replaced_log: Path) -> int:
    """Write the plan: scores and locks, promoted candidates (their players move along), statuses."""
    for p in plan.rated:
        store.save_profile(p)
        if backup is not None:
            backup.save_profile(p)
    moved = 0
    by_id = {c["voiceId"]: c for c in cands.items}
    replaced = []
    for pr in plan.promotions:
        store.save_profile(pr.new)
        if backup is not None:
            backup.save_profile(pr.new)
        moved += store.reassign_voice(pr.old.voice_id, pr.new.voice_id)
        replaced.append({"name": pr.new.name, "voiceId": pr.new.voice_id, "replaced": pr.old.voice_id,
                         "replacedScore": pr.old.rating, "by": "candidate", "at": _now()})
        if pr.old.voice_id in by_id:  # an earlier candidate lost its slot to a better one
            by_id[pr.old.voice_id]["status"] = REPLACED
    for vid, status in plan.statuses.items():
        c = by_id[vid]
        c["status"] = status
        if status == CHOSEN:
            c["slot"] = next(pr.new.slot for pr in plan.promotions if pr.new.voice_id == vid)
    for c in cands.items:  # remember the user's verdicts next to the briefs
        if score_of(ratings, c["voiceId"]):
            c["score"] = score_of(ratings, c["voiceId"])
            c["note"] = str((ratings.get(c["voiceId"]) or {}).get("note", ""))
    closed = {(r["race"], r["gender"]) for r in pool_rows(store.load_profiles(), []) if r["closed"]}
    for c in cands.items:  # a closed character needs no more choices
        if c.get("status", OPEN) in (OPEN, SPARE) and (c["race"], c["gender"]) in closed:
            c["status"] = UNUSED
    cands.save()
    if replaced:
        old = json.loads(replaced_log.read_text(encoding="utf-8")) if replaced_log.exists() else []
        replaced_log.write_text(json.dumps(old + replaced, indent=1), encoding="utf-8")
    return moved


ASSISTANT_RACE = "Assistant"  # the voice assistant's own candidates: one voice, chosen with a 5


def assistant_pick(candidates: list[dict], ratings: dict[str, dict]) -> Optional[dict]:
    """The assistant candidate rated 5 first (the page shows it as chosen), or None."""
    fives = [c for c in candidates if c["race"] == ASSISTANT_RACE and score_of(ratings, c["voiceId"]) == 5]
    fives.sort(key=lambda c: str((ratings.get(c["voiceId"]) or {}).get("updatedAt", "")))
    return fives[0] if fives else None


def pool_rows(profiles: list[VoiceProfile], candidates: list[dict]) -> list[dict]:
    """Per pool: locked voices, open slots (rated 1-4), unrated slots, candidates waiting."""
    rows = []
    for race in POOL_RACES:
        for gender in GENDERS:
            ps = sorted((p for p in profiles if p.race == race and p.gender == gender), key=lambda p: p.slot)
            if not ps:
                continue
            waiting = [c for c in candidates if c["race"] == race and c["gender"] == gender
                       and c.get("status", OPEN) in (OPEN, SPARE)]
            rows.append({"race": race, "gender": gender, "slots": len(ps),
                         "locked": sum(p.locked for p in ps),
                         "open": [p.slot for p in ps if not p.locked and 1 <= p.rating <= 4],
                         "unrated": [p.slot for p in ps if not p.locked and not p.rating],
                         "candidates": len(waiting),
                         "closed": len(ps) >= POOL_SIZE and all(p.locked for p in ps)})
    return rows


def open_pools(profiles: list[VoiceProfile]) -> list[tuple[str, str]]:
    """Pools with at least one slot rated 1-4 that is not locked."""
    return [(r["race"], r["gender"]) for r in pool_rows(profiles, []) if r["open"]]


def unused_briefs(pool: tuple[str, str], cands: CandidateFile) -> list[dict]:
    used = {c.get("label") for c in cands.of_pool(*pool)}
    return [b for b in CANDIDATE_BRIEFS.get(pool, []) if b["label"] not in used]


# Measured on the EN+PL review samples: the same voice reading two texts scores ~0.74.
LOCKED_MAX = 0.72  # a candidate this close to a locked voice of its pool would duplicate it
PAIR_MAX = 0.78  # two candidates this close are one choice shown twice


def near_duplicates(locked: dict, cands: dict, locked_max: float = LOCKED_MAX,
                    pair_max: float = PAIR_MAX) -> dict[str, tuple[str, float]]:
    """Candidates of one pool not worth the user's time: voiceId -> (the voice it copies, similarity).

    ``locked`` and ``cands`` map voiceId -> speaker embedding.  Of a too-close pair of candidates
    the more central one goes (the higher mean similarity to the rest of the pool), so the
    candidates that stay are as different as possible."""
    from .similarity import cosine

    drop: dict[str, tuple[str, float]] = {}
    for cid, e in cands.items():
        close = max(((cosine(e, le), lid) for lid, le in locked.items()), default=(0.0, ""))
        if close[0] >= locked_max:
            drop[cid] = (close[1], round(close[0], 2))
    while True:
        alive = [c for c in cands if c not in drop]
        pairs = [(cosine(cands[a], cands[b]), a, b) for i, a in enumerate(alive) for b in alive[i + 1:]]
        if not pairs:
            break
        sim, a, b = max(pairs)
        if sim < pair_max:
            break
        others = {**{k: v for k, v in cands.items() if k in alive}, **locked}

        def centrality(x: str) -> float:
            return sum(cosine(cands[x], v) for k, v in others.items() if k != x) / max(1, len(others) - 1)

        loser, keeper = (a, b) if centrality(a) >= centrality(b) else (b, a)
        drop[loser] = (keeper, round(sim, 2))
    return drop


# --------------------------------------------------------------------------- CLI

def _stores(cfg):
    vcfg = cfg["voice"]
    fallback = resolve_path(vcfg["store"].get("fallback_dir", "data"))
    store = open_store(vcfg["store"], fallback)
    backup = JsonVoiceStore(fallback) if store.kind == "mongodb" else None
    return store, backup, fallback


def cmd_status(cfg) -> int:
    store, _backup, fallback = _stores(cfg)
    cands = CandidateFile(fallback / "voice_candidates.json")
    rows = pool_rows(store.load_profiles(), cands.items)
    closed = sum(r["closed"] for r in rows)
    _print(f"Magazyn: {store.description}. Zamknięte postacie: {closed}/{len(rows)}, "
           f"zablokowane głosy: {sum(r['locked'] for r in rows)}/{sum(r['slots'] for r in rows)}.")
    for r in rows:
        state = "ZAMKNIĘTA" if r["closed"] else (
            f"zablokowane {r['locked']}/{r['slots']}"
            + (f", otwarte sloty {r['open']}" if r["open"] else "")
            + (f", bez oceny {r['unrated']}" if r["unrated"] else "")
            + (f", kandydaci czekają: {r['candidates']}" if r["candidates"] else ""))
        _print(f"  {r['race']:9} {r['gender']:6} {state}")
    return 0


def cmd_apply(cfg, args) -> int:
    store, backup, fallback = _stores(cfg)
    cands = CandidateFile(fallback / "voice_candidates.json")
    ratings = load_ratings(Path(args.ratings))
    profiles = store.load_profiles()
    plan = plan_round(profiles, cands.items, ratings)
    newly_locked = [p for p in plan.rated if p.locked]
    _print(f"Ocen: {len(ratings)}. Zmienione oceny slotów: {len(plan.rated)} (nowe blokady: {len(newly_locked)}), "
           f"kandydaci na slot: {len(plan.promotions)}.")
    for p in plan.rated:
        _print(f"  {p.race} {p.gender} {p.slot}: ocena {p.rating}" + (" -> zablokowany" if p.locked else ""))
    for pr in plan.promotions:
        _print(f"  {pr.new.race} {pr.new.gender} {pr.new.slot}: kandydat „{pr.candidate.get('label')}” "
               f"({pr.new.rating}) zamiast głosu z oceną {pr.old.rating}" + (" -> zablokowany" if pr.new.locked else ""))
    for vid, status in plan.statuses.items():
        if status != CHOSEN:
            c = next(c for c in cands.items if c["voiceId"] == vid)
            _print(f"  kandydat {c['race']} {c['gender']} „{c.get('label')}”: {status}")
    if args.dry_run:
        return 0
    moved = apply_plan(plan, ratings, store, backup, cands, fallback / "replaced_voices.json")
    _print(f"Zapisane. Przeniesione postacie graczy: {moved}.")
    pick = assistant_pick(cands.items, ratings)
    if pick is not None:
        cfg.set("assistant.voice_id", pick["voiceId"])
        cfg.save()
        for c in cands.items:
            if c["race"] == ASSISTANT_RACE:
                c["status"] = CHOSEN if c is pick else (UNUSED if not score_of(ratings, c["voiceId"]) else REJECTED)
        cands.save()
        _print(f"Głos asystentki: „{pick.get('label')}” ({pick['voiceId']}) – zapisany w config.json.")
    return cmd_status(cfg)


def cmd_prune(cfg, args) -> int:
    """Reject waiting candidates that duplicate a locked voice or another candidate (no credits spent:
    only samples already on disk are compared)."""
    from .design import load_embedder, voice_embedding

    store, _backup, fallback = _stores(cfg)
    cands = CandidateFile(fallback / "voice_candidates.json")
    profiles = store.load_profiles()
    embedder = load_embedder()
    if embedder is None:
        return 2
    samples_dir = Path(args.samples_dir)
    names = {c["voiceId"]: f"kandydatka „{c.get('label')}”" for c in cands.items}
    names.update({p.voice_id: f"zamknięty slot {p.slot}" for p in profiles})
    dropped = 0
    for race in POOL_RACES:
        for gender in GENDERS:
            waiting = cands.of_pool(race, gender, {OPEN, SPARE})
            if not waiting:
                continue
            locked = {p.voice_id: voice_embedding(embedder, p.voice_id, samples_dir, None)
                      for p in profiles if (p.race, p.gender) == (race, gender) and p.locked}
            mine = {c["voiceId"]: voice_embedding(embedder, c["voiceId"], samples_dir, None) for c in waiting}
            missing = [names[v] for v, e in {**locked, **mine}.items() if e is None]
            if missing:
                _print(f"  {race} {gender}: brak próbek dla {', '.join(missing)} – pomijam je")
            drop = near_duplicates({k: v for k, v in locked.items() if v is not None},
                                   {k: v for k, v in mine.items() if v is not None}, args.locked_max, args.pair_max)
            for c in waiting:
                if c["voiceId"] in drop:
                    other, sim = drop[c["voiceId"]]
                    _print(f"  {race} {gender}: odpada {names[c['voiceId']]} – brzmi jak {names[other]} ({sim:.2f})")
                    if not args.dry_run:
                        c["status"] = REJECTED
                        c["note"] = f"brzmi jak {names[other]} ({sim:.2f})"
                    dropped += 1
    if not args.dry_run:
        cands.save()
    _print(f"Odpada {dropped} kandydatów" + (" (próba, nic nie zapisane)." if args.dry_run else "."))
    return 0


def cmd_make(cfg, args) -> int:
    import numpy as np

    from ..envfile import secret
    from .design import load_embedder, load_stt, voice_embedding
    from .elevenlabs import ElevenLabsClient, ElevenLabsError

    store, _backup, fallback = _stores(cfg)
    cands = CandidateFile(fallback / "voice_candidates.json")
    profiles = store.load_profiles()
    if args.pools:
        pools = []
        for item in (x.strip() for x in args.pools.split(",") if x.strip()):
            race, gender = item.split(":")
            pools.append((race, gender.capitalize()))
    else:
        pools = open_pools(profiles)
    todo = {pool: unused_briefs(pool, cands)[: args.count or None] for pool in pools}
    if args.retry_flagged:  # candidates the judge warned about get another, longer try
        for pool in pools:
            briefs = {b["label"]: b for b in CANDIDATE_BRIEFS.get(pool, [])}
            for c in cands.of_pool(*pool, {OPEN}):
                if c.get("warnings") and c.get("label") in briefs:
                    todo[pool].append(dict(briefs[c["label"]], replaces=c["voiceId"]))
    todo = {k: v for k, v in todo.items() if v}
    missing = [f"{r} {g}" for r, g in pools if not CANDIDATE_BRIEFS.get((r, g))]
    _print(f"Magazyn: {store.description}. Pule z otwartymi slotami: {len(pools)}. "
           f"Nowi kandydaci: {sum(len(v) for v in todo.values())}"
           + (f". Brak opisów postaci dla: {', '.join(missing)} (dopisz je w briefs.CANDIDATE_BRIEFS)" if missing else "")
           + ".")
    for (race, gender), briefs in todo.items():
        for b in briefs:
            _print(f"  {race} {gender}: {b['label']} – {b['brief']}")
    if args.dry_run or not todo:
        return 0

    vcfg = cfg["voice"]
    key = secret(vcfg.get("api_key_env", "ELEVENLABS_API_KEY"))
    client = ElevenLabsClient(key, timeout_s=60)
    embedder = load_embedder()
    samples_dir = Path(args.samples_dir) if args.samples_dir else None
    sample_client = ElevenLabsClient(key, output_format="mp3_44100_64")
    genders = {g for _r, g in todo}
    emb: dict[str, np.ndarray] = {}
    if embedder is not None:
        for p in profiles:  # the user's locked voices teach the judge; all of a gender keep pools apart
            if p.locked or p.gender in genders:
                e = voice_embedding(embedder, p.voice_id, samples_dir, sample_client)
                if e is not None:
                    emb[p.voice_id] = e
        for c in cands.items:  # earlier candidates still waiting: no TTS spent on them
            if c.get("status", OPEN) in (OPEN, SPARE) and (c["race"], c["gender"]) in todo:
                e = voice_embedding(embedder, c["voiceId"], samples_dir, None)
                if e is not None:
                    emb[c["voiceId"]] = e
    judge = None
    if embedder is not None:
        from .judge import VoiceJudge

        refs = [(p.race, p.gender, emb[p.voice_id]) for p in profiles if p.locked and p.voice_id in emb]
        judge = VoiceJudge(refs, None if args.no_speech_check else load_stt(cfg))
        _print(f"Sędzia: {len(refs)} głosów ocenionych na 5, sprawdzanie mowy: {'tak' if judge.stt else 'nie'}.")

    lock = threading.Lock()
    totals = {"created": 0, "requests": 0, "failed": 0}

    def make_pool(pool: tuple[str, str]) -> None:
        from .similarity import decode_audio

        race, gender = pool
        chosen = [emb[p.voice_id] for p in profiles
                  if (p.race, p.gender) == pool and p.locked and p.voice_id in emb]
        chosen += [emb[c["voiceId"]] for c in cands.of_pool(race, gender, {OPEN, SPARE}) if c["voiceId"] in emb]
        others = [emb[p.voice_id] for p in profiles
                  if p.gender == gender and (p.race, p.gender) != pool and p.voice_id in emb]
        number = len(cands.of_pool(race, gender))
        for i, b in enumerate(todo[pool]):
            desc = b["brief"] + QUALITY_TAIL
            best = None  # (preview, embedding, verdict)
            for attempt in range(max(1, args.attempts)):
                race_ix = POOL_RACES.index(race) if race in POOL_RACES else 50  # 50: the assistant's own pool
                seed = 9000 + race_ix * 100 + GENDERS.index(gender) * 50 + (number + i) * 7 \
                    + attempt * 7919
                try:
                    previews, _ = client.design_voice(desc, b["line"], model_id=args.model, seed=seed,
                                                      guidance_scale=args.guidance)
                except ElevenLabsError as exc:
                    if not exc.retryable:
                        raise
                    time.sleep(3)
                    continue
                with lock:
                    totals["requests"] += 1
                for pv in previews:
                    if judge is None:
                        best = best or (pv, None, None)
                        continue
                    audio = decode_audio(base64.b64decode(pv["audio_base_64"]))
                    e = embedder.embed(audio)
                    v = judge.assess(audio, e, race, gender, b["line"], chosen, others)
                    if best is None or (not v.ok, v.score) < (not best[2].ok, best[2].score):
                        best = (pv, e, v)
                if best is not None and (best[2] is None or (best[2].ok and best[2].score <= args.target)):
                    break
            if best is None:
                raise ElevenLabsError(f"Voice Design nie zwrócił podglądów dla {race} {gender} „{b['label']}”")
            name = f"WVT {race} {gender} kandydat {number + i + 1}"
            voice_id = client.create_voice(name, desc, best[0]["generated_voice_id"])
            verdict = best[2]
            if b.get("replaces"):  # the judge's earlier pick of this character leaves the page
                with cands._lock:
                    for c in cands.items:
                        if c["voiceId"] == b["replaces"]:
                            c["status"] = REJECTED
                            c["note"] = "sędzia: " + "; ".join(c.get("warnings", []))
            cands.add({"voiceId": voice_id, "race": race, "gender": gender, "label": b["label"], "name": name,
                       "description": desc, "line": b["line"], "status": OPEN, "round": 4, "createdAt": _now(),
                       "judge": verdict.details if verdict else {}, "warnings": verdict.reasons if verdict else []})
            if best[1] is not None:
                chosen.append(best[1])
            with lock:
                totals["created"] += 1
            _print(f"  {name} „{b['label']}” -> {voice_id}" + (f"  {verdict.details}" if verdict else "")
                   + (f"  UWAGA: {'; '.join(verdict.reasons)}" if verdict and verdict.reasons else ""))

    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as ex:
        futures = {ex.submit(make_pool, pool): pool for pool in todo}
        for fut in as_completed(futures):
            race, gender = futures[fut]
            try:
                fut.result()
                _print(f"[pula gotowa] {race} {gender}")
            except Exception as exc:
                with lock:
                    totals["failed"] += 1
                _print(f"[BŁĄD] {race} {gender}: {exc}")
    _print(f"Gotowe: {totals['created']} kandydatów z {totals['requests']} zapytań Voice Design, "
           f"pule z błędem: {totals['failed']}.")
    return 0 if not totals["failed"] else 1


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(prog="python -m wvt.voice.candidates")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status", help="locked voices and open slots per pool")
    a = sub.add_parser("apply", help="store the user's scores, lock the 5s, promote candidates")
    a.add_argument("--ratings", required=True, help="folder with the exported rating documents")
    a.add_argument("--dry-run", action="store_true")
    m = sub.add_parser("make", help="design candidate voices for pools with open slots")
    m.add_argument("--pools", default="", help="Race:Gender,... (default: every pool with a slot rated 1-4)")
    m.add_argument("--count", type=int, default=0, help="at most this many new candidates per pool")
    m.add_argument("--samples-dir", default="", help="folder with <voiceId>_en.mp3/_pl.mp3 samples")
    m.add_argument("--attempts", type=int, default=2, help="Voice Design requests per candidate at most")
    m.add_argument("--target", type=float, default=0.45, help="stop once the best preview is this distinct")
    m.add_argument("--guidance", type=float, default=5.0)
    m.add_argument("--model", default="eleven_ttv_v3")
    m.add_argument("--workers", type=int, default=4)
    m.add_argument("--retry-flagged", action="store_true",
                   help="design again the waiting candidates the judge warned about (they are then rejected)")
    m.add_argument("--no-speech-check", action="store_true")
    m.add_argument("--dry-run", action="store_true")
    pr = sub.add_parser("prune", help="reject candidates that duplicate a locked voice or another candidate")
    pr.add_argument("--samples-dir", required=True, help="folder with <voiceId>_en.mp3/_pl.mp3 samples")
    pr.add_argument("--locked-max", type=float, default=LOCKED_MAX)
    pr.add_argument("--pair-max", type=float, default=PAIR_MAX)
    pr.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    from ..config import Config
    from ..envfile import load_env

    load_env()
    cfg = Config.load()
    if args.cmd == "status":
        return cmd_status(cfg)
    if args.cmd == "apply":
        return cmd_apply(cfg, args)
    if args.cmd == "prune":
        return cmd_prune(cfg, args)
    return cmd_make(cfg, args)


if __name__ == "__main__":
    sys.exit(main())
