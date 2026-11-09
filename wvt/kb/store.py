"""Knowledge base files: SQLite (documents, chunks, FTS5 index) + a float16 vector matrix.

Search is hybrid: cosine similarity over all chunk vectors (numpy, brute force: ~20k chunks in a
few ms) and BM25 over the FTS5 index, merged with reciprocal rank fusion. The best cosine score is
returned too, so the caller can tell "the base knows this" from "nothing relevant".
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

import numpy as np

from . import DB_PATH, VEC_PATH
from .docs import chunk_sections

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE docs(id INTEGER PRIMARY KEY, source TEXT, title TEXT, url TEXT, license TEXT, kind TEXT, ref INTEGER);
CREATE TABLE chunks(id INTEGER PRIMARY KEY, doc_id INTEGER, ord INTEGER, section TEXT, text TEXT);
CREATE VIRTUAL TABLE chunks_fts USING fts5(title, section, text, content='', tokenize='porter unicode61 remove_diacritics 2');
"""
RRF_K = 60
# fused-score multipliers: your own notes first, then pages about WoW Forever / Classic
KIND_BOOST = {"guide": 1.5, "forever": 1.3, "quest": 1.0, "services": 1.0, "wiki": 1.0}
CLASSIC_TITLE_BOOST = 1.15
_STOP = {"the", "a", "an", "of", "in", "on", "to", "and", "or", "is", "are", "where", "what", "how", "who", "which",
         "do", "does", "i", "my", "me", "can", "for", "with", "it", "this", "that", "be", "at", "from", "find", "get"}


def passage_text(title: str, section: str, text: str) -> str:
    head = f"{title} > {section}" if section else title
    return f"{head}\n{text}"


def write_kb(docs: Iterable[dict], embedder, db_path: Path = DB_PATH, vec_path: Path = VEC_PATH,
             meta: Optional[dict] = None, progress=None) -> dict:
    """Chunk, embed and write everything (to temp files, then swap in)."""
    db_tmp, vec_tmp = db_path.with_suffix(".tmp"), vec_path.with_suffix(".tmp.npy")
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_tmp.exists():
        db_tmp.unlink()
    con = sqlite3.connect(db_tmp)
    con.executescript(SCHEMA)
    texts: list[str] = []
    n_docs = 0
    by_kind: dict[str, int] = {}
    for doc_id, d in enumerate(docs, 1):
        con.execute("INSERT INTO docs VALUES (?,?,?,?,?,?,?)",
                    (doc_id, d["source"], d["title"], d.get("url", ""), d.get("license", ""), d.get("kind", ""),
                     d.get("ref")))
        n_docs += 1
        for ord_, (section, text) in enumerate(chunk_sections(d["sections"])):
            if ord_ >= 80:
                break
            cid = len(texts) + 1
            con.execute("INSERT INTO chunks VALUES (?,?,?,?,?)", (cid, doc_id, ord_, section, text))
            con.execute("INSERT INTO chunks_fts(rowid, title, section, text) VALUES (?,?,?,?)",
                        (cid, d["title"], section, text))
            texts.append(passage_text(d["title"], section, text))
            by_kind[d.get("kind", "")] = by_kind.get(d.get("kind", ""), 0) + 1
    t0 = time.time()
    vecs = embedder.embed(texts, prefix="passage: ", progress=progress)
    info = {"docs": n_docs, "chunks": len(texts), "by_kind": by_kind, "embed_s": round(time.time() - t0, 1),
            "built": time.strftime("%Y-%m-%d %H:%M"), **(meta or {})}
    con.execute("INSERT INTO meta VALUES ('info', ?)", (json.dumps(info),))
    con.commit()
    con.close()
    np.save(vec_tmp, vecs.astype(np.float16))
    db_tmp.replace(db_path)
    vec_tmp.replace(vec_path)
    return info


@dataclass
class Hit:
    chunk_id: int
    title: str
    section: str
    text: str
    url: str
    kind: str
    ref: Optional[int]
    score: float  # fused (RRF)
    cosine: float  # best vector similarity of this chunk to the query (0..1)


class KnowledgeBase:
    def __init__(self, db_path: Path = DB_PATH, vec_path: Path = VEC_PATH, embedder=None):
        self.db_path, self.vec_path = Path(db_path), Path(vec_path)
        self.con = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True, check_same_thread=False)
        self.lock = threading.Lock()
        self.vecs = np.load(self.vec_path).astype(np.float32)
        self.embedder = embedder
        row = self.con.execute("SELECT value FROM meta WHERE key='info'").fetchone()
        self.info = json.loads(row[0]) if row else {}

    @staticmethod
    def available(db_path: Path = DB_PATH, vec_path: Path = VEC_PATH) -> bool:
        return Path(db_path).exists() and Path(vec_path).exists()

    def __len__(self) -> int:
        return len(self.vecs)

    def doc_text(self, title: str) -> str:
        """All chunks of one document (e.g. "Mage abilities (Forever)"), in order."""
        with self.lock:
            rows = self.con.execute("SELECT c.text FROM chunks c JOIN docs d ON d.id = c.doc_id WHERE d.title = ? "
                                    "ORDER BY c.ord", (title,)).fetchall()
        return "\n".join(r[0] for r in rows)

    def lead(self, titles: list[str]) -> str:
        """The first chunk of the first of these documents that exists."""
        with self.lock:
            for t in titles:
                row = self.con.execute("SELECT c.text FROM chunks c JOIN docs d ON d.id = c.doc_id WHERE d.title = ? "
                                       "ORDER BY c.ord LIMIT 1", (t,)).fetchone()
                if row:
                    return row[0]
        return ""

    def _fts(self, query: str, limit: int) -> list[int]:
        words = [w for w in re.findall(r"[\w']+", query.lower()) if w not in _STOP and len(w) > 1]
        if not words:
            return []
        expr = " OR ".join('"' + w.replace('"', "") + '"' for w in words[:12])
        try:
            with self.lock:
                rows = self.con.execute(
                    "SELECT rowid FROM chunks_fts WHERE chunks_fts MATCH ? ORDER BY bm25(chunks_fts, 4.0, 2.0, 1.0) "
                    "LIMIT ?", (expr, limit)).fetchall()
        except sqlite3.Error as e:
            log.debug("fts: %s", e)
            return []
        return [r[0] for r in rows]

    def search(self, query: str, query_en: str = "", k: int = 5, kinds: Optional[set[str]] = None,
               pool: int = 50) -> list[Hit]:
        texts = [t for t in dict.fromkeys([query, query_en]) if t]
        if not texts or self.embedder is None or not len(self.vecs):
            return []
        qv = self.embedder.embed(texts, prefix="query: ")
        sims = (self.vecs @ qv.T).max(axis=1)  # best over the Polish and the English wording
        vec_rank = np.argsort(-sims)[:pool]
        fused: dict[int, float] = {}
        for r, i in enumerate(vec_rank):
            fused[int(i) + 1] = fused.get(int(i) + 1, 0.0) + 1.0 / (RRF_K + r)
        for r, cid in enumerate(self._fts(query_en or query, pool)):
            fused[cid] = fused.get(cid, 0.0) + 1.0 / (RRF_K + r)
        hits: list[Hit] = []
        with self.lock:
            for cid, score in fused.items():
                row = self.con.execute(
                    "SELECT c.section, c.text, d.title, d.url, d.kind, d.ref FROM chunks c JOIN docs d ON d.id = c.doc_id "
                    "WHERE c.id = ?", (cid,)).fetchone()
                if not row or (kinds and row[4] not in kinds):
                    continue
                boost = KIND_BOOST.get(row[4], 1.0) * (CLASSIC_TITLE_BOOST if row[2].endswith("(Classic)") else 1.0)
                hits.append(Hit(cid, row[2], row[0], row[1], row[3], row[4], row[5], score * boost,
                                float(sims[cid - 1])))
        hits.sort(key=lambda h: -h.score)
        return hits[:k]
