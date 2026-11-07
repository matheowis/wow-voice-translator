"""A stable voice for every character.

1. an existing assignment wins (cache, then store);
2. otherwise a slot of the race+gender pool is chosen deterministically from a SHA-256 of
   ``realm|name|race|gender`` (never Python's randomised ``hash()``) and stored atomically;
3. an assignment made while race or sex was unknown is moved once to the right pool when
   they become known - unless ``manuallyOverridden`` is set;
4. changing the pools never changes stored voices; editing a player's ``voiceId`` (and
   setting ``manuallyOverridden``) in the store is the manual override;
5. store errors never block speech: the deterministic choice is used and cached only.
"""

from __future__ import annotations

import hashlib
import logging
import threading
import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from .races import GENDERS, UNKNOWN, gender_of, pool_race
from .store import VoiceProfile

log = logging.getLogger(__name__)


def split_sender(sender: str, default_realm: str) -> tuple[str, str, str]:
    """"Thrall-Forever" -> ("Forever", "Thrall", "thrall")."""
    name, _, realm = (sender or "").strip().partition("-")
    display = name.strip() or "?"
    realm = realm.strip() or default_realm
    return realm, display, unicodedata.normalize("NFKC", display).casefold()


def stable_index(key: str, n: int) -> int:
    return int.from_bytes(hashlib.sha256(key.encode("utf-8")).digest()[:8], "big") % n


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


@dataclass
class Assignment:
    voice_id: str
    realm: str
    name: str
    race: str
    gender: str
    persisted: bool = True
    migrated: bool = False


class VoiceResolver:
    def __init__(self, store, default_realm: str = "Forever", race_aliases: Optional[dict] = None):
        self.store = store
        self.default_realm = default_realm
        self.aliases = dict(race_aliases or {})
        self._cache: dict[tuple[str, str], dict] = {}
        self._pools: dict[tuple[str, str], list[VoiceProfile]] = {}
        self._lock = threading.Lock()
        self.store_errors = 0
        self.reload_profiles()

    # ------------------------------------------------------------------ pools
    def reload_profiles(self) -> None:
        profiles = self._store_call(self.store.load_profiles)
        if profiles is None:
            return
        pools: dict[tuple[str, str], list[VoiceProfile]] = defaultdict(list)
        for p in profiles:
            if p.enabled:
                pools[(p.race, p.gender)].append(p)
        self._pools = {k: sorted(v, key=lambda p: p.slot) for k, v in pools.items()}

    @property
    def profile_count(self) -> int:
        return sum(len(v) for v in self._pools.values())

    def pools(self) -> dict[tuple[str, str], list[VoiceProfile]]:
        return dict(self._pools)

    def _pool_for(self, race: str, gender: str, key: str) -> list[VoiceProfile]:
        g = gender if gender in GENDERS else GENDERS[stable_index(key + "|gender", 2)]
        for k in ((race, g), (UNKNOWN, g)):
            if self._pools.get(k):
                return self._pools[k]
        same_gender = [p for (r, gg), ps in sorted(self._pools.items()) if gg == g for p in ps]
        if same_gender:
            return same_gender
        return [p for _, ps in sorted(self._pools.items()) for p in ps]

    def pick(self, realm: str, norm: str, race: str, gender: str) -> Optional[VoiceProfile]:
        key = f"{realm}|{norm}|{race}|{gender}"
        pool = self._pool_for(race, gender, key)
        if not pool:
            return None
        return pool[stable_index(key, len(pool))]

    # ------------------------------------------------------------------ assignments
    def resolve(self, sender: str, english_race: str, sex: int) -> Optional[Assignment]:
        realm, display, norm = split_sender(sender, self.default_realm)
        race = pool_race(english_race, self.aliases)
        gender = gender_of(sex)
        key = (realm, norm)
        with self._lock:
            doc = self._cache.get(key)
        persisted = True
        if doc is None:
            doc = self._store_call(self.store.get_player, realm, norm)
        if doc is not None:
            if self._needs_migration(doc, race, gender):
                return self._migrate(doc, realm, display, norm, race, gender)
            with self._lock:
                self._cache[key] = doc
            return Assignment(doc["voiceId"], realm, display, doc.get("race", race), doc.get("gender", gender))

        prof = self.pick(realm, norm, race, gender)
        if prof is None:
            return None  # no voices designed yet
        now = _now()
        doc = {"realm": realm, "playerNameNormalized": norm, "playerNameDisplay": display, "race": race,
               "gender": gender, "voiceId": prof.voice_id, "assignmentVersion": 1, "manuallyOverridden": False,
               "createdAt": now, "updatedAt": now}
        stored = self._store_call(self.store.insert_player_if_absent, doc)
        if stored is None:
            persisted = False
        else:
            doc = stored
        with self._lock:
            self._cache[key] = doc
        return Assignment(doc["voiceId"], realm, display, doc["race"], doc["gender"], persisted=persisted)

    @staticmethod
    def _needs_migration(doc: dict, race: str, gender: str) -> bool:
        if doc.get("manuallyOverridden"):
            return False
        return (doc.get("race") == UNKNOWN and race != UNKNOWN) or (doc.get("gender") == UNKNOWN and gender != UNKNOWN)

    def _migrate(self, doc: dict, realm: str, display: str, norm: str, race: str, gender: str) -> Assignment:
        new_race = race if doc.get("race") == UNKNOWN else doc["race"]
        new_gender = gender if doc.get("gender") == UNKNOWN else doc["gender"]
        prof = self.pick(realm, norm, new_race, new_gender)
        version = int(doc.get("assignmentVersion", 1))
        fields = {"race": new_race, "gender": new_gender, "updatedAt": _now(), "assignmentVersion": version + 1}
        if prof is not None:
            fields["voiceId"] = prof.voice_id
        updated = self._store_call(self.store.update_player, realm, norm, fields, version)
        if updated is None:  # store down, or somebody else migrated it first
            fresh = self._store_call(self.store.get_player, realm, norm)
            updated = fresh if fresh is not None else {**doc, **fields}
        with self._lock:
            self._cache[(realm, norm)] = updated
        log.info("voice of a character moved from the unknown pool to %s/%s", new_race, new_gender)
        return Assignment(updated["voiceId"], realm, display, updated["race"], updated["gender"], migrated=True)

    def _store_call(self, fn, *args):
        try:
            return fn(*args)
        except Exception as exc:
            self.store_errors += 1
            if self.store_errors <= 3:
                log.warning("voice store error (%s) - using the deterministic voice without saving", type(exc).__name__)
            return None
