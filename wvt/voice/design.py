"""Create the race+gender voice pools with ElevenLabs Voice Design.

    python -m wvt.voice.design --dry-run            # what would be created
    python -m wvt.voice.design                      # fill every missing slot (5 per pool)
    python -m wvt.voice.design --races Orc,Gnome --genders Male --slots 1

Each slot = one Voice Design request with its own description variant (so the voices of a
pool differ in age and character) + saving the first preview as the voice
"WVT <Race> <Gender> <slot>" in your ElevenLabs account.  Every created voice is written to
the voice store right away (MongoDB plus a JSON copy in data/), and existing slots are never
replaced, so the command can be re-run safely after an interruption.
"""

from __future__ import annotations

import argparse
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from ..config import Config, resolve_path
from ..envfile import load_env, secret
from .elevenlabs import ElevenLabsClient, ElevenLabsError
from .races import GENDERS, POOL_RACES
from .store import JsonVoiceStore, VoiceProfile, open_store

RACE_TRAITS = {
    "Human": "human from a medieval fantasy kingdom, with a clear, natural voice and a neutral English accent",
    "Dwarf": "stout fantasy dwarf from snowy mountain halls, with a deep, gravelly, hearty voice and a broad Scottish accent",
    "NightElf": "night elf from ancient moonlit forests, with a calm, airy, graceful and melodic voice",
    "Gnome": "tiny fantasy gnome tinkerer, with a high-pitched, quick, energetic and slightly nasal voice",
    "Orc": "hulking fantasy orc warrior, with a very deep, rough, guttural and forceful voice",
    "Undead": "undead revenant, with a raspy, hollow, dry and sardonic voice",
    "Tauren": "massive, gentle bull-like tauren, with a very deep, slow, warm and resonant voice",
    "Troll": "lanky jungle troll, with a laid-back, rhythmic voice and a Caribbean-style lilt",
    "Unknown": "adventurer in a fantasy world, with a natural, friendly and clear voice",
}
VARIANTS = (
    "young adult, bright and energetic",
    "middle-aged, confident and steady",
    "older and weathered, a little gravelly, experienced",
    "playful and expressive, a bit mischievous",
    "calm, soft-spoken and thoughtful",
)
PREVIEW_TEXT = ("Well met, friend! We are heading into the dungeon soon and we still need a healer. "
                "Stay close, watch the patrol on the left, and do not pull before I say so. Ready when you are!")


def describe(race: str, gender: str, slot: int) -> str:
    return (f"A {gender.lower()} {RACE_TRAITS[race]}. {VARIANTS[(slot - 1) % len(VARIANTS)].capitalize()}. "
            "Speaks English naturally at a conversational pace. Clean studio recording, no background noise or music.")


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
    ap.add_argument("--model", default="eleven_ttv_v3")
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    load_env()
    cfg = Config.load()
    vcfg = cfg["voice"]
    slots = args.slots or int(vcfg.get("slots_per_pool", 5))
    races = [r.strip() for r in args.races.split(",") if r.strip()]
    genders = [g.strip().capitalize() for g in args.genders.split(",") if g.strip()]
    unknown = [r for r in races if r not in RACE_TRAITS] + [g for g in genders if g not in GENDERS]
    if unknown:
        print("Nieznane rasy/płcie:", ", ".join(unknown))
        return 2

    fallback = resolve_path(vcfg["store"].get("fallback_dir", "data"))
    store = open_store(vcfg["store"], fallback)
    backup = JsonVoiceStore(fallback) if store.kind == "mongodb" else None
    existing = {(p.race, p.gender, p.slot) for p in store.load_profiles()}
    todo = [(r, g, s) for r in races for g in genders for s in range(1, slots + 1) if (r, g, s) not in existing]
    print(f"Magazyn: {store.description}. Istnieje {len(existing)} profili, do utworzenia: {len(todo)}.")
    if args.dry_run or not todo:
        for r, g, s in todo:
            print(f"  WVT {r} {g} {s}: {describe(r, g, s)}")
        return 0

    client = ElevenLabsClient(secret(vcfg.get("api_key_env", "ELEVENLABS_API_KEY")))
    lock = threading.Lock()
    totals = {"created": 0, "credits": 0.0, "failed": 0}

    def create(item: tuple[str, str, int]) -> str:
        race, gender, slot = item
        desc = describe(race, gender, slot)
        name = f"WVT {race} {gender} {slot}"
        for attempt in range(3):
            try:
                previews, cost = client.design_voice(desc, PREVIEW_TEXT, model_id=args.model,
                                                     seed=1000 + POOL_RACES.index(race) * 100 + GENDERS.index(gender) * 10 + slot)
                if not previews:
                    raise ElevenLabsError("Voice Design returned no previews")
                voice_id = client.create_voice(name, desc, previews[0]["generated_voice_id"])
                break
            except ElevenLabsError as exc:
                if not exc.retryable or attempt == 2:
                    raise
                time.sleep(2 + attempt * 3)
        profile = VoiceProfile(voice_id, race, gender, slot, True, desc, name)
        store.save_profile(profile)
        if backup is not None:
            backup.save_profile(profile)
        with lock:
            totals["created"] += 1
            totals["credits"] += cost or 0.0
        return f"{name} -> {voice_id}" + (f" ({cost:.0f} kredytów)" if cost else "")

    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = {pool.submit(create, item): item for item in todo}
        for i, fut in enumerate(as_completed(futures), 1):
            race, gender, slot = futures[fut]
            try:
                print(f"[{i}/{len(todo)}] {fut.result()}", flush=True)
            except Exception as exc:
                with lock:
                    totals["failed"] += 1
                print(f"[{i}/{len(todo)}] WVT {race} {gender} {slot}: BŁĄD {exc}", flush=True)
    print(f"Gotowe: utworzono {totals['created']}, błędów {totals['failed']}, "
          f"koszt wg nagłówków {totals['credits']:.0f} kredytów.")
    return 0 if totals["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
