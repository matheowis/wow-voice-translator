"""Speech-to-text: NVIDIA Parakeet TDT 0.6B v3 (multilingual, incl. Polish) via onnx-asr.

Runtime choice: the official NeMo toolkit is heavy and poorly supported on Windows,
so we run the ONNX export (istupakov/parakeet-tdt-0.6b-v3-onnx, CC-BY-4.0) with
onnxruntime-gpu (CUDA EP) and fall back to the int8 model on CPU.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

import numpy as np

from .config import resolve_path
from .gpu import preload_cuda_dlls

log = logging.getLogger(__name__)


class SpeechToText:
    # measured on RTX 5080 with the options below: CUDA session needs ~3.7 GB of VRAM
    GPU_NEED_MB = 3800

    def __init__(self, model_dir: str, model_name: str = "nemo-parakeet-tdt-0.6b-v3", device: str = "auto",
                 cpu_quantization: str | None = "int8", vram_reserve_mb: int = 2500):
        self.model_dir = resolve_path(model_dir)
        self.model_name = model_name
        self.device_pref = device
        self.cpu_quantization = cpu_quantization
        self.vram_reserve_mb = vram_reserve_mb
        self.model = None
        self.device = "none"
        self.device_reason = ""
        self.load_seconds = 0.0

    def _gpu_has_room(self) -> bool:
        """auto mode: use the GPU only if it leaves `vram_reserve_mb` free for the game."""
        from .gpu import vram_used_mb

        v = vram_used_mb()
        if v is None:
            self.device_reason = "brak danych NVML"
            return True
        free = v["total"] - v["used"]
        ok = free >= self.GPU_NEED_MB + self.vram_reserve_mb
        self.device_reason = (f"wolne VRAM {free:.0f} MB {'≥' if ok else '<'} "
                              f"{self.GPU_NEED_MB} (STT) + {self.vram_reserve_mb} (zapas dla gry)")
        return ok

    def load(self) -> None:
        import onnx_asr
        import onnxruntime as ort

        t0 = time.perf_counter()
        ort.set_default_logger_severity(3)  # hide harmless "Memcpy nodes added" warnings
        errors = []
        candidates: list[tuple[str, list, str | None]] = []
        use_gpu = self.device_pref == "cuda" or (self.device_pref == "auto" and self._gpu_has_room())
        if self.device_pref == "cpu":
            self.device_reason = "ustawione na CPU"
        if use_gpu:
            preload_cuda_dlls()
            if "CUDAExecutionProvider" in ort.get_available_providers():
                # measured on RTX 5080: default options = +4.5 GB VRAM, 15 s utterance 866 ms
                # (cuDNN exhaustive search per new shape); these = +3.8 GB, 134 ms, 7 s load
                cuda_opts = {"arena_extend_strategy": "kSameAsRequested", "cudnn_conv_algo_search": "HEURISTIC"}
                candidates.append(("cuda", [("CUDAExecutionProvider", cuda_opts), "CPUExecutionProvider"], None))
        # CPU (int8) is always the last resort, also when the GPU session fails to start
        quant = self.cpu_quantization if self._has_quantized() else None
        candidates.append(("cpu", ["CPUExecutionProvider"], quant))

        for device, providers, quant in candidates:
            try:
                model = onnx_asr.load_model(self.model_name, path=str(self.model_dir), quantization=quant,
                                            providers=providers)
                # make sure CUDA really works (falls back silently otherwise)
                model.recognize(np.zeros(16000, dtype=np.float32), sample_rate=16000)
                self.model = model
                self.device = device if quant is None else f"{device}-{quant}"
                break
            except Exception as exc:
                errors.append(f"{device}: {exc}")
                log.warning("STT load on %s failed: %s", device, exc)
        if self.model is None:
            raise RuntimeError("Nie udało się załadować Parakeet: " + " | ".join(errors))
        self.load_seconds = time.perf_counter() - t0
        log.info("STT ready on %s in %.1fs (%s)", self.device, self.load_seconds, self.device_reason)

    def _has_quantized(self) -> bool:
        return (Path(self.model_dir) / "encoder-model.int8.onnx").exists()

    def warmup(self) -> None:
        if self.model is not None:
            self.model.recognize(np.zeros(16000 * 2, dtype=np.float32), sample_rate=16000)

    def transcribe(self, audio: np.ndarray, sample_rate: int = 16000) -> str:
        if self.model is None:
            raise RuntimeError("STT not loaded")
        if audio.size == 0:
            return ""
        text = self.model.recognize(audio.astype(np.float32, copy=False), sample_rate=sample_rate)
        if isinstance(text, (list, tuple)):
            text = " ".join(str(t) for t in text)
        return str(text).strip()
