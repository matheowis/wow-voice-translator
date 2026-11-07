"""Local LLM access through llama.cpp's OpenAI-compatible server.

Two modes:
  * "spawn"    – the app starts runtime/llama.cpp/llama-server.exe itself, bound to
                 127.0.0.1 only, with the GGUF from the config; the process is put in a
                 Windows job object so it dies together with the app.
  * "external" – use a server you already run (llama-server, LM Studio, Ollama's
                 OpenAI endpoint ...) at ``llm.base_url``.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import logging
import subprocess
import sys
import time
from pathlib import Path

import httpx

from .config import ROOT, resolve_path

log = logging.getLogger(__name__)

CREATE_NO_WINDOW = 0x08000000


class _Job:
    """Windows job object with KILL_ON_JOB_CLOSE so the server never outlives us."""

    def __init__(self) -> None:
        self.handle = None
        if sys.platform != "win32":
            return
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        k.CreateJobObjectW.restype = wt.HANDLE
        k.CreateJobObjectW.argtypes = [ctypes.c_void_p, wt.LPCWSTR]
        k.SetInformationJobObject.argtypes = [wt.HANDLE, ctypes.c_int, ctypes.c_void_p, wt.DWORD]
        k.AssignProcessToJobObject.argtypes = [wt.HANDLE, wt.HANDLE]
        k.OpenProcess.restype = wt.HANDLE
        k.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]

        class IO_COUNTERS(ctypes.Structure):
            _fields_ = [(n, ctypes.c_ulonglong) for n in ("r", "w", "o", "rb", "wb", "ob")]

        class BASIC(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_longlong),
                ("PerJobUserTimeLimit", ctypes.c_longlong),
                ("LimitFlags", wt.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wt.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wt.DWORD),
                ("SchedulingClass", wt.DWORD),
            ]

        class EXTENDED(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", BASIC),
                ("IoInfo", IO_COUNTERS),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        self._k = k
        h = k.CreateJobObjectW(None, None)
        if not h:
            return
        info = EXTENDED()
        info.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if k.SetInformationJobObject(h, 9, ctypes.byref(info), ctypes.sizeof(info)):
            self.handle = h

    def assign(self, pid: int) -> None:
        if not self.handle:
            return
        hp = self._k.OpenProcess(0x0100 | 0x0001, False, pid)  # SET_QUOTA | TERMINATE
        if hp:
            self._k.AssignProcessToJobObject(self.handle, hp)


class LLMError(RuntimeError):
    pass


class LocalLLM:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.mode = cfg.get("mode", "spawn")
        self.base_url = cfg.get("base_url") or f"http://{cfg.get('host', '127.0.0.1')}:{cfg.get('port', 39281)}"
        self.proc: subprocess.Popen | None = None
        self._job: _Job | None = None
        self._log_fh = None
        self.client = httpx.Client(base_url=self.base_url, timeout=cfg.get("request_timeout_s", 30))
        self.model_name = Path(cfg.get("model_path", "")).name
        self.load_seconds = 0.0

    # ------------------------------------------------------------------ server
    def start(self) -> None:
        if self.mode == "external":
            self._wait_ready(self.cfg.get("startup_timeout_s", 60))
            return
        exe = resolve_path(self.cfg["server_exe"])
        model = resolve_path(self.cfg["model_path"])
        if not exe.exists():
            raise LLMError(f"Brak llama-server: {exe} (uruchom scripts/setup.ps1)")
        if not model.exists():
            raise LLMError(f"Brak modelu GGUF: {model}")
        args = [
            str(exe),
            "-m", str(model),
            "--host", self.cfg.get("host", "127.0.0.1"),
            "--port", str(self.cfg.get("port", 39281)),
            "-c", str(self.cfg.get("context", 4096)),
            "-np", str(self.cfg.get("parallel", 2)),
            "-ngl", str(self.cfg.get("gpu_layers", 999)),
            "--no-webui",
            "--jinja",
        ]
        if self.cfg.get("disable_thinking", True):
            args += ["--reasoning", "off"]  # translation needs no chain-of-thought
        args += [str(a) for a in self.cfg.get("extra_args", [])]
        log_dir = ROOT / "logs"
        log_dir.mkdir(exist_ok=True)
        self._log_fh = open(log_dir / "llama-server.log", "w", encoding="utf-8", errors="replace")
        t0 = time.perf_counter()
        log.info("Starting llama-server: %s", " ".join(args))
        self.proc = subprocess.Popen(
            args,
            stdout=self._log_fh,
            stderr=subprocess.STDOUT,
            cwd=str(exe.parent),
            creationflags=CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        self._job = _Job()
        self._job.assign(self.proc.pid)
        self._wait_ready(self.cfg.get("startup_timeout_s", 180))
        self.load_seconds = time.perf_counter() - t0
        log.info("llama-server ready in %.1fs (%s)", self.load_seconds, self.model_name)

    def _wait_ready(self, timeout: float) -> None:
        end = time.monotonic() + timeout
        last = ""
        while time.monotonic() < end:
            if self.proc is not None and self.proc.poll() is not None:
                raise LLMError(f"llama-server zakończył się (kod {self.proc.returncode}) – zobacz logs/llama-server.log")
            try:
                r = self.client.get("/health", timeout=2)
                if r.status_code == 200:
                    return
                last = f"HTTP {r.status_code}"
            except httpx.HTTPError as exc:
                last = str(exc)
            time.sleep(0.3)
        raise LLMError(f"llama-server nie odpowiada ({last})")

    def stop(self) -> None:
        if self.proc is not None:
            self.proc.terminate()
            try:
                self.proc.wait(5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
            self.proc = None
        if self._log_fh:
            self._log_fh.close()
            self._log_fh = None

    @property
    def pid(self) -> int | None:
        return self.proc.pid if self.proc else None

    # ------------------------------------------------------------------ inference
    def chat(self, messages: list[dict], max_tokens: int = 200, temperature: float = 0.0,
             stop: list[str] | None = None, timeout: float | None = None) -> tuple[str, dict]:
        body: dict = {
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "top_p": 1.0,
            "cache_prompt": True,
            "stream": False,
        }
        if temperature == 0:
            body["top_k"] = 1
        if stop:
            body["stop"] = stop
        if self.cfg.get("disable_thinking", True):
            body["chat_template_kwargs"] = {"enable_thinking": False}
            body["reasoning_format"] = "deepseek"
        t0 = time.perf_counter()
        try:
            if timeout is None:
                r = self.client.post("/v1/chat/completions", json=body)
            else:
                r = self.client.post("/v1/chat/completions", json=body, timeout=timeout)
        except httpx.HTTPError as exc:
            raise LLMError(f"LLM niedostępny: {exc}") from exc
        if r.status_code != 200:
            raise LLMError(f"LLM HTTP {r.status_code}: {r.text[:300]}")
        data = r.json()
        msg = data["choices"][0]["message"]
        content = msg.get("content") or ""
        timings = data.get("timings", {})
        info = {
            "seconds": time.perf_counter() - t0,
            "prompt_tokens": data.get("usage", {}).get("prompt_tokens"),
            "completion_tokens": data.get("usage", {}).get("completion_tokens"),
            "predicted_per_second": timings.get("predicted_per_second"),
            "reasoning_chars": len(msg.get("reasoning_content") or ""),
        }
        return content, info
