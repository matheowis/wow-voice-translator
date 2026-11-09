"""multilingual-e5-small (ONNX int8, CPU): 384-dim sentence vectors for Polish questions and English text.

e5 wants a role prefix: ``"query: "`` for questions, ``"passage: "`` for indexed text. Vectors are
mean-pooled over real tokens and L2-normalised, so a dot product is the cosine similarity.
CPU on purpose: the GPU is busy with speech recognition and the LLM, and one query takes ~10-20 ms.
"""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np

from . import MODEL_DIR

DIM = 384
MAX_TOKENS = 512


class Embedder:
    def __init__(self, model_dir: Path = MODEL_DIR, threads: int | None = None):
        import onnxruntime as ort
        from tokenizers import Tokenizer

        model = Path(model_dir) / "model_quantized.onnx"
        if not model.exists():
            raise FileNotFoundError(f"{model} - uruchom: python scripts/download.py --embed-model")
        self.tok = Tokenizer.from_file(str(Path(model_dir) / "tokenizer.json"))
        self.tok.enable_truncation(max_length=MAX_TOKENS)
        self.pad_id = self.tok.token_to_id("<pad>") or 1
        so = ort.SessionOptions()
        so.intra_op_num_threads = threads or max(1, min(4, (os.cpu_count() or 4) // 2))
        so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        self.sess = ort.InferenceSession(str(model), so, providers=["CPUExecutionProvider"])
        self.inputs = {i.name for i in self.sess.get_inputs()}

    def _run(self, texts: list[str]) -> np.ndarray:
        enc = self.tok.encode_batch(texts)
        n = max(len(e.ids) for e in enc)
        ids = np.full((len(enc), n), self.pad_id, dtype=np.int64)
        mask = np.zeros((len(enc), n), dtype=np.int64)
        for i, e in enumerate(enc):
            ids[i, : len(e.ids)] = e.ids
            mask[i, : len(e.ids)] = 1
        feed = {"input_ids": ids, "attention_mask": mask}
        if "token_type_ids" in self.inputs:
            feed["token_type_ids"] = np.zeros_like(ids)
        hidden = self.sess.run(None, feed)[0]  # (batch, tokens, 384)
        m = mask[..., None].astype(np.float32)
        vec = (hidden * m).sum(axis=1) / np.maximum(m.sum(axis=1), 1e-9)
        return vec / np.maximum(np.linalg.norm(vec, axis=1, keepdims=True), 1e-12)

    def embed(self, texts: list[str], prefix: str = "passage: ", batch: int = 32, progress=None) -> np.ndarray:
        if not texts:
            return np.zeros((0, DIM), dtype=np.float32)
        order = sorted(range(len(texts)), key=lambda i: len(texts[i]))  # similar lengths share a batch
        out = np.zeros((len(texts), DIM), dtype=np.float32)
        for start in range(0, len(order), batch):
            idx = order[start:start + batch]
            out[idx] = self._run([prefix + texts[i] for i in idx])
            if progress:
                progress(min(start + batch, len(order)), len(order))
        return out

    def query(self, text: str) -> np.ndarray:
        return self.embed([text], prefix="query: ")[0]
