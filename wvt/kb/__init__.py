"""The assistant's local knowledge base: guides and game data from the web, searched on this PC.

- ``python -m wvt.kb.build`` downloads the sources (Warcraft Wiki pages, QuestieDB data for
  WoW Forever, your own notes in ``data/kb/guides``), cuts them into chunks, embeds them with
  multilingual-e5-small (ONNX, CPU) and writes ``data/kb/``.
- :class:`wvt.kb.search.KnowledgeBase` answers a query with a hybrid search: vectors (cosine,
  brute force in numpy) + SQLite FTS5 (BM25), fused with reciprocal rank fusion.
- :mod:`wvt.kb.world` holds the structured part (quests, NPCs, coordinates) used by the
  "where should I go" advisor and by map waypoints.

Downloaded files are data only: Lua tables are parsed (:mod:`wvt.kb.luadata`), never run.
"""

from __future__ import annotations

from pathlib import Path

from ..config import ROOT

KB_DIR = ROOT / "data" / "kb"
RAW_DIR = KB_DIR / "raw"            # downloads (untrusted data), re-used on rebuilds
GUIDES_DIR = KB_DIR / "guides"      # your own .md / .txt notes and guides
DB_PATH = KB_DIR / "kb.sqlite"      # chunks + FTS5 index + doc metadata
VEC_PATH = KB_DIR / "vectors.npy"   # float16 (n_chunks, 384), row i = chunk id i + 1
WORLD_PATH = KB_DIR / "world.json.gz"
INSTANCES_PATH = KB_DIR / "instances.json"  # dungeons and raids with level ranges (wiki infoboxes)
MODEL_DIR = ROOT / "models" / "embed" / "multilingual-e5-small"


def kb_dir(base: Path | None = None) -> Path:
    return Path(base) if base else KB_DIR
