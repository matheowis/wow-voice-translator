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
        {"key": "mouse5", "channel": "assistant"},
    ],
    # the voice assistant: her own push-to-talk key (channel "assistant"), Polish speech in, a spoken
    # answer and group/chat commands out (prepared in the chat box, Enter runs them) - wvt/assistant.py
    "assistant": {
        "enabled": True,
        "name": "Iskra",
        "default_key": "mouse5",  # added once to push_to_talk for configs older than the assistant
        "default_key_added": False,
        "voice_id": "",  # her ElevenLabs voice - set when you pick one on the voice rating page
        "volume": 1.0,
        "invite_window_s": 300,  # "inv me" requests younger than this are invited
        "llm_timeout_s": 8,
        "max_reply_chars": 240,
        "persona_extra": "",  # added to her character description, e.g. "mów do mnie Mati"
        "auto_waypoint": True,  # answers about places prepare "/wvt way ..." (map waypoint) in the chat box
        "loot_advice": True,  # say "need" or "greed" when a group loot roll starts
        "knowledge_base": True,  # answer game questions from data/kb (python -m wvt.kb.build)
        "kb_min_score": 0.8,  # cosine similarity a guide fragment needs to count as an answer
        "reminders_file": "data/reminders.json",
        "flavor": True,  # her character: varied phrasings and her own words for spontaneous comments
        "quest_tips": True,  # say whether a quest you just took is worth it (chain, XP, rewards)
        "zone_tips": True,  # a word about a zone when you enter it
        "level_tips": True,  # level up: new abilities at the trainer
        "bag_tips": True,  # bags full
        "loot_tips": True,  # an item you loot is better than what you wear: say so
        # "co teraz robić?": how much Gemini thinks about the computed route (low / medium / high); measured: low
        # 4.4 s, medium 12.3 s for equally good plans (the route, chains and what to let go are computed already)
        "plan_thinking": "low",
        "max_knowledge_chars": 480,  # answers from the guides and the quest base may be longer than small talk
        "log_file": "logs/assistant.jsonl",  # what you asked and what she answered (privacy.log_assistant)
        # "gemini": she talks through Google Gemini (key in .env, section "gemini"); translations stay local and
        # the local model answers whenever Gemini fails
        "llm": "local",
    },
    "gemini": {
        "model": "gemini-3.8-flash",
        "thinking_level": "low",  # 3.8 Flash: low | medium | high (thinking cannot be switched off)
        "timeout_s": 15,
        "max_output_tokens": 4096,  # includes thinking tokens; the answer length comes from the prompt
        "api_key_env": "GOOGLE_AI_API_KEY",  # read from the git-ignored .env
    },
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
        "input_gain_db": 0.0,  # fixed boost of the microphone before speech detection (0-30 dB)
        "auto_gain": True,  # lift a quiet recording (up to +30 dB) without lifting the noise above the threshold
    },
    "stt": {
        # Whisper large-v3-turbo, fp16 on CUDA, Polish forced: 10.4 % wrong words on the user's recordings against
        # Parakeet's 12.0 %, and 2.5 GB of VRAM instead of 3.7 (wvt/stt.py); Parakeet takes over when it cannot run
        "model_dir": "models/whisper-large-v3-turbo-onnx",
        "model_name": "whisper",
        "language": "pl",  # Whisper: set on every call, otherwise it guesses the language of each utterance
        # auto: GPU if (after the LLM is loaded) it leaves vram_reserve_mb free for the game, otherwise the fallback
        "device": "auto",  # auto | cuda | cpu (cpu = the fallback model)
        "gpu_quantization": "fp16",
        "gpu_need_mb": 2600,  # measured +2488 MB on RTX 5080
        "cpu_quantization": "int8",
        "vram_reserve_mb": 2500,
        "cpu_fallback": {"model_dir": "models/parakeet-tdt-0.6b-v3-onnx", "model_name": "nemo-parakeet-tdt-0.6b-v3",
                         "cpu_quantization": "int8", "gpu_need_mb": 3800},
        "drop_phrases": None,  # null = wvt.stt.DEFAULT_DROP_PHRASES (Whisper's typical hallucinations)
        # local speech-to-text service for other apps on this PC (127.0.0.1 only): one Whisper in VRAM; 0 = off
        "server_port": 39283,
        # Whisper's prompt (the names of the moment, wvt/lexicon.py) is cut to this many tokens, its start first
        "prompt_max_tokens": 200,
    },
    # the names of the moment (wvt/lexicon.py): your quests, people and places around you, your loot and bags -
    # Whisper's prompt, a sound-alike correction of misheard names, a small vector index, the LLMs' context
    "lexicon": {
        "enabled": True,
        "whisper_prompt": True,  # give Whisper the names (52 -> 62 of 82 names right in the bench)
        "prompt_terms": 60,
        "prompt_lead": "Rozmowa w World of Warcraft.",
        "prompt_words": "",  # fixed words added at the end of Whisper's prompt
        "correct": True,  # "Targasa" -> "Thargas", "gnome Regan" -> "Gnomeregan" before Iskra and the translator
        "vector": True,  # "ten kij, który znalazłem", "quest z trollami": search the entries by meaning
        # whose vectors: "auto" = Google's while Iskra talks through Gemini (same key), else the local e5;
        # "google" / "local" to choose.  Measured: Google 11/12 items and 10/10 quests first, e5-small 8/12 and 5/10
        "embedder": "auto",
        "embedder_model": "gemini-embedding-2",
        "item_min": 0.65,  # Google's score from which an item / a quest description counts as found
        "quest_min": 0.69,
        "translate_names": 20,  # names given to the translator's prompt (0 = none)
        "nearby_yards": 300,
        "nearby_max": 30,
        "user_file": "data/slownik.txt",  # your own words, one per line ("Name = what it is"); data/ is not in git
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
        # what you say is translated together with the last lines of the same conversation
        # (/say, /party, /raid, whispers for /r), so "ok, cześć" at the end of a talk becomes "ok, bye"
        "context_lines": 6,
        "context_seconds": 180,
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
        "log_assistant": False,  # true: questions to the assistant and her answers go to assistant.log_file
        "keep_history": True,  # last 10 PTT translations kept in memory (never on disk)
    },
    "logging": {"level": "INFO", "dir": "logs"},
    # Reading /say and /party aloud with ElevenLabs. Which channels are read is switched
    # IN THE GAME (panel above the chat, /wvt say|party|lang) - off channels cost nothing.
    "voice": {
        "enabled": True,  # master switch of the app side
        "api_key_env": "ELEVENLABS_API_KEY",  # read from the git-ignored .env
        "model_id": "eleven_v4_turbo",
        "output_format": "pcm_24000",
        "sample_rate": 24000,
        "request_timeout_s": 15,
        "max_retries": 2,
        "speak_own_messages": True,  # EN mode: your character repeats your line in its voice
        "volume_say": 1.0,
        "volume_party": 0.9,
        # /say volume by distance in yards (linear between points); beyond the last point = silent
        # and NOT synthesised, so far-away chatter costs nothing
        "distance_curve": [[0, 1.0], [5, 1.0], [10, 0.75], [15, 0.5], [20, 0.25], [30, 0.0]],
        "unknown_distance": "play_quiet",  # play_normal | play_quiet | mute
        "quiet_gain": 0.35,
        "max_chars_per_message": 220,
        "assistant_max_chars": 900,  # Iskra's answers (a plan is up to ~600 characters): never cut mid-sentence
        "daily_char_budget": 30000,  # characters sent to ElevenLabs per day (~0.5 credit each on v4 Turbo)
        "max_queue": 6,
        "max_concurrent": 3,  # characters speaking at the same time (people often write at once)
        "stale_after_s": 20,  # older lines are dropped instead of read late
        "dedup_window_s": 15,  # same sender + same text within this window is read once
        # /say safety valve for crowded places (cities): the nearest player is always read first;
        # when many people talk at once only players within crowd_radius_yd are read, at most
        # crowd_max_per_min lines a minute; a player flooding the chat is muted for a while
        "say_valve": {
            "crowd_window_s": 15,
            "crowd_lines": 6,  # this many /say lines in the window...
            "crowd_speakers": 4,  # ...or this many different players = a crowd
            "crowd_radius_yd": 10,
            "crowd_max_per_min": 6,
            "flood_lines": 3,  # more lines than this from one player within flood_window_s...
            "flood_window_s": 20,
            "flood_mute_s": 30,  # ...and they are not read for this long
        },
        "blocked_players": [],
        "output_device": None,  # sounddevice output name/index, None = Windows default
        "realm": "Forever",  # used when the sender name has no "-Realm" part
        "slots_per_pool": 5,  # voices per race+gender pool (Voice Design)
        "race_aliases": {},  # extra englishRace -> pool mapping, e.g. {"Goblin": "Gnome"}
        "unknown_race_pool": "Human",  # voices for characters whose race the game does not give
        "store": {
            "mongodb_uri_env": "MONGODB_URI",  # empty/missing -> mongodb://127.0.0.1:27017
            "mongodb_default_uri": "mongodb://127.0.0.1:27017",
            "database": "wow_voice_translator",
            "timeout_ms": 1500,
            "fallback_dir": "data",  # local JSON store when MongoDB is not reachable
        },
    },
    # Spoken-English cleanup before ElevenLabs (EN mode): brb -> be right back, oom -> out of mana...
    "normalizer": {
        "enabled": True,
        "mode": "hybrid",  # off | rules | hybrid (rules + local LLM for slang the rules leave ambiguous)
        "timeout_ms": 500,  # LLM slower than this -> the rules result is used
        "max_chars": 220,
        "profanity_preservation": True,
        "cache_size": 500,
    },
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
