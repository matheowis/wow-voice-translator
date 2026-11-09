"""Build the assistant's knowledge base:  python -m wvt.kb.build

    python -m wvt.kb.build              # download what is missing, then index everything
    python -m wvt.kb.build --offline    # only what is already in data/kb/raw (no network)
    python -m wvt.kb.build --replan     # ask the wiki again which pages to take (default: re-use the list)
    python -m wvt.kb.build --refresh    # download all wiki pages again (new revisions)
    python -m wvt.kb.build --search "gdzie jest Hogger"   # try a question against the built base

Sources: Warcraft Wiki (CC BY-SA 4.0, ~600 pages, one request per second, the first run takes
~15 min), QuestieDB data for WoW Forever (quests, NPCs, coordinates; ~13 MB from GitHub) and your
own notes in data/kb/guides (*.md, *.txt - add files there and run the build again).
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time

from . import DB_PATH, GUIDES_DIR, INSTANCES_PATH, KB_DIR, MODEL_DIR, RAW_DIR, VEC_PATH, WORLD_PATH
from . import questie, questie_tables
from .docs import extract_instances, guide_docs, quest_docs, service_docs, wiki_docs
from .net import PoliteClient
from .translate_pl import llm_for_build, load_cache, translate_world
from .wiki import RETAIL_PAGES, Wiki, _cache_path

log = logging.getLogger("wvt.kb.build")
PLAN_PATH = RAW_DIR / "wiki_plan.json"
PL_CACHE = RAW_DIR / "quests_pl.json"  # Polish quest texts (made once by the local LLM)


def _bar(label: str):
    last = [0.0]

    def progress(i: int, n: int, *_):
        now = time.monotonic()
        if now - last[0] > 0.5 or i == n:
            last[0] = now
            print(f"\r  {label}: {i}/{n}", end="" if i < n else "\n", flush=True)

    return progress


def build(offline: bool = False, replan: bool = False, refresh: bool = False, wiki: bool = True,
          translate: bool = True) -> dict:
    from .embedder import Embedder
    from .world import World, build_world, save_world

    if not (MODEL_DIR / "model_quantized.onnx").exists():
        raise SystemExit("Brak modelu wektorów. Najpierw: python scripts/download.py --embed-model")
    KB_DIR.mkdir(parents=True, exist_ok=True)
    GUIDES_DIR.mkdir(parents=True, exist_ok=True)
    client = None if offline else PoliteClient(min_interval=1.0)

    print("QuestieDB (questy, NPC, współrzędne WoW Forever):")
    src = questie.latest_local() if offline else questie.fetch(client)
    extra = questie.latest_local(name="questie") if offline else questie.fetch_addon_lists(client)
    if src is None:
        raise SystemExit("Brak danych QuestieDB w data/kb/raw - uruchom bez --offline")
    tables = questie_tables.load_tables(src)
    support = questie_tables.load_support(src)
    black, events = questie_tables.load_addon_lists(extra)
    world_data = build_world(tables, support, black, events, source=f"QuestieDB {src.name}")
    save_world(world_data, WORLD_PATH)
    world = World(world_data)
    print(f"  {len(world.quests)} questów, {len(world.npcs)} NPC, {len(world.objects)} obiektów")

    pl = load_cache(PL_CACHE)
    if translate:
        print("Questy po polsku (lokalny LLM, nazwy z gry zostają po angielsku; tylko nowe teksty):")
        from ..config import Config

        llm, started = llm_for_build(Config.load())
        try:
            pl = translate_world(world, llm, PL_CACHE, progress=_bar("paczki"))
        finally:
            if started:
                llm.stop()
        print(f"  przetłumaczone: {sum(1 for v in pl.values() if v)} tekstów")

    pages: list[dict] = []
    if wiki:
        print("Warcraft Wiki:")
        w = Wiki(client, RAW_DIR) if client else None
        if w and (replan or not PLAN_PATH.exists()):
            titles = w.plan_titles()
            PLAN_PATH.write_text(json.dumps(titles, ensure_ascii=False, indent=0), encoding="utf-8")
        titles = json.loads(PLAN_PATH.read_text(encoding="utf-8")) if PLAN_PATH.exists() else []
        titles = [t for t in titles if t not in RETAIL_PAGES]
        if w:
            pages = w.fetch_all(titles, refresh=refresh, progress=_bar("strony"))
        else:
            for t in titles:
                path = _cache_path(RAW_DIR, t)
                if path.exists():
                    pages.append(json.loads(path.read_text(encoding="utf-8")))
        seen, unique = set(), []
        for p in pages:  # two planned titles can redirect to the same page
            if p["title"] not in seen:
                seen.add(p["title"])
                unique.append(p)
        pages = unique
        print(f"  {len(pages)} stron")

    if pages:
        instances = extract_instances(pages)
        INSTANCES_PATH.write_text(json.dumps(instances, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"  dungeony i rajdy z poziomami: {len(instances)}")

    guides = list(guide_docs(GUIDES_DIR))
    print(f"Twoje poradniki (data/kb/guides): {len(guides)} plików")

    docs = list(wiki_docs(pages)) + guides + list(quest_docs(world, pl)) + list(service_docs(world))
    print(f"Wektory ({len(docs)} dokumentów, model multilingual-e5-small na CPU):")
    embedder = Embedder()
    from .store import write_kb
    info = write_kb(docs, embedder, DB_PATH, VEC_PATH,
                    meta={"questiedb": src.name, "wiki_pages": len(pages), "guides": len(guides)},
                    progress=_bar("fragmenty"))
    print(f"Gotowe: {info['chunks']} fragmentów z {info['docs']} dokumentów ({info['embed_s']} s) -> {KB_DIR}")
    if client:
        client.close()
    return info


def try_search(question: str) -> None:
    from .embedder import Embedder
    from .store import KnowledgeBase

    kb = KnowledgeBase(embedder=Embedder())
    t = time.perf_counter()
    hits = kb.search(question, k=5)
    print(f"{len(kb)} fragmentów, {1000 * (time.perf_counter() - t):.0f} ms")
    for h in hits:
        print(f"[{h.cosine:.3f} {h.kind}] {h.title} > {h.section}\n    {h.text[:220]!r}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m wvt.kb.build", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--replan", action="store_true")
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--no-wiki", action="store_true")
    ap.add_argument("--no-translate", action="store_true", help="bez polskich wersji questów (nie uruchamia LLM)")
    ap.add_argument("--search", metavar="PYTANIE")
    args = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    if args.search:
        try_search(args.search)
        return 0
    build(offline=args.offline, replan=args.replan, refresh=args.refresh, wiki=not args.no_wiki,
          translate=not args.no_translate)
    return 0


if __name__ == "__main__":
    sys.exit(main())
