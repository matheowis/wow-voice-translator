"""JSON configuration with defaults (deep-merged), stored next to the project root."""

from __future__ import annotations

import copy
import json
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = Path(os.environ.get("WVT_CONFIG", ROOT / "config.json"))

DEFAULTS: dict[str, Any] = {
    "wow": {
        # empty = find it (Battle.net registry entry, then common folders); or e.g. "D:/Games/World of Warcraft"
        "install_dir": "",
        "flavor_dir": "_forever_",
        "process_names": ["Wow.exe", "WowT.exe", "WowB.exe", "WowClassic.exe", "Wow-64.exe"],
        # push-to-talk only reacts while WoW is the foreground window
        "only_when_wow_active": True,
    },
    # push-to-talk bindings: hold the key, speak Polish, release -> English text lands
    # in the WoW chat input with the channel prefix. Nothing is ever sent automatically.
    "push_to_talk": [
        {"key": "caps_lock", "channel": "party"},
        {"key": "`", "channel": "say"},
    ],
    "channels": {
        "say": "/s",
        "party": "/p",
        "raid": "/ra",
        "instance": "/i",
        "guild": "/g",
        "yell": "/y",
        "reply": "/r",
    },
    "audio": {
        "device": None,  # None = Windows default input; otherwise device name or index
        "sample_rate": 16000,
        "mic_always_open": True,  # keeps a 0.3 s pre-roll so the first syllable is not cut
        "pre_roll_ms": 300,
        "min_speech_seconds": 0.35,
        "max_record_seconds": 30,
        "silence_rms_threshold": 0.006,  # below this the recording counts as silence
    },
    "stt": {
        "model_dir": "models/parakeet-tdt-0.6b-v3-onnx",
        "model_name": "nemo-parakeet-tdt-0.6b-v3",
        # auto: GPU if (after the LLM is loaded) it leaves vram_reserve_mb free for the game,
        # otherwise CPU int8 (measured: 160 ms vs 80 ms per utterance, same WER on clean speech)
        "device": "auto",  # auto | cuda | cpu
        "cpu_quantization": "int8",  # used only when running on CPU
        "vram_reserve_mb": 2500,
    },
    "llm": {
        "server_exe": "runtime/llama.cpp/llama-server.exe",
        "model_path": "models/llm/NVIDIA-Nemotron-3.5-Bolt-E4B-UD-Q4_K_XL.gguf",
        "host": "127.0.0.1",
        "port": 39281,
        "context": 4096,
        "parallel": 2,
        "gpu_layers": 999,
        "extra_args": [],
        "disable_thinking": True,
        "request_timeout_s": 30,
        "startup_timeout_s": 180,
    },
    "translation": {
        "max_chat_chars": 255,
        "temperature": 0.0,
        "glossary_file": "wvt/data/glossary.json",
        "cache_size": 500,
    },
    "insert": {
        # slash: press "/" (opens chat with "/") then paste "p text"  -> safest
        # enter: press Enter then paste "/p text"
        # clipboard: only copy, you paste yourself
        "method": "slash",
        "restore_clipboard": False,
        "key_delay_ms": 60,
    },
    "hover": {
        "enabled": True,
        "poll_hz": 25,
        "font_family": "Arial Narrow",
        "font_scale": 1.0,
        "background_opacity": 0.9,
        "translate_system_messages": True,
    },
    "hud": {
        "enabled": True,
        "show_seconds": 6,
        "position": "top_center",  # top_center | bottom_center
    },
    "privacy": {
        "log_texts": False,  # transcripts/translations are not written to logs by default
        "keep_history": True,  # last 10 PTT translations kept in memory (never on disk)
    },
    "logging": {"level": "INFO", "dir": "logs"},
}


def _deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def resolve_path(p: str | os.PathLike) -> Path:
    path = Path(p)
    return path if path.is_absolute() else (ROOT / path)


def wow_root_from(path: str | os.PathLike) -> Path:
    """`...\\World of Warcraft\\_forever_\\` (a flavour folder) -> `...\\World of Warcraft`."""
    p = Path(path)
    return p.parent if p.name.startswith("_") and p.name.endswith("_") and len(p.name) > 2 else p


def detect_wow_dir(flavor_dir: str = "_forever_") -> Path | None:
    """The World of Warcraft folder that holds `flavor_dir`, or None.

    Battle.net writes the folder of the last started WoW flavour to the registry
    (HKLM\\SOFTWARE\\WOW6432Node\\Blizzard Entertainment\\World of Warcraft, InstallPath);
    common install folders are the fallback.
    """
    candidates: list[Path] = []
    if sys.platform == "win32":
        import winreg

        for view in (winreg.KEY_WOW64_32KEY, winreg.KEY_WOW64_64KEY):
            try:
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Blizzard Entertainment\World of Warcraft",
                                    0, winreg.KEY_READ | view) as key:
                    candidates.append(wow_root_from(winreg.QueryValueEx(key, "InstallPath")[0]))
            except OSError:
                continue
    for drive in "CDEFGH":
        for sub in ("Program Files (x86)", "Program Files", "", "Games", "Gry"):
            candidates.append(Path(f"{drive}:/") / sub / "World of Warcraft")
    for c in candidates:
        try:
            if (c / flavor_dir).is_dir():
                return c
        except OSError:
            continue
    return None


class Config:
    def __init__(self, data: dict[str, Any], path: Path = CONFIG_PATH):
        self.data = data
        self.path = path

    @classmethod
    def load(cls, path: Path | None = None) -> "Config":
        path = Path(path or CONFIG_PATH)
        user: dict[str, Any] = {}
        if path.exists():
            with open(path, "r", encoding="utf-8") as fh:
                user = json.load(fh)
        cfg = cls(_deep_merge(DEFAULTS, user), path)
        if not path.exists():
            cfg.save()
        return cfg

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self.data, fh, indent=2, ensure_ascii=False)
        os.replace(tmp, self.path)

    def __getitem__(self, key: str) -> Any:
        return self.data[key]

    def get(self, dotted: str, default: Any = None) -> Any:
        cur: Any = self.data
        for part in dotted.split("."):
            if not isinstance(cur, dict) or part not in cur:
                return default
            cur = cur[part]
        return cur

    def set(self, dotted: str, value: Any) -> None:
        parts = dotted.split(".")
        cur = self.data
        for part in parts[:-1]:
            cur = cur.setdefault(part, {})
        cur[parts[-1]] = value

    @property
    def wow_dir(self) -> Path | None:
        """`wow.install_dir`, or the detected WoW folder when that setting is empty."""
        configured = self.get("wow.install_dir")
        if configured:
            return wow_root_from(configured)
        return detect_wow_dir(self.get("wow.flavor_dir", "_forever_"))

    @property
    def addon_dir(self) -> Path:
        root = self.wow_dir
        if root is None:
            raise FileNotFoundError(
                "Nie znaleziono folderu World of Warcraft. Podaj go ręcznie: "
                '.venv\\Scripts\\python -m wvt.cli install-addon --wow "D:\\Gry\\World of Warcraft"')
        return root / self.get("wow.flavor_dir") / "Interface" / "AddOns"
