"""GPU helpers: VRAM usage via NVML and loading CUDA DLLs shipped in pip wheels."""

from __future__ import annotations

import logging
import os
from pathlib import Path

log = logging.getLogger(__name__)


def vram_used_mb() -> dict[str, float] | None:
    """Total / used VRAM of GPU 0 in MB, or None when NVML is unavailable."""
    try:
        import pynvml

        pynvml.nvmlInit()
        try:
            h = pynvml.nvmlDeviceGetHandleByIndex(0)
            mem = pynvml.nvmlDeviceGetMemoryInfo(h)
            name = pynvml.nvmlDeviceGetName(h)
            if isinstance(name, bytes):
                name = name.decode()
            return {"total": mem.total / 2**20, "used": mem.used / 2**20, "name": name}
        finally:
            pynvml.nvmlShutdown()
    except Exception as exc:  # no NVIDIA GPU / driver
        log.debug("NVML unavailable: %s", exc)
        return None


def process_vram_mb(pid: int) -> float | None:
    try:
        import pynvml

        pynvml.nvmlInit()
        try:
            h = pynvml.nvmlDeviceGetHandleByIndex(0)
            total = 0.0
            found = False
            for getter in ("nvmlDeviceGetComputeRunningProcesses", "nvmlDeviceGetGraphicsRunningProcesses"):
                try:
                    for p in getattr(pynvml, getter)(h):
                        if p.pid == pid and p.usedGpuMemory:
                            total += p.usedGpuMemory / 2**20
                            found = True
                except pynvml.NVMLError:
                    pass
            return total if found else None
        finally:
            pynvml.nvmlShutdown()
    except Exception:
        return None


_dlls_loaded = False


def preload_cuda_dlls() -> bool:
    """Make the CUDA/cuDNN DLLs from the nvidia-* wheels visible to onnxruntime."""
    global _dlls_loaded
    if _dlls_loaded:
        return True
    try:
        import onnxruntime as ort

        if hasattr(ort, "preload_dlls"):
            ort.preload_dlls(directory="")
            _dlls_loaded = True
            return True
    except Exception as exc:
        log.debug("ort.preload_dlls failed: %s", exc)
    # fallback: add nvidia/*/bin to the DLL search path
    try:
        import nvidia  # type: ignore

        base = Path(list(nvidia.__path__)[0])
        for bin_dir in base.glob("*/bin"):
            os.add_dll_directory(str(bin_dir))
        _dlls_loaded = True
        return True
    except Exception as exc:
        log.debug("nvidia wheel DLL dirs not found: %s", exc)
    return False
