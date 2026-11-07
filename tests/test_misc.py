import json
from pathlib import Path

import numpy as np
import pytest

from wvt import protocol as P
from wvt.audio import analyze, resample, trim_and_normalize
from wvt.config import DEFAULTS, Config, _deep_merge
from wvt.hotkeys import keyspec_to_config, parse_key
from wvt.signal_reader import HoverStateMachine


# ----------------------------------------------------------------- hotkeys
@pytest.mark.parametrize(
    "name,kind,code",
    [("caps_lock", "key", 0x14), ("`", "key", 0xC0), ("f9", "key", 0x78), ("mouse5", "mouse", "x2"),
     ("mouse4", "mouse", "x1"), ("x", "key", ord("X")), ("numpad3", "key", 0x63)],
)
def test_parse_key(name, kind, code):
    spec = parse_key(name)
    assert spec.kind == kind and spec.code == code


def test_parse_key_rejects_garbage():
    with pytest.raises(ValueError):
        parse_key("definitely-not-a-key")


def test_keyspec_roundtrip():
    for name in ("caps_lock", "f10", "mouse5", "q", "numpad7", "`"):
        assert parse_key(keyspec_to_config(parse_key(name))).code == parse_key(name).code


def test_default_hotkeys_are_distinct():
    keys = [parse_key(b["key"]) for b in DEFAULTS["push_to_talk"]]
    assert len({(k.kind, k.code) for k in keys}) == len(keys)
    assert {b["channel"] for b in DEFAULTS["push_to_talk"]} == {"say", "party"}


# ----------------------------------------------------------------- config
def test_config_deep_merge_and_save(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"audio": {"silence_rms_threshold": 0.01}}), encoding="utf-8")
    cfg = Config.load(path)
    assert cfg.get("audio.silence_rms_threshold") == 0.01
    assert cfg.get("audio.sample_rate") == 16000  # default kept
    cfg.set("hover.enabled", False)
    cfg.save()
    again = Config.load(path)
    assert again.get("hover.enabled") is False
    assert _deep_merge({"a": {"b": 1, "c": 2}}, {"a": {"b": 3}}) == {"a": {"b": 3, "c": 2}}


# ----------------------------------------------------------------- audio
def test_resample_48k_to_16k_keeps_tone():
    sr = 48000
    t = np.arange(sr) / sr
    x = np.sin(2 * np.pi * 440 * t).astype(np.float32)
    y = resample(x, sr, 16000)
    assert len(y) == 16000
    spec = np.abs(np.fft.rfft(y))
    assert abs(np.argmax(spec) - 440) <= 2


def test_silence_detection_and_trim():
    sr = 16000
    silence = np.zeros(sr, dtype=np.float32)
    speech = (0.05 * np.sin(2 * np.pi * 200 * np.arange(sr) / sr)).astype(np.float32)
    x = np.concatenate([silence, speech, silence])
    st = analyze(x, 0.006)
    assert 0.9 <= st.speech_seconds <= 1.1
    y = trim_and_normalize(x, 0.006)
    assert len(y) < len(x) * 0.6
    assert np.max(np.abs(y)) > 0.05  # gained up
    assert analyze(silence, 0.006).speech_seconds == 0


# ----------------------------------------------------------------- hover state machine
def test_hover_state_machine():
    events = []
    m = HoverStateMachine(lambda p: events.append(("start", p.text)), lambda: events.append(("stop",)), misses_to_stop=2)
    hover = P.Packet(type=P.TYPE_HOVER, seq=5, text="hi")
    keep = P.Packet(type=P.TYPE_KEEP, seq=5)
    m.feed(hover)
    m.feed(hover)
    m.feed(keep)
    m.feed(None)  # one glitch frame is tolerated
    m.feed(keep)
    m.feed(None)
    m.feed(None)
    assert events == [("start", "hi"), ("stop",)]
    m.feed(P.Packet(type=P.TYPE_KEEP, seq=9))  # keep for an unknown line: ignored
    assert events[-1] == ("stop",)


def test_wow_root_from_flavor_folder():
    from wvt.config import wow_root_from

    assert wow_root_from(r"E:\games\World of Warcraft\_forever_" + "\\") == Path(r"E:\games\World of Warcraft")
    assert wow_root_from("D:/Gry/World of Warcraft") == Path("D:/Gry/World of Warcraft")


def test_addon_dir_from_configured_wow_folder(tmp_path):
    from wvt.config import Config

    (tmp_path / "_forever_").mkdir()
    cfg = Config({"wow": {"install_dir": str(tmp_path / "_forever_"), "flavor_dir": "_forever_"}}, tmp_path / "c.json")
    assert cfg.addon_dir == tmp_path / "_forever_" / "Interface" / "AddOns"


def test_install_addon_with_wow_option_saves_the_folder(tmp_path):
    from wvt.cli import cmd_install_addon

    (tmp_path / "wow" / "_forever_").mkdir(parents=True)
    cfg = Config(_deep_merge(DEFAULTS, {}), tmp_path / "config.json")

    class Args:
        wow = str(tmp_path / "wow")

    assert cmd_install_addon(cfg, Args) == 0
    toc = tmp_path / "wow" / "_forever_" / "Interface" / "AddOns" / "WoWVoiceTranslator" / "WoWVoiceTranslator.toc"
    assert toc.exists()
    assert Config.load(tmp_path / "config.json").get("wow.install_dir") == str(tmp_path / "wow").replace("\\", "/")
