"""Create (or redesign) the race+gender voice pools with ElevenLabs Voice Design.

    python -m wvt.voice.design --dry-run                    # briefs for the missing slots
    python -m wvt.voice.design                              # fill every missing slot
    python -m wvt.voice.design --replace --keep ID1,ID2     # redesign all slots except the kept voices
    python -m wvt.voice.design --replace --races Human --genders Male
    python -m wvt.voice.design --only Gnome:Male:2,Tauren:Female:4 --attempts 6   # re-roll single voices

Every slot has its own brief (``archetypes.py``: age, register, accent, pace, attitude and
an in-character preview line).  With the speaker model present (models/speaker, see
similarity.py) each Voice Design request is scored: of its previews, the one least
similar to the voices already in the pool is taken, and a request whose best preview is
still too close is repeated with another seed.  Created voices are written to the store
right away (MongoDB plus a JSON copy in data/); a replaced voice's players move to the new
voice of the same slot unless their assignment was set by hand, and the replaced ids are
listed in data/replaced_voices.json (the old voices stay in your ElevenLabs account).

Slots the user rated 5 are locked (``VoiceProfile.locked``) and never redesigned, not even with
--replace or --only.  Extra choices for the slots rated below 5 come from
``python -m wvt.voice.candidates`` instead.
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Optional

import numpy as np

from ..config import Config, resolve_path
from ..envfile import load_env, secret
from .archetypes import RACES, describe, preview_line
from .elevenlabs import ElevenLabsClient, ElevenLabsError
from .races import GENDERS, POOL_RACES
from .store import JsonVoiceStore, VoiceProfile, open_store

SAMPLE_TEXT = "Well met, friend! We still need a healer. Stay close and don't pull before I say so."
SLOT_ORDER = (2, 3, 1, 4, 5)  # the most contrasting briefs first


def _print(*a) -> None:
    print(*a, flush=True)


def plan_slots(by_slot: dict, races: list[str], genders: list[str], slots: int, replace: bool,
               keep: set[str], only: set[tuple[str, str, int]]) -> tuple[dict[tuple[str, str], list[int]], list[str]]:
    """Which slots to design: (pool -> slots, locked slots that were skipped)."""
    todo: dict[tuple[str, str], list[int]] = {}
    skipped: list[str] = []
    for r in races:
        for g in genders:
            for s in ([s for s in SLOT_ORDER if s <= slots] if slots <= 5 else range(1, slots + 1)):
                old = by_slot.get((r, g, s))
                if only and (r, g, s) not in only:
                    continue
                if old is not None and old.locked:  # rated 5 by the user: final
                    if replace:
                        skipped.append(f"{r} {g} {s}")
                    continue
                if old is None or (replace and old.voice_id not in keep):
                    todo.setdefault((r, g), []).append(s)
    return todo, skipped


def load_embedder():
    """The speaker model, or None when it (or ffmpeg) is missing."""
    try:
        from .similarity import SpeakerEmbedder

        return SpeakerEmbedder()
    except Exception as exc:  # model or ffmpeg missing: still works, just without the checks
        _print(f"Bez porównywania głosów ({exc}) – biorę pierwszy podgląd.")
        return None


def voice_embedding(embedder, voice_id: str, samples_dir: Optional[Path], sample_client) -> Optional[np.ndarray]:
    """Speaker embedding of an existing voice: its review samples, or one short line (~45 credits)."""
    if embedder is None:
        return None
    from .similarity import decode_audio

    parts = []
    if samples_dir:
        for lang in ("en", "pl"):
            f = samples_dir / f"{voice_id}_{lang}.mp3"
            if f.exists():
                parts.append(decode_audio(f.read_bytes()))
    if not parts:
        if sample_client is None:  # no sample on disk and no credits to be spent on one
            return None
        parts.append(decode_audio(b"".join(sample_client.stream_tts(voice_id, SAMPLE_TEXT, "en"))))
    return embedder.embed(np.concatenate(parts))


def load_stt(cfg: Config):
    """Parakeet on the CPU for the intelligibility check, or None."""
    try:
        from ..stt import SpeechToText

        stt = SpeechToText(cfg["stt"]["model_dir"], cfg["stt"].get("model_name", "nemo-parakeet-tdt-0.6b-v3"),
                           device="cpu")
        stt.load()
        return stt
    except Exception as exc:
        _print(f"Bez sprawdzania wyraźności mowy ({exc}).")
        return None


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(prog="python -m wvt.voice.design")
    ap.add_argument("--races", default=",".join(POOL_RACES))
    ap.add_argument("--genders", default=",".join(GENDERS))
    ap.add_argument("--slots", type=int, default=None, help="voices per pool (default: voice.slots_per_pool)")
    ap.add_argument("--replace", action="store_true", help="redesign existing slots too")
    ap.add_argument("--keep", default="", help="comma-separated voice ids that stay (with --replace)")
    ap.add_argument("--only", default="", help="redesign just these slots: Race:Gender:Slot,... (implies --replace)")
    ap.add_argument("--samples-dir", default="", help="folder with <voiceId>_en.mp3/_pl.mp3 of existing voices")
    ap.add_argument("--attempts", type=int, default=3, help="Voice Design requests per slot at most")
    ap.add_argument("--target", type=float, default=0.45, help="stop retrying once the best preview is this distinct")
    ap.add_argument("--guidance", type=float, default=6.0)
    ap.add_argument("--suffix", default="v2", help="appended to the names of redesigned voices")
    ap.add_argument("--model", default="eleven_ttv_v3")
    ap.add_argument("--no-speech-check", action="store_true", help="skip the Parakeet intelligibility check")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    load_env()
    cfg = Config.load()
    vcfg = cfg["voice"]
    slots = args.slots or int(vcfg.get("slots_per_pool", 5))
    races = [r.strip() for r in args.races.split(",") if r.strip()]
    genders = [g.strip().capitalize() for g in args.genders.split(",") if g.strip()]
    bad = [r for r in races if r not in RACES] + [g for g in genders if g not in GENDERS]
    if bad:
        _print("Nieznane rasy/płcie:", ", ".join(bad))
        return 2
    keep = {k.strip() for k in args.keep.split(",") if k.strip()}
    only = set()
    for item in (x.strip() for x in args.only.split(",") if x.strip()):
        r, g, s = item.split(":")
        only.add((r, g.capitalize(), int(s)))
    if only:
        args.replace = True

    fallback = resolve_path(vcfg["store"].get("fallback_dir", "data"))
    store = open_store(vcfg["store"], fallback)
    backup = JsonVoiceStore(fallback) if store.kind == "mongodb" else None
    by_slot = {(p.race, p.gender, p.slot): p for p in store.load_profiles()}
    todo, skipped = plan_slots(by_slot, races, genders, slots, args.replace, keep, only)
    total = sum(len(v) for v in todo.values())
    _print(f"Magazyn: {store.description}. Do zaprojektowania: {total} głosów w {len(todo)} pulach"
           + (f", zostają: {len(keep)}" if keep else "") + ".")
    if skipped:
        _print(f"Zablokowane (ocena 5), nie ruszam: {', '.join(skipped)}.")
    if args.dry_run or not total:
        for (r, g), ss in todo.items():
            for s in ss:
                _print(f"  WVT {r} {g} {s}: {describe(r, g, s)}")
        return 0

    client = ElevenLabsClient(secret(vcfg.get("api_key_env", "ELEVENLABS_API_KEY")), timeout_s=60)
    embedder = load_embedder()
    samples_dir = Path(args.samples_dir) if args.samples_dir else None
    lock = threading.Lock()
    sample_client = ElevenLabsClient(secret(vcfg.get("api_key_env", "ELEVENLABS_API_KEY")), output_format="mp3_44100_64")

    def existing_embedding(p: VoiceProfile) -> Optional[np.ndarray]:
        return voice_embedding(embedder, p.voice_id, samples_dir, sample_client)

    # voices that stay: anchors for their own pool and (weaker) for the same gender elsewhere
    staying = {k: p for k, p in by_slot.items() if (k[0], k[1]) not in todo or k[2] not in todo[(k[0], k[1])]}
    anchors: dict[tuple[str, str], list[np.ndarray]] = {}
    by_gender: dict[str, list[tuple[tuple[str, str], np.ndarray]]] = {g: [] for g in GENDERS}
    if embedder is not None:
        _print(f"Odciski {len(staying)} zostających głosów…")
        for (r, g, _s), p in staying.items():
            e = existing_embedding(p)
            if e is not None:
                anchors.setdefault((r, g), []).append(e)
                by_gender.setdefault(g, []).append(((r, g), e))
    judge = None
    if embedder is not None:
        from .judge import VoiceJudge

        refs = []
        for (r, g, _s), p in staying.items():
            if p.voice_id in keep:  # the user's well-rated voices teach gender and race fit
                e = existing_embedding(p)
                if e is not None:
                    refs.append((r, g, e))
        stt = None if args.no_speech_check else load_stt(cfg)
        judge = VoiceJudge(refs, stt)
        _print(f"Sędzia: {len(refs)} wzorcowych głosów, sprawdzanie mowy: {'tak' if stt else 'nie'}.")
    replaced_log = fallback / "replaced_voices.json"
    totals = {"created": 0, "failed": 0, "requests": 0, "moved_players": 0}
    results: list[dict] = []

    def design_pool(pool: tuple[str, str]) -> None:
        race, gender = pool
        chosen = list(anchors.get(pool, []))
        for slot in todo[pool]:
            desc = describe(race, gender, slot)
            best = None
            line = preview_line(slot, race, gender)
            for attempt in range(max(1, args.attempts)):
                seed = 4000 + POOL_RACES.index(race) * 100 + GENDERS.index(gender) * 10 + slot + attempt * 7919
                try:
                    previews, _ = client.design_voice(desc, line, model_id=args.model, seed=seed,
                                                      guidance_scale=args.guidance)
                except ElevenLabsError as exc:
                    if not exc.retryable:
                        raise
                    time.sleep(3)
                    continue
                with lock:
                    totals["requests"] += 1
                if not previews:
                    continue
                if embedder is None:
                    best = (previews[0], None, 0.0)
                    break
                from .similarity import decode_audio

                with lock:
                    others = [e for k, e in by_gender.get(gender, []) if k != pool]
                done = False
                for pv in previews:
                    audio = decode_audio(base64.b64decode(pv["audio_base_64"]))
                    emb = embedder.embed(audio)
                    verdict = judge.assess(audio, emb, race, gender, line, chosen, others)
                    key = (not verdict.ok, verdict.score)
                    if best is None or key < (not best[3].ok, best[3].score):
                        best = (pv, emb, verdict.details.get("pool", 0.0), verdict)
                    if verdict.ok and verdict.score <= args.target:
                        done = True
                if done:
                    break
            if best is None:
                raise ElevenLabsError(f"Voice Design nie zwrócił podglądów dla {race} {gender} {slot}")
            name = f"WVT {race} {gender} {slot}" + (f" {args.suffix}" if args.suffix and (race, gender, slot) in by_slot else "")
            voice_id = client.create_voice(name, desc, best[0]["generated_voice_id"])
            profile = VoiceProfile(voice_id, race, gender, slot, True, desc, name)
            store.save_profile(profile)
            if backup is not None:
                backup.save_profile(profile)
            old = by_slot.get((race, gender, slot))
            moved = store.reassign_voice(old.voice_id, voice_id) if old is not None else 0
            if best[1] is not None:
                chosen.append(best[1])
                with lock:
                    by_gender.setdefault(gender, []).append((pool, best[1]))
            with lock:
                totals["created"] += 1
                totals["moved_players"] += moved
                results.append({"name": name, "voiceId": voice_id, "replaced": old.voice_id if old else None,
                                "similarity": round(best[2], 3)})
            verdict = best[3] if len(best) > 3 else None
            _print(f"  {name} -> {voice_id}  podobieństwo do puli {best[2]:.2f}"
                   + (f"  {verdict.details}" if verdict else "")
                   + (f"  UWAGA: {'; '.join(verdict.reasons)}" if verdict and verdict.reasons else "")
                   + (f", przeniesione postacie: {moved}" if moved else ""))

    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool_exec:
        futures = {pool_exec.submit(design_pool, pool): pool for pool in todo}
        for fut in as_completed(futures):
            race, gender = futures[fut]
            try:
                fut.result()
                _print(f"[pula gotowa] {race} {gender}")
            except Exception as exc:
                with lock:
                    totals["failed"] += 1
                _print(f"[BŁĄD] {race} {gender}: {exc}")

    if any(r["replaced"] for r in results):
        old_log = json.loads(replaced_log.read_text(encoding="utf-8")) if replaced_log.exists() else []
        replaced_log.write_text(json.dumps(old_log + [r for r in results if r["replaced"]], indent=1), encoding="utf-8")
    sims = [r["similarity"] for r in results]
    _print(f"Gotowe: utworzono {totals['created']} głosów w {totals['requests']} zapytaniach Voice Design, "
           f"pule z błędem: {totals['failed']}, przeniesione postacie: {totals['moved_players']}"
           + (f", podobieństwo do puli średnio {np.mean(sims):.2f}, najwyżej {np.max(sims):.2f}" if sims else "") + ".")
    return 0 if totals["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
