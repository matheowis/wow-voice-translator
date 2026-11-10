"""Console prototype (spec stage 1): STT / translation / benchmark / addon install.

    python -m wvt.cli check
    python -m wvt.cli stt tests/audio/pl_01.wav
    python -m wvt.cli translate "Siema, idziemy na Deadmines?" --dir pl-en
    python -m wvt.cli mic --seconds 5 --channel party
    python -m wvt.cli bench [--model models/llm/xxx.gguf] [--no-llm] [--no-stt]
    python -m wvt.cli install-addon [--wow "D:/Games/World of Warcraft"]
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import statistics
import sys
import time
import wave
from pathlib import Path

import numpy as np

from .config import ROOT, Config, resolve_path, wow_root_from
from .logging_setup import setup_logging


# --------------------------------------------------------------------------- helpers

def load_wav(path: str | Path) -> np.ndarray:
    from .audio import resample

    with wave.open(str(path), "rb") as w:
        sr = w.getframerate()
        ch = w.getnchannels()
        width = w.getsampwidth()
        raw = w.readframes(w.getnframes())
    if width != 2:
        raise ValueError("only 16-bit PCM WAV supported")
    x = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    if ch > 1:
        x = x.reshape(-1, ch).mean(axis=1)
    return resample(x, sr)


def normalize_words(text: str) -> list[str]:
    text = text.lower()
    text = re.sub(r"[^\w\s']", " ", text)
    return text.split()


def wer(ref: str, hyp: str) -> float:
    r, h = normalize_words(ref), normalize_words(hyp)
    if not r:
        return 0.0 if not h else 1.0
    d = list(range(len(h) + 1))
    for i in range(1, len(r) + 1):
        prev, d[0] = d[0], i
        for j in range(1, len(h) + 1):
            cur = d[j]
            d[j] = min(d[j] + 1, d[j - 1] + 1, prev + (r[i - 1] != h[j - 1]))
            prev = cur
    return d[len(h)] / len(r)


def add_noise(x: np.ndarray, snr_db: float, seed: int = 0) -> np.ndarray:
    """Pink-ish noise + a low hum, roughly like game music/ambience behind a voice."""
    rng = np.random.default_rng(seed)
    white = rng.standard_normal(len(x)).astype(np.float32)
    spec = np.fft.rfft(white)
    f = np.arange(len(spec), dtype=np.float32)
    f[0] = 1.0
    pink = np.fft.irfft(spec / np.sqrt(f), n=len(x)).astype(np.float32)
    t = np.arange(len(x)) / 16000.0
    noise = pink / (np.std(pink) + 1e-9) + 0.5 * np.sin(2 * np.pi * 110 * t).astype(np.float32)
    sig_p = np.mean(x**2) + 1e-12
    noise *= np.sqrt(sig_p / (10 ** (snr_db / 10)) / (np.mean(noise**2) + 1e-12))
    return np.clip(x + noise, -1, 1).astype(np.float32)


def pct(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    v = sorted(values)
    k = min(len(v) - 1, max(0, int(round(q * (len(v) - 1)))))
    return v[k]


def _vram() -> dict | None:
    from .gpu import vram_used_mb

    return vram_used_mb()


def make_llm(cfg: Config, model: str | None = None):
    from .llm import LocalLLM

    llm_cfg = dict(cfg["llm"])
    if model:
        llm_cfg["model_path"] = model
    return LocalLLM(llm_cfg)


def make_stt(cfg: Config):
    from .stt import SpeechToText

    return SpeechToText.from_config(cfg["stt"])


# --------------------------------------------------------------------------- commands

def cmd_check(cfg: Config, _args) -> int:
    print("Python:", sys.version.split()[0])
    v = _vram()
    print("GPU:", v["name"] if v else "brak NVML / GPU", f"| VRAM użyte {v['used']:.0f}/{v['total']:.0f} MB" if v else "")
    try:
        import onnxruntime as ort
        from .gpu import preload_cuda_dlls

        preload_cuda_dlls()
        print("onnxruntime:", ort.__version__, ort.get_available_providers())
    except Exception as exc:
        print("onnxruntime: BŁĄD", exc)
    for label, p in (("STT", cfg.get("stt.model_dir")), ("STT zapas", cfg.get("stt.cpu_fallback.model_dir")),
                     ("llama-server", cfg.get("llm.server_exe")), ("Model LLM", cfg.get("llm.model_path"))):
        if not p:
            continue
        path = resolve_path(p)
        print(f"{label}: {'OK' if path.exists() else 'BRAK'} – {path}")
    try:
        addon = cfg.addon_dir / "WoWVoiceTranslator" / "WoWVoiceTranslator.toc"
        print(f"Addon w WoW: {'OK' if addon.exists() else 'BRAK'} – {addon}")
    except FileNotFoundError as exc:
        print("Addon w WoW: BRAK –", exc)
    try:
        from .audio import AudioRecorder

        print("Mikrofony:", AudioRecorder.list_input_devices())
    except Exception as exc:
        print("Mikrofony: BŁĄD", exc)
    return 0


def cmd_stt(cfg: Config, args) -> int:
    stt = make_stt(cfg)
    stt.load()
    print(f"STT: {stt.describe()}, ładowanie {stt.load_seconds:.1f}s")
    for f in args.files:
        x = load_wav(f)
        t0 = time.perf_counter()
        text = stt.transcribe(x)
        print(f"[{(time.perf_counter() - t0) * 1000:6.0f} ms] {Path(f).name}: {text}")
    return 0


def cmd_translate(cfg: Config, args) -> int:
    from .translator import Translator

    llm = make_llm(cfg, args.model)
    llm.start()
    try:
        tr = Translator(llm, cfg["translation"])
        res = tr.translate(args.text, args.dir)
        print(res.text)
        print(f"[{res.seconds * 1000:.0f} ms] {res.info}")
    finally:
        llm.stop()
    return 0


def cmd_mic(cfg: Config, args) -> int:
    from .audio import AudioRecorder, analyze, trim_and_normalize
    from .translator import Translator

    a = cfg["audio"]
    rec = AudioRecorder(a.get("device"), a.get("sample_rate", 16000), always_open=False)
    stt = make_stt(cfg)
    stt.load()
    llm = make_llm(cfg, args.model)
    llm.start()
    try:
        tr = Translator(llm, cfg["translation"])
        input(f"Enter = start nagrywania ({args.seconds}s)… ")
        x = rec.record_seconds(args.seconds)
        st = analyze(x, a["silence_rms_threshold"])
        print(f"nagranie {st.duration:.1f}s, mowa {st.speech_seconds:.1f}s, rms {st.rms:.4f}")
        clip = trim_and_normalize(x, a["silence_rms_threshold"])
        t0 = time.perf_counter()
        pl = stt.transcribe(clip)
        t1 = time.perf_counter()
        en = tr.translate(pl, "pl-en").text
        t2 = time.perf_counter()
        print(f"PL: {pl}\nEN: {en}\nSTT {1000 * (t1 - t0):.0f} ms | tłumaczenie {1000 * (t2 - t1):.0f} ms")
    finally:
        llm.stop()
    return 0


def cmd_bench(cfg: Config, args) -> int:
    from .translator import Translator

    data = json.loads((ROOT / "wvt" / "data" / "benchmark.json").read_text(encoding="utf-8"))
    report: dict = {"date": time.strftime("%Y-%m-%d %H:%M:%S"), "vram_start": _vram()}
    audio_dir = ROOT / "tests" / "audio"
    stt_texts: list[str] = []

    if not args.no_stt:
        stt = make_stt(cfg)
        stt.load()
        stt.warmup()
        report["stt"] = {"device": stt.device, "load_s": round(stt.load_seconds, 2), "vram_after_load": _vram()}
        print(f"== STT {stt.describe()} (ładowanie {stt.load_seconds:.1f}s)")
        rows = []
        for noisy in (False, True):
            lat, wers = [], []
            for i, item in enumerate(data["pl_to_en"], 1):
                wav = audio_dir / f"pl_{i:02d}.wav"
                if not wav.exists():
                    continue
                x = load_wav(wav)
                if noisy:
                    x = add_noise(x, 10.0, seed=i)
                t0 = time.perf_counter()
                hyp = stt.transcribe(x)
                ms = (time.perf_counter() - t0) * 1000
                e = wer(item["pl"], hyp)
                lat.append(ms)
                wers.append(e)
                if not noisy:
                    stt_texts.append(hyp)
                    rows.append({"ref": item["pl"], "hyp": hyp, "ms": round(ms), "wer": round(e, 3), "audio_s": round(len(x) / 16000, 2)})
                    print(f"  {ms:6.0f} ms  WER {e:4.2f}  {hyp}")
            key = "noisy_snr10" if noisy else "clean"
            report["stt"][key] = {
                "n": len(lat),
                "latency_ms_mean": round(statistics.mean(lat), 1) if lat else None,
                "latency_ms_p90": round(pct(lat, 0.9), 1),
                "wer_mean": round(statistics.mean(wers), 3) if wers else None,
            }
            print(f"  -> {key}: śr. {report['stt'][key]['latency_ms_mean']} ms, p90 {report['stt'][key]['latency_ms_p90']} ms, WER {report['stt'][key]['wer_mean']}")
        report["stt"]["utterances"] = rows

    if not args.no_llm:
        llm = make_llm(cfg, args.model)
        t0 = time.perf_counter()
        llm.start()
        report["llm"] = {"model": llm.model_name, "load_s": round(time.perf_counter() - t0, 2), "vram_after_load": _vram()}
        try:
            from .gpu import process_vram_mb

            report["llm"]["server_vram_mb"] = process_vram_mb(llm.pid) if llm.pid else None
            tr = Translator(llm, cfg["translation"])
            tr.translate("Cześć, rozgrzewka.", "pl-en")  # warm-up / prompt cache
            tr.translate("hello, warm up", "en-pl")
            print(f"== LLM {llm.model_name} (start {report['llm']['load_s']} s)")
            for direction, items, src_key, ref_key in (("pl-en", data["pl_to_en"], "pl", "en"), ("en-pl", data["en_to_pl"], "en", "pl")):
                lat, out_rows, tps = [], [], []
                for item in items:
                    tr.cache = type(tr.cache)(tr.cache.size)  # no cache hits in the benchmark
                    res = tr.translate(item[src_key], direction)
                    lat.append(res.seconds * 1000)
                    if res.info.get("predicted_per_second"):
                        tps.append(res.info["predicted_per_second"])
                    out_rows.append({"src": item[src_key], "out": res.text, "ref": item[ref_key], "ms": round(res.seconds * 1000),
                                     "reasoning_chars": res.info.get("reasoning_chars", 0)})
                    print(f"  {res.seconds * 1000:6.0f} ms  {item[src_key]}  =>  {res.text}")
                report["llm"][direction] = {
                    "latency_ms_mean": round(statistics.mean(lat), 1),
                    "latency_ms_p90": round(pct(lat, 0.9), 1),
                    "tokens_per_s_mean": round(statistics.mean(tps), 1) if tps else None,
                    "rows": out_rows,
                }
                print(f"  -> {direction}: śr. {report['llm'][direction]['latency_ms_mean']} ms, p90 {report['llm'][direction]['latency_ms_p90']} ms")
            if stt_texts:
                lat = []
                rows = []
                for hyp in stt_texts:
                    res = tr.translate(hyp, "pl-en")
                    lat.append(res.seconds * 1000)
                    rows.append({"stt": hyp, "out": res.text})
                report["llm"]["pl-en_from_stt"] = {"latency_ms_mean": round(statistics.mean(lat), 1), "rows": rows}
            report["vram_end"] = _vram()
        finally:
            llm.stop()

    out_dir = ROOT / "logs"
    out_dir.mkdir(exist_ok=True)
    tag = Path(args.model or cfg.get("llm.model_path")).stem if not args.no_llm else "stt"
    out = out_dir / f"bench_{tag}_{time.strftime('%Y%m%d_%H%M%S')}.json"
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print("Raport:", out)
    return 0


def install_addon(cfg: Config) -> Path:
    src = ROOT / "addon" / "WoWVoiceTranslator"
    dst_root = cfg.addon_dir
    if not dst_root.parent.parent.exists():
        raise FileNotFoundError(f"Nie znaleziono katalogu WoW: {dst_root.parent.parent}")
    dst = dst_root / "WoWVoiceTranslator"
    dst.mkdir(parents=True, exist_ok=True)
    for f in src.iterdir():
        if f.is_file():
            shutil.copy2(f, dst / f.name)
    return dst


def cmd_install_addon(cfg: Config, args) -> int:
    if getattr(args, "wow", None):
        root = wow_root_from(args.wow)
        flavor = cfg.get("wow.flavor_dir", "_forever_")
        if not (root / flavor).is_dir():
            print(f"W {root} nie ma folderu {flavor} – podaj folder, w którym jest {flavor} (WoW Forever).")
            return 1
        cfg.set("wow.install_dir", str(root).replace("\\", "/"))
        cfg.save()  # remembered for the app and later installs
    try:
        dst = install_addon(cfg)
    except FileNotFoundError as exc:
        print(exc)
        return 1
    print("Addon zainstalowany w:", dst)
    print("W grze: /reload (albo restart), a potem /wvt test")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m wvt.cli")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check")
    p = sub.add_parser("stt")
    p.add_argument("files", nargs="+")
    p = sub.add_parser("translate")
    p.add_argument("text")
    p.add_argument("--dir", default="pl-en", choices=["pl-en", "en-pl"])
    p.add_argument("--model")
    p = sub.add_parser("mic")
    p.add_argument("--seconds", type=float, default=5)
    p.add_argument("--model")
    p = sub.add_parser("bench")
    p.add_argument("--model")
    p.add_argument("--no-llm", action="store_true")
    p.add_argument("--no-stt", action="store_true")
    p = sub.add_parser("install-addon")
    p.add_argument("--wow", help="folder World of Warcraft (ten z _forever_), gdy nie wykryje się sam")
    args = ap.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # Polish text in a cp1250 console
        except (AttributeError, ValueError):
            pass

    cfg = Config.load()
    setup_logging(cfg, console=True)
    return {
        "check": cmd_check,
        "stt": cmd_stt,
        "translate": cmd_translate,
        "mic": cmd_mic,
        "bench": cmd_bench,
        "install-addon": cmd_install_addon,
    }[args.cmd](cfg, args)


if __name__ == "__main__":
    sys.exit(main())
