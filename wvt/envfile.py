"""Minimal ``.env`` loader: KEY=VALUE lines from the git-ignored .env next to the project.

Secrets (ELEVENLABS_API_KEY, MONGODB_URI) live only there. Values are put into
``os.environ`` and are never logged; callers may log *which* keys were found.
"""

from __future__ import annotations

import os
from pathlib import Path

from .config import ROOT

ENV_PATH = ROOT / ".env"


def load_env(path: Path = ENV_PATH, override: bool = False) -> list[str]:
    """Load ``path`` into os.environ (existing variables win unless override). Returns the key names."""
    if not path.exists():
        return []
    loaded = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key.startswith("export "):
            key = key[7:].strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key and (override or key not in os.environ):
            os.environ[key] = value
            loaded.append(key)
    return loaded


def secret(name: str) -> str:
    """Value of an environment secret ('' when missing)."""
    return os.environ.get(name, "").strip()
