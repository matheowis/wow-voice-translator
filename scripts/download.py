"""Downloads (with resume) and verifies all model/runtime files.

    python scripts/download.py            # everything needed by the default config
    python scripts/download.py --verify   # only check sizes + SHA-256 of what is on disk
    python scripts/download.py --llm nemotron-nano-4b   # alternative LLM

Hashes come from the Hugging Face / GitHub APIs (LFS sha256 / release asset digest),
so a truncated or tampered file is detected.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import zipfile
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent

PARAKEET_REPO = "istupakov/parakeet-tdt-0.6b-v3-onnx"
PARAKEET_FILES = [
    "config.json", "vocab.txt", "nemo128.onnx", "decoder_joint-model.onnx", "encoder-model.onnx",
    "encoder-model.onnx.data", "decoder_joint-model.int8.onnx", "encoder-model.int8.onnx",
]
LLMS = {
    "nemotron-bolt-e4b": ("unsloth/NVIDIA-Nemotron-3.5-Bolt-E4B-GGUF", "NVIDIA-Nemotron-3.5-Bolt-E4B-UD-Q4_K_XL.gguf"),
    "nemotron-nano-4b": ("nvidia/NVIDIA-Nemotron-3-Nano-4B-GGUF", "NVIDIA-Nemotron3-Nano-4B-Q4_K_M.gguf"),
    "gemma4-e4b": ("google/gemma-4-E4B-it-qat-q4_0-gguf", "gemma-4-E4B_q4_0-it.gguf"),
}
# speech recognition (MIT): Whisper large-v3-turbo, fp16 for CUDA; Parakeet above is the CPU fallback
WHISPER_REPO = "onnx-community/whisper-large-v3-turbo"
WHISPER_REVISION = "360ebcde2559d60bb474678be3c1de9ef347d01a"  # the files measured on 2026-11-09
WHISPER_FILES = [
    "onnx/encoder_model_fp16.onnx", "onnx/decoder_model_merged_fp16.onnx", "added_tokens.json", "config.json",
    "generation_config.json", "merges.txt", "normalizer.json", "preprocessor_config.json", "special_tokens_map.json",
    "tokenizer.json", "tokenizer_config.json", "vocab.json",
]
EMBED_REPO = "Xenova/multilingual-e5-small"  # MIT; ONNX int8 export of intfloat/multilingual-e5-small
EMBED_FILES = ["onnx/model_quantized.onnx", "tokenizer.json", "config.json"]
LLAMA_CPP_TAG = "b12177"
LLAMA_CPP_ASSETS = [f"llama-{LLAMA_CPP_TAG}-bin-win-cuda-13.5-x64.zip", "cudart-llama-bin-win-cuda-13.5-x64.zip"]


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def hf_meta(repo: str, revision: str = "") -> dict[str, dict]:
    rev = f"/revision/{revision}" if revision else ""
    r = httpx.get(f"https://huggingface.co/api/models/{repo}{rev}?blobs=true", timeout=30, follow_redirects=True)
    r.raise_for_status()
    out = {}
    for s in r.json().get("siblings", []):
        lfs = s.get("lfs") or {}
        out[s["rfilename"]] = {"size": s.get("size"), "sha256": lfs.get("sha256")}
    return out


def github_assets(tag: str) -> dict[str, dict]:
    r = httpx.get(f"https://api.github.com/repos/ggml-org/llama.cpp/releases/tags/{tag}", timeout=30)
    r.raise_for_status()
    out = {}
    for a in r.json().get("assets", []):
        digest = a.get("digest") or ""
        out[a["name"]] = {"size": a["size"], "sha256": digest.split(":", 1)[1] if digest.startswith("sha256:") else None,
                          "url": a["browser_download_url"]}
    return out


def download(url: str, dest: Path, size: int | None) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    have = dest.stat().st_size if dest.exists() else 0
    if size and have == size:
        return
    headers = {"Range": f"bytes={have}-"} if have else {}
    with httpx.stream("GET", url, headers=headers, timeout=60, follow_redirects=True) as r:
        if r.status_code == 416:
            return
        r.raise_for_status()
        mode = "ab" if r.status_code == 206 else "wb"
        done = have if mode == "ab" else 0
        with open(dest, mode) as fh:
            for chunk in r.iter_bytes(1 << 20):
                fh.write(chunk)
                done += len(chunk)
                if size:
                    print(f"\r  {dest.name}: {done / 1e6:8.1f} / {size / 1e6:.1f} MB", end="", flush=True)
    print()


def check(dest: Path, meta: dict) -> bool:
    if not dest.exists():
        print(f"  BRAK   {dest}")
        return False
    if meta.get("size") and dest.stat().st_size != meta["size"]:
        print(f"  ROZMIAR {dest.name}: {dest.stat().st_size} != {meta['size']}")
        return False
    if meta.get("sha256"):
        ok = sha256(dest) == meta["sha256"]
        print(f"  {'OK    ' if ok else 'SHA!!!'} {dest.name}")
        return ok
    print(f"  OK(rozmiar) {dest.name}")
    return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--llm", default="nemotron-bolt-e4b", choices=sorted(LLMS))
    ap.add_argument("--verify", action="store_true")
    ap.add_argument("--skip-llama-cpp", action="store_true")
    ap.add_argument("--speaker-model", action="store_true",
                    help="WeSpeaker ResNet34 (26 MB) - only needed to design voices (python -m wvt.voice.design)")
    ap.add_argument("--embed-model", action="store_true",
                    help="only multilingual-e5-small (ONNX int8, ~120 MB) for the assistant's knowledge base, then stop")
    ap.add_argument("--skip-whisper", action="store_true",
                    help="no Whisper large-v3-turbo (1.6 GB); speech recognition then uses Parakeet")
    args = ap.parse_args()
    ok = True

    if args.embed_model:  # the knowledge base's embedding model (MIT): Polish questions, English guides
        print("multilingual-e5-small (ONNX int8, wektory bazy wiedzy):")
        meta = hf_meta(EMBED_REPO)
        for f in EMBED_FILES:
            dest = ROOT / "models" / "embed" / "multilingual-e5-small" / Path(f).name
            if not args.verify:
                download(f"https://huggingface.co/{EMBED_REPO}/resolve/main/{f}", dest, meta[f]["size"])
            ok &= check(dest, meta[f])
        return 0 if ok else 1

    if args.speaker_model:
        repo, fname = "Wespeaker/wespeaker-voxceleb-resnet34", "voxceleb_resnet34.onnx"
        print("WeSpeaker ResNet34 (podobieństwo głosów):")
        meta = hf_meta(repo)
        dest = ROOT / "models" / "speaker" / fname
        if not args.verify:
            download(f"https://huggingface.co/{repo}/resolve/main/{fname}", dest, meta[fname]["size"])
        ok &= check(dest, meta[fname])

    if not args.skip_whisper:
        print("Whisper large-v3-turbo (ONNX fp16, rozpoznawanie mowy):")
        meta = hf_meta(WHISPER_REPO, WHISPER_REVISION)
        for f in WHISPER_FILES:
            dest = ROOT / "models" / "whisper-large-v3-turbo-onnx" / f
            if not args.verify:
                download(f"https://huggingface.co/{WHISPER_REPO}/resolve/{WHISPER_REVISION}/{f}", dest,
                         meta[f]["size"])
            ok &= check(dest, meta[f])

    print("Parakeet TDT 0.6B v3 (ONNX, zapas na CPU):")
    meta = hf_meta(PARAKEET_REPO)
    for f in PARAKEET_FILES:
        dest = ROOT / "models" / "parakeet-tdt-0.6b-v3-onnx" / f
        if not args.verify:
            download(f"https://huggingface.co/{PARAKEET_REPO}/resolve/main/{f}", dest, meta[f]["size"])
        ok &= check(dest, meta[f])

    repo, fname = LLMS[args.llm]
    print(f"LLM {args.llm}:")
    meta = hf_meta(repo)
    dest = ROOT / "models" / "llm" / fname
    if not args.verify:
        download(f"https://huggingface.co/{repo}/resolve/main/{fname}", dest, meta[fname]["size"])
    ok &= check(dest, meta[fname])

    if not args.skip_llama_cpp:
        print(f"llama.cpp {LLAMA_CPP_TAG} (CUDA 13.5, Windows x64):")
        assets = github_assets(LLAMA_CPP_TAG)
        target = ROOT / "runtime" / "llama.cpp"
        for name in LLAMA_CPP_ASSETS:
            dest = ROOT / "runtime" / "dl" / name
            if not args.verify:
                download(assets[name]["url"], dest, assets[name]["size"])
            good = check(dest, assets[name])
            ok &= good
            if good and not args.verify:
                with zipfile.ZipFile(dest) as z:
                    z.extractall(target)
        print(f"  llama-server: {'OK' if (target / 'llama-server.exe').exists() else 'BRAK'}")

    print("WSZYSTKO OK" if ok else "SĄ PROBLEMY – uruchom ponownie (pobieranie wznawia się)")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
