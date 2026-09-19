"""Installs every model config/models.yaml needs, and reports what's missing.

    python scripts/install_models.py            # list what is installed / missing (downloads nothing)
    python scripts/install_models.py --yes      # download and register the missing ones

Two install routes, because Ollama 0.34 can no longer pull `hf.co/...` tags (Hugging Face moved to a
CDN host that Ollama blocks as a "redirect to a different host"):

  registry  `ollama pull <tag>` from the official Ollama library.
  gguf      download the GGUF (+ Ollama template/params) from Hugging Face with huggingface_hub,
            then `ollama create <tag>` — the same result `ollama pull` used to give.

hf.co tags are attempted with `ollama pull` first (it works again after an Ollama update); if that
fails the script says which library tag to use instead.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import requests
import yaml

ROOT = Path(__file__).resolve().parent.parent
OLLAMA = "http://localhost:11434"

# tag -> how to get it, download size (GB, from the model pages), what uses it
MANIFEST: dict[str, dict] = {
    "qwen3-embedding:0.6b": {"route": "registry", "gb": 0.64, "role": "embedder"},
    "qwen2.5:3b": {"route": "registry", "gb": 1.9, "role": "tool_caller, council"},
    "moondream:1.8b": {"route": "registry", "gb": 1.7, "role": "vision"},
    "qwen2.5:7b": {"route": "registry", "gb": 4.7, "role": "coder fallback (high tier only)"},
    "llava:7b": {"route": "registry", "gb": 4.7, "role": "vision fallback (high tier only)"},
    "qwen3-4b-instruct-2507:Q4_K_M": {
        "route": "gguf", "gb": 2.5, "role": "router stage 2, tarjuman",
        "repo": "unsloth/Qwen3-4B-Instruct-2507-GGUF", "file": "Qwen3-4B-Instruct-2507-Q4_K_M.gguf",
        "bytes": 2497281120,
    },
    "hf.co/Qwen/Qwen2.5-1.5B-Instruct-GGUF:Q4_K_M": {
        "route": "hf", "gb": 1.1, "role": "writer_general, researcher_rag", "library_alternative": "qwen2.5:1.5b"},
    "hf.co/Qwen/Qwen2.5-Coder-3B-Instruct-GGUF:Q4_K_M": {
        "route": "hf", "gb": 2.1, "role": "coder", "library_alternative": "qwen2.5-coder:3b"},
}


def required_tags(registry: dict | None = None) -> dict[str, list[str]]:
    """Every tag models.yaml references (including fallbacks), mapped to the roles using it."""
    registry = registry or yaml.safe_load((ROOT / "config" / "models.yaml").read_text(encoding="utf-8"))
    tags: dict[str, list[str]] = {}
    for role, cfg in registry.items():
        if role == "tiers":
            continue
        for key in ("ollama_tag", "fallback_tag"):
            if key in cfg:
                tags.setdefault(cfg[key], []).append(role)
    return tags


def installed() -> set[str]:
    r = requests.get(f"{OLLAMA}/api/tags", timeout=10)
    r.raise_for_status()
    return {m["name"] for m in r.json()["models"]}


def _run(cmd: list[str], cwd: Path | None = None) -> bool:
    return subprocess.run(cmd, cwd=cwd).returncode == 0


def install_gguf(tag: str, spec: dict) -> bool:
    from huggingface_hub import hf_hub_download

    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        gguf = Path(hf_hub_download(spec["repo"], spec["file"], local_dir=work))
        if spec.get("bytes") and gguf.stat().st_size != spec["bytes"]:
            print(f"  size mismatch for {spec['file']}: got {gguf.stat().st_size}, expected {spec['bytes']}")
            return False
        template = Path(hf_hub_download(spec["repo"], "template", local_dir=work)).read_text(encoding="utf-8")
        params = json.loads(Path(hf_hub_download(spec["repo"], "params", local_dir=work)).read_text(encoding="utf-8"))
        lines = [f"FROM ./{gguf.name}", f'TEMPLATE """{template}"""']
        lines += [f'PARAMETER stop "{s}"' for s in params.get("stop", [])]
        lines += [f"PARAMETER {k} {params[k]}" for k in ("top_k", "top_p", "min_p", "repeat_penalty") if k in params]
        (work / "Modelfile").write_text("\n".join(lines) + "\n", encoding="utf-8")
        return _run(["ollama", "create", tag, "-f", "Modelfile"], cwd=work)


def install(tag: str, spec: dict) -> bool:
    if spec["route"] == "gguf":
        return install_gguf(tag, spec)
    if _run(["ollama", "pull", tag]):
        return True
    if spec["route"] == "hf":
        print(f"  `ollama pull {tag}` failed (Ollama can't reach hf.co right now). Either update Ollama and retry, or "
              f"use `ollama pull {spec['library_alternative']}` and change that role's ollama_tag in config/models.yaml.")
    return False


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--yes", action="store_true", help="download and register the missing models")
    args = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if not shutil.which("ollama"):
        print("ollama isn't on PATH — install it from https://ollama.com/download first.")
        return 1

    have = installed()
    needed = required_tags()
    missing = []
    for tag, roles in needed.items():
        spec = MANIFEST.get(tag)
        state = "installed" if tag in have else "MISSING"
        size = f"{spec['gb']} GB" if spec else "?"
        print(f"  [{state:9}] {tag}  ({size})  <- {', '.join(roles)}")
        if tag not in have:
            missing.append(tag)
    unknown = [t for t in missing if t not in MANIFEST]
    if unknown:
        print(f"\nNot in the manifest (pull manually): {unknown}")
    todo = [t for t in missing if t in MANIFEST]
    if not todo:
        print("\nEverything models.yaml needs is installed." if not unknown else "")
        return 1 if unknown else 0
    total = sum(MANIFEST[t]["gb"] for t in todo)
    if not args.yes:
        print(f"\n{len(todo)} model(s) missing, about {total:.1f} GB to download. Re-run with --yes to install.")
        return 1
    failed = [t for t in todo if not install(t, MANIFEST[t])]
    print("\nfailed: " + ", ".join(failed) if failed else "\nall installed.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
