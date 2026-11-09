"""Voice profiles (race+gender pools) and per-character voice assignments.

MongoDB (the local server by default) is the main store:

* ``voice_profiles`` – ``{voiceId, race, gender, slot, enabled, description, name, rating, locked}``,
  unique ``(race, gender, slot)`` and unique ``voiceId``.  ``rating`` is the user's last score
  of the voice (0 = not rated) and ``locked`` marks a voice rated 5: the voice design tools
  never touch a locked slot, and a pool with five locked slots is closed;
* ``player_voices`` – ``{realm, playerNameNormalized, playerNameDisplay, race, gender, voiceId,
  assignmentVersion, manuallyOverridden, createdAt, updatedAt}``, unique ``(realm, playerNameNormalized)``.

No chat text is ever stored.  When MongoDB cannot be reached the same documents go to JSON
files in ``data/`` (git-ignored), so the app keeps working.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)


@dataclass
class VoiceProfile:
    voice_id: str
    race: str
    gender: str
    slot: int
    enabled: bool = True
    description: str = ""
    name: str = ""
    rating: int = 0  # the user's score 1-5 from the rating page, 0 = not rated yet
    locked: bool = False  # rated 5: final, never redesigned

    def to_doc(self) -> dict:
        return {"voiceId": self.voice_id, "race": self.race, "gender": self.gender, "slot": int(self.slot),
                "enabled": bool(self.enabled), "description": self.description, "name": self.name,
                "rating": int(self.rating), "locked": bool(self.locked)}

    @classmethod
    def from_doc(cls, d: dict) -> "VoiceProfile":
        return cls(voice_id=d["voiceId"], race=d["race"], gender=d["gender"], slot=int(d["slot"]),
                   enabled=bool(d.get("enabled", True)), description=d.get("description", ""), name=d.get("name", ""),
                   rating=int(d.get("rating") or 0), locked=bool(d.get("locked", False)))


class MongoVoiceStore:
    kind = "mongodb"

    def __init__(self, uri: str, database: str, timeout_ms: int = 1500):
        from pymongo import ASCENDING, MongoClient

        self._client = MongoClient(uri, serverSelectionTimeoutMS=timeout_ms, connectTimeoutMS=timeout_ms,
                                   socketTimeoutMS=max(2000, timeout_ms * 2), appname="wow-voice-translator")
        self._client.admin.command("ping")
        db = self._client[database]
        self.profiles = db["voice_profiles"]
        self.players = db["player_voices"]
        self.profiles.create_index([("race", ASCENDING), ("gender", ASCENDING), ("slot", ASCENDING)], unique=True)
        self.profiles.create_index("voiceId", unique=True)
        self.players.create_index([("realm", ASCENDING), ("playerNameNormalized", ASCENDING)], unique=True)
        self.description = f"MongoDB, baza {database}"

    def load_profiles(self) -> list[VoiceProfile]:
        return [VoiceProfile.from_doc(d) for d in self.profiles.find({}, {"_id": 0})]

    def save_profile(self, p: VoiceProfile) -> None:
        self.profiles.update_one({"race": p.race, "gender": p.gender, "slot": p.slot}, {"$set": p.to_doc()}, upsert=True)

    def delete_profile(self, race: str, gender: str, slot: int) -> None:
        self.profiles.delete_one({"race": race, "gender": gender, "slot": slot})

    def get_player(self, realm: str, norm: str) -> Optional[dict]:
        return self.players.find_one({"realm": realm, "playerNameNormalized": norm}, {"_id": 0})

    def insert_player_if_absent(self, doc: dict) -> dict:
        """Atomic: the first writer wins, everybody gets the stored document back."""
        from pymongo import ReturnDocument
        from pymongo.errors import DuplicateKeyError

        flt = {"realm": doc["realm"], "playerNameNormalized": doc["playerNameNormalized"]}
        try:
            return self.players.find_one_and_update(flt, {"$setOnInsert": doc}, upsert=True,
                                                    return_document=ReturnDocument.AFTER, projection={"_id": 0})
        except DuplicateKeyError:  # concurrent upsert of the same key
            return self.get_player(doc["realm"], doc["playerNameNormalized"])

    def update_player(self, realm: str, norm: str, fields: dict, expect_version: Optional[int] = None) -> Optional[dict]:
        from pymongo import ReturnDocument

        flt = {"realm": realm, "playerNameNormalized": norm}
        if expect_version is not None:
            flt["assignmentVersion"] = expect_version
        return self.players.find_one_and_update(flt, {"$set": fields}, return_document=ReturnDocument.AFTER,
                                                projection={"_id": 0})

    def reassign_voice(self, old_voice_id: str, new_voice_id: str) -> int:
        """Characters on a replaced voice move to its successor (hand-set assignments stay)."""
        from datetime import datetime, timezone

        res = self.players.update_many(
            {"voiceId": old_voice_id, "manuallyOverridden": {"$ne": True}},
            {"$set": {"voiceId": new_voice_id, "updatedAt": datetime.now(timezone.utc).isoformat()},
             "$inc": {"assignmentVersion": 1}})
        return int(res.modified_count)

    def close(self) -> None:
        self._client.close()


class JsonVoiceStore:
    """Same interface as MongoVoiceStore, backed by two JSON files."""

    kind = "json"

    def __init__(self, directory: Path):
        self.dir = Path(directory)
        self.dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._profiles_path = self.dir / "voice_profiles.json"
        self._players_path = self.dir / "player_voices.json"
        self._profiles: list[dict] = self._read(self._profiles_path, [])
        self._players: dict[str, dict] = self._read(self._players_path, {})
        self.description = f"plik lokalny {self.dir}"

    @staticmethod
    def _read(path: Path, default):
        try:
            return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default
        except (OSError, ValueError) as exc:
            log.warning("voice store file %s unreadable (%s), starting empty", path.name, exc)
            return default

    @staticmethod
    def _write(path: Path, data) -> None:
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, path)

    @staticmethod
    def _key(realm: str, norm: str) -> str:
        return f"{realm}\x1f{norm}"

    def load_profiles(self) -> list[VoiceProfile]:
        with self._lock:
            return [VoiceProfile.from_doc(d) for d in self._profiles]

    def save_profile(self, p: VoiceProfile) -> None:
        with self._lock:
            self._profiles = [d for d in self._profiles
                              if (d["race"], d["gender"], int(d["slot"])) != (p.race, p.gender, p.slot)]
            self._profiles.append(p.to_doc())
            self._profiles.sort(key=lambda d: (d["race"], d["gender"], int(d["slot"])))
            self._write(self._profiles_path, self._profiles)

    def delete_profile(self, race: str, gender: str, slot: int) -> None:
        with self._lock:
            self._profiles = [d for d in self._profiles
                              if (d["race"], d["gender"], int(d["slot"])) != (race, gender, slot)]
            self._write(self._profiles_path, self._profiles)

    def get_player(self, realm: str, norm: str) -> Optional[dict]:
        with self._lock:
            d = self._players.get(self._key(realm, norm))
            return dict(d) if d else None

    def insert_player_if_absent(self, doc: dict) -> dict:
        with self._lock:
            key = self._key(doc["realm"], doc["playerNameNormalized"])
            if key not in self._players:
                self._players[key] = dict(doc)
                self._write(self._players_path, self._players)
            return dict(self._players[key])

    def update_player(self, realm: str, norm: str, fields: dict, expect_version: Optional[int] = None) -> Optional[dict]:
        with self._lock:
            key = self._key(realm, norm)
            d = self._players.get(key)
            if d is None or (expect_version is not None and d.get("assignmentVersion") != expect_version):
                return None
            d.update(fields)
            self._write(self._players_path, self._players)
            return dict(d)

    def reassign_voice(self, old_voice_id: str, new_voice_id: str) -> int:
        from datetime import datetime, timezone

        with self._lock:
            moved = 0
            for d in self._players.values():
                if d.get("voiceId") == old_voice_id and not d.get("manuallyOverridden"):
                    d["voiceId"] = new_voice_id
                    d["assignmentVersion"] = int(d.get("assignmentVersion", 1)) + 1
                    d["updatedAt"] = datetime.now(timezone.utc).isoformat()
                    moved += 1
            if moved:
                self._write(self._players_path, self._players)
            return moved

    def close(self) -> None:
        pass


def open_store(store_cfg: dict, fallback_dir: Path):
    """MongoDB when reachable, otherwise the local JSON store. The URI is never logged."""
    uri = os.environ.get(store_cfg.get("mongodb_uri_env", "MONGODB_URI"), "").strip() \
        or store_cfg.get("mongodb_default_uri", "mongodb://127.0.0.1:27017")
    try:
        store = MongoVoiceStore(uri, store_cfg.get("database", "wow_voice_translator"),
                                int(store_cfg.get("timeout_ms", 1500)))
        log.info("voice store: %s", store.description)
        return store
    except Exception as exc:  # ImportError, ServerSelectionTimeoutError, auth errors...
        log.warning("MongoDB unavailable (%s) - voice assignments go to %s", type(exc).__name__, fallback_dir)
        return JsonVoiceStore(fallback_dir)
